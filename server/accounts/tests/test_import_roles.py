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


def test_the_parser_refuses_what_it_cannot_read():
    with pytest.raises(ConfigRolesError):
        parse(UNREADABLE)


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
