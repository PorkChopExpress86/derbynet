"""Cheap checks over the shell scripts in `testing/` (#37, #64 C44 and C45).

The dev and demo scripts there run against PHP and Django but gate nothing, so
they rotted unnoticed: `simulate-replay.sh` posted `register-replay`,
`demo-multi.sh` posted `select-heat`, `polling-loop.sh` ran a `query.sh` that
was never there, and `testing/query` called a `curl_get` that `common.sh` no
longer defined. These read the scripts instead of running them.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from legacy_api.handlers.testing import MANAGED
from legacy_api.registry import DEFERRED, REGISTRY
from legacy_api.tests.test_registry import php_endpoints

TESTING = Path(__file__).resolve().parents[3] / "testing"
# The `.sh` scripts, and the helpers with no suffix such as `queryj`.
SCRIPTS = sorted(
    path
    for path in TESTING.iterdir()
    if path.is_file()
    and (
        path.suffix == ".sh"
        or (not path.suffix and path.read_bytes().startswith(b"#!"))
    )
)
COMMON = TESTING / "common.sh"
# `action=role.login` in a curl argument, `-F action=snapshot.put` in a form.
# A name made of a shell variable is not checked.
ENDPOINT = re.compile(r"\b(action|query)=([A-Za-z0-9._-]+)")
# A script another script runs, as `dirname $0`/x.sh or $(dirname "$0")/x.sh.
CALLED = re.compile(
    r"""(?:`dirname \$0`|\$\(dirname "?\$0"?\))/([A-Za-z0-9._-]+\.sh)"""
)
# A curl helper from common.sh, as called or as defined there.
CURL_HELPER = re.compile(r"\bcurl_[a-z_]+\b")
DEFINED = re.compile(r"^\s*(?:function\s+)?(curl_[a-z_]+)\s*(?:\(\))?\s*\{?\s*$")


def code_lines(path: Path) -> list[str]:
    """The lines of a script that are not comments."""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def endpoints_named(path: Path) -> set[tuple[str, str]]:
    return {
        (found[1], found[2])
        for line in code_lines(path)
        for found in ENDPOINT.finditer(line)
    }


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_every_endpoint_a_script_calls_is_one_php_has_and_django_covers(script):
    """Django serves it (or a backlog ticket defers it, or it is a test
    endpoint), and PHP has it, so the script runs against either server."""
    django = {(e.kind, e.name) for e in REGISTRY}
    django |= {(d.kind, d.name) for d in DEFERRED} | set(MANAGED)

    named = endpoints_named(script)

    assert named - django == set(), "Django has no such endpoint"
    assert named - php_endpoints() == set(), "PHP has no such endpoint"


def test_a_name_no_server_has_is_found(tmp_path):
    script = tmp_path / "x.sh"
    script.write_text(
        '# curl_post action.php "action=commented-out"\n'
        'curl_post action.php "action=select-heat&roundid=$R" | check_success\n'
        "curl -F action=snapshot.put -F snapshot=@x $URL\n"
        'curl_getj "action.php?query=poll.coordinator"\n'
    )

    assert endpoints_named(script) == {
        ("action", "select-heat"),
        ("action", "snapshot.put"),
        ("query", "poll.coordinator"),
    }


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_every_script_a_script_runs_is_there(script):
    called = {
        found[1] for line in code_lines(script) for found in CALLED.finditer(line)
    }

    assert {name for name in called if not (TESTING / name).is_file()} == set()


def curl_helpers_defined() -> set[str]:
    return {found[1] for line in code_lines(COMMON) if (found := DEFINED.match(line))}


def test_the_scan_covers_the_helpers_with_no_suffix():
    assert {"queryj", "actionj"} <= {path.name for path in SCRIPTS}


def test_a_definition_in_common_sh_is_found():
    assert {"curl_getj", "curl_postj", "curl_get_xml"} <= curl_helpers_defined()


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_every_curl_helper_a_script_calls_is_defined_in_common_sh(script):
    called = {
        found[0] for line in code_lines(script) for found in CURL_HELPER.finditer(line)
    }

    assert called - curl_helpers_defined() == set(), "common.sh has no such function"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_every_script_parses(script):
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
