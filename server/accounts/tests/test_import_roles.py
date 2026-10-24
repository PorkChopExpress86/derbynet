"""`import_roles` against PHP. `data/capture-php.sh` wrote each `.json` with
PHP's own `json_encode()` of the `.inc` beside it."""

import json
import os
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from accounts.config_roles import ConfigRolesError, parse
from accounts.models import Role

DATA = Path(__file__).parent / "data"
INPUTS = ["default", "postinst", "hand-edited"]


def php(name):
    return json.loads((DATA / f"config-roles-{name}.json").read_text())


@pytest.mark.parametrize("name", INPUTS)
def test_the_parse_matches_phps_json_encode(name):
    roles, post_setup_role = parse((DATA / f"config-roles-{name}.inc").read_text())

    expected = php(name)
    assert json.dumps(roles) == json.dumps(expected["roles"])
    assert post_setup_role == expected["post_setup_role"]


def import_roles(path, *args):
    out = StringIO()
    call_command("import_roles", str(path), *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize("name", INPUTS)
def test_the_import_writes_phps_roles_in_file_order(name):
    import_roles(DATA / f"config-roles-{name}.inc")

    expected = php(name)
    roles = list(Role.objects.all())
    assert [r.name for r in roles] == list(expected["roles"])
    for role in roles:
        details = expected["roles"][role.name]
        assert role.permissions == details["permissions"]
        assert role.interactive == details.get("interactive", True)
        assert role.check_password(details["password"])
        assert role.password_hash != details["password"] or not details["password"]
        assert role.post_setup == (role.name == expected["post_setup_role"])


@pytest.mark.django_db
def test_a_dry_run_prints_the_masks_and_writes_nothing():
    before = list(Role.objects.values_list("name", "permissions", "password_hash"))

    out = import_roles(DATA / "config-roles-hand-edited.inc", "--dry-run")

    assert "'Judge'" in out and "258" in out
    assert "n3w-pass" not in out
    assert list(Role.objects.values_list("name", "permissions", "password_hash")) == (
        before
    )


@pytest.mark.django_db
def test_importing_twice_gives_the_same_roles():
    path = DATA / "config-roles-hand-edited.inc"
    import_roles(path)
    first = list(Role.objects.values_list("name", "permissions", "position"))

    import_roles(path)

    assert list(Role.objects.values_list("name", "permissions", "position")) == first


UNREADABLE = """<?php
$base = 1;
$roles = array('' => array('password' => '', 'permissions' => $base));
$post_setup_role = '';
"""


@pytest.mark.parametrize(
    "text",
    [
        UNREADABLE,
        # PHP reads 0100 as octal 64.
        "<?php $roles = array('X' => array('password' => '', 'permissions' => 0100));",
        """<?php $roles = array('X' => array('password' => "p\\x41"));""",
        "<?php $roles = array('X' => array('', 1));",
    ],
    ids=["variable", "octal", "hex-escape", "keyless"],
)
def test_the_parser_refuses_what_it_cannot_read(text):
    with pytest.raises(ConfigRolesError):
        parse(text)


@pytest.mark.django_db
def test_a_php_failure_is_a_command_error(tmp_path, monkeypatch):
    path = tmp_path / "config-roles.inc"
    path.write_text(UNREADABLE)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php").write_text("#!/bin/sh\nexit 255\n")
    (bin_dir / "php").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(CommandError, match="cannot read"):
        import_roles(path)
    assert Role.objects.count() == 5


@pytest.mark.django_db
def test_without_php_an_unreadable_file_fails(tmp_path, monkeypatch):
    path = tmp_path / "config-roles.inc"
    path.write_text(UNREADABLE)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(CommandError, match="cannot read"):
        import_roles(path)
    assert Role.objects.count() == 5


@pytest.mark.django_db
def test_an_unreadable_file_falls_back_to_php(tmp_path, monkeypatch):
    path = tmp_path / "config-roles.inc"
    path.write_text(UNREADABLE)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_php = bin_dir / "php"
    php_json = '{"roles":{"":{"password":"","permissions":1}},"post_setup_role":""}'
    fake_php.write_text(f"#!/bin/sh\necho '{php_json}'\n")
    fake_php.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    import_roles(path)

    assert list(Role.objects.values_list("name", "permissions", "post_setup")) == [
        ("", 1, True)
    ]


@pytest.mark.django_db
def test_a_post_setup_role_that_names_no_role_fails(tmp_path):
    path = tmp_path / "config-roles.inc"
    path.write_text(
        "<?php $roles = array('' => array('password' => '', 'permissions' => 1));"
        " $post_setup_role = 'Nobody'; ?>"
    )

    with pytest.raises(CommandError, match="Nobody"):
        import_roles(path)
    assert Role.objects.count() == 5


def install_php(tmp_path, monkeypatch, script):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php").write_text(f"#!/bin/sh\n{script}\n")
    (bin_dir / "php").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


ROLES_JSON = '{"roles":{"":{"password":"","permissions":1}},"post_setup_role":""}'


@pytest.mark.django_db
def test_php_gets_an_absolute_path_after_the_options(tmp_path, monkeypatch):
    """A relative path would be looked up in PHP's include_path first, and a
    name starting with `-` would read as an option."""
    (tmp_path / "config-roles.inc").write_text(UNREADABLE)
    argv = tmp_path / "argv"
    install_php(
        tmp_path, monkeypatch, f"printf '%s\\n' \"$@\" > {argv}\necho '{ROLES_JSON}'"
    )
    monkeypatch.chdir(tmp_path)

    import_roles(Path("config-roles.inc"))

    assert argv.read_text().splitlines()[-2:] == [
        "--",
        str(tmp_path / "config-roles.inc"),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "script",
    [
        # What `echo json_encode(...)` prints when the encoding fails.
        "printf ''",
        f"echo 'Notice: Undefined variable $x'; echo '{ROLES_JSON}'",
        "echo '{\"roles\": '",
        "echo '[]'",
        'echo \'{"roles": [], "post_setup_role": null}\'',
    ],
    ids=["nothing", "stray-output", "truncated", "not-an-object", "roles-not-a-map"],
)
def test_output_php_cannot_have_meant_is_a_command_error(tmp_path, monkeypatch, script):
    path = tmp_path / "config-roles.inc"
    path.write_text(UNREADABLE)
    install_php(tmp_path, monkeypatch, script)

    with pytest.raises(CommandError, match="php printed"):
        import_roles(path)
    assert Role.objects.count() == 5


@pytest.mark.django_db
def test_a_php_error_message_reaches_the_operator(tmp_path, monkeypatch):
    path = tmp_path / "config-roles.inc"
    path.write_text(UNREADABLE)
    install_php(tmp_path, monkeypatch, "echo 'Malformed UTF-8 characters' >&2; exit 1")

    with pytest.raises(CommandError, match="Malformed UTF-8 characters"):
        import_roles(path)


def test_the_help_says_php_runs_the_file(capsys):
    with pytest.raises(SystemExit):
        call_command("import_roles", "--help")

    assert "executes the file as PHP" in " ".join(capsys.readouterr().out.split())


@pytest.mark.django_db
def test_a_file_that_is_not_utf8_is_a_command_error(tmp_path, monkeypatch):
    path = tmp_path / "config-roles.inc"
    path.write_bytes(b"<?php $roles = array('X' => array('password' => '\xff'));")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(CommandError, match="cannot read"):
        import_roles(path)
