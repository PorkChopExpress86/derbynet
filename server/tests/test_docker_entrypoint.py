"""The Docker entrypoint's choice of derbynet's uid and gid, and its takeover
line (D106, #350).

`server/docker/entrypoint.sh` defines `derbynet_ids` and `takeover` as shell
functions, so these tests run them in bash, under the entrypoint's own shell
options, with no Docker. checks.sh's bind-mount, uid-env, uid-root and
uid-takeover checks run the same rules in the built image.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "server" / "docker" / "entrypoint.sh"
# The image's own ids in these tests, as /opt/derbynet/derbynet-ids holds
# them.
IMAGE_IDS = ("999", "998")


def shell_function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{.*?^\}}$", ENTRYPOINT.read_text(), re.M | re.S)
    assert match, f"entrypoint.sh defines no {name}()"
    return match.group()


def run(name: str, args: list[str], env: dict[str, str] | None = None):
    """`name args...` under the entrypoint's shell options, with its log()
    and die(), and only the given DERBYNET_UID and DERBYNET_GID."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DERBYNET_UID", "DERBYNET_GID")
    }
    environment.update(env or {})
    log_die = "\n".join(
        line
        for line in ENTRYPOINT.read_text().splitlines()
        if line.startswith(("log() ", "die() "))
    )
    return subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail\n{log_die}\n{shell_function(name)}\n{name} "$@"',
            "bash",
            *args,
        ],
        env=environment,
        capture_output=True,
        text=True,
    )


def ids(owner: tuple[str, str], env: dict[str, str] | None = None) -> str:
    done = run("derbynet_ids", [*owner, *IMAGE_IDS], env)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    return done.stdout.strip()


def refusal(env: dict[str, str]) -> str:
    done = run("derbynet_ids", ["4321", "4321", *IMAGE_IDS], env)
    assert done.returncode == 1
    assert done.stdout == ""
    return done.stderr


def test_the_variables_win_over_the_folder_owner():
    assert ids(("4321", "4322"), {"DERBYNET_UID": "2345", "DERBYNET_GID": "2346"}) == (
        "2345 2346"
    )


@pytest.mark.parametrize("unset", [{}, {"DERBYNET_UID": "", "DERBYNET_GID": ""}])
def test_with_both_unset_a_non_root_owner_is_taken(unset):
    """Empty counts as unset, as compose.yaml passes a line .env lacks."""
    assert ids(("4321", "4322"), unset) == "4321 4322"


def test_a_root_owned_folder_keeps_the_image_ids():
    assert ids(("0", "0")) == "999 998"
    assert ids(("0", "4322")) == "999 998"


def test_an_owner_in_roots_group_keeps_the_image_gid():
    """Owner question, pending (#350): a folder of uid 4321 in gid 0 takes
    the uid alone."""
    assert ids(("4321", "0")) == "4321 998"


@pytest.mark.parametrize(
    ("uid", "gid", "expected"),
    [
        ("1", "1", "1 1"),
        ("33", "100", "33 100"),
        ("4294967294", "4294967294", "4294967294 4294967294"),
        ("0100", "0033", "100 33"),
    ],
)
def test_ids_in_the_range_pass_as_numbers(uid, gid, expected):
    """The range's ends, and a leading zero, which usermod gets without."""
    assert ids(("0", "0"), {"DERBYNET_UID": uid, "DERBYNET_GID": gid}) == expected


ROOT_LINE = (
    "derbynet: {}={} is root's id, and the image refuses to run the server as "
    "root. Set it to your own user's, as 'id -u' and 'id -g' print them.\n"
)
RANGE_LINE = (
    "derbynet: {} must be a whole number from 1 to 4294967294, as 'id -u' and "
    "'id -g' print them, not '{}'\n"
)


@pytest.mark.parametrize(
    ("uid", "gid", "line"),
    [
        ("0", "1000", ROOT_LINE.format("DERBYNET_UID", "0")),
        ("1000", "0", ROOT_LINE.format("DERBYNET_GID", "0")),
        ("00", "1000", ROOT_LINE.format("DERBYNET_UID", "00")),
        ("0.5", "1000", RANGE_LINE.format("DERBYNET_UID", "0.5")),
        ("1.5", "1000", RANGE_LINE.format("DERBYNET_UID", "1.5")),
        ("-1", "1000", RANGE_LINE.format("DERBYNET_UID", "-1")),
        ("1000", "4294967295", RANGE_LINE.format("DERBYNET_GID", "4294967295")),
        ("4294967294.5", "1", RANGE_LINE.format("DERBYNET_UID", "4294967294.5")),
        ("1000", "99999999999", RANGE_LINE.format("DERBYNET_GID", "99999999999")),
        ("abc", "1000", RANGE_LINE.format("DERBYNET_UID", "abc")),
        # Past bash's arithmetic, which would wrap it to a small id.
        (
            "18446744073709552616",
            "1000",
            RANGE_LINE.format("DERBYNET_UID", "18446744073709552616"),
        ),
    ],
)
def test_root_and_malformed_ids_stop_with_one_line(uid, gid, line):
    assert refusal({"DERBYNET_UID": uid, "DERBYNET_GID": gid}) == line


@pytest.mark.parametrize(
    ("env", "line"),
    [
        (
            {"DERBYNET_UID": "1000"},
            "derbynet: DERBYNET_UID is set and DERBYNET_GID is not. Set both, as "
            "'id -u' and 'id -g' print them, or neither.\n",
        ),
        (
            {"DERBYNET_UID": "1000", "DERBYNET_GID": ""},
            "derbynet: DERBYNET_UID is set and DERBYNET_GID is not. Set both, as "
            "'id -u' and 'id -g' print them, or neither.\n",
        ),
        (
            {"DERBYNET_GID": "1000"},
            "derbynet: DERBYNET_GID is set and DERBYNET_UID is not. Set both, as "
            "'id -u' and 'id -g' print them, or neither.\n",
        ),
    ],
)
def test_one_without_the_other_stops_with_one_line(env, line):
    """Owner question, pending (#350)."""
    assert refusal(env) == line


# ----- takeover ---------------------------------------------------------------

TAKEOVER = (
    "derbynet: the files under {} belong to uid {} (gid {}); giving them to "
    "uid {} (gid {}), once, which takes longer the more photos the folder "
    "holds\n"
)


def takeover(folder: Path, *ids: str) -> str:
    done = run("takeover", [*ids, str(folder)])
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    return done.stderr


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "secret_key").write_text("x")
    return tmp_path


def test_a_new_uid_logs_one_line_naming_both(folder):
    assert takeover(folder, "999", "998", "2345", "2346") == TAKEOVER.format(
        folder, 999, 998, 2345, 2346
    )


def test_a_new_gid_alone_logs_the_line(folder):
    assert takeover(folder, "2345", "998", "2345", "2346") == TAKEOVER.format(
        folder, 2345, 998, 2345, 2346
    )


def test_a_folder_already_derbynets_logs_nothing(folder):
    assert takeover(folder, "2345", "2346", "2345", "2346") == ""


def test_an_empty_folder_logs_nothing(tmp_path):
    """A new volume, root's and empty (owner question, pending (#350))."""
    assert takeover(tmp_path, "0", "0", "2345", "2346") == ""
    (tmp_path / ".hidden").write_text("x")
    assert takeover(tmp_path, "0", "0", "2345", "2346") == TAKEOVER.format(
        tmp_path, 0, 0, 2345, 2346
    )
