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


# The helpers that write their path argument alone as the request line of
# output.curl (`echo $1 >> $OUTPUT_CURL`); the others write `post`, `photo`,
# `login` or `snapshot.put` before it, which `_REQUEST` always parses.
BARE_REQUEST = re.compile(r"^\s*echo \$1 >> \$OUTPUT_CURL", re.MULTILINE)
FUNCTION = re.compile(r"^function (\w+)\(\) \{\n(.*?)^\}", re.MULTILINE | re.DOTALL)


def bare_request_helpers() -> set[str]:
    text = COMMON.read_text(encoding="utf-8")
    return {m[1] for m in FUNCTION.finditer(text) if BARE_REQUEST.search(m[2])}


def static_word(line: str, start: int) -> tuple[str, bool]:
    """The shell word at `start`, its quotes dropped and each expansion
    (`$x`, `${x}`, `$(...)`) as `x`; and whether any of it was literal."""
    out, literal, i, quote = [], False, start, None
    while i < len(line):
        c = line[i]
        if c == "$" and line[i + 1 : i + 2] == "(":
            depth, i = 1, i + 2
            while i < len(line) and depth:
                depth += {"(": 1, ")": -1}.get(line[i], 0)
                i += 1
            out.append("x")
            continue
        if c == "$":
            found = re.match(r"\$(\{[^}]*\}|\w+)", line[i:])
            if found:
                out.append("x")
                i += len(found[0])
                continue
        if quote:
            if c == quote:
                quote = None
            else:
                out.append(c)
                literal = True
        elif c in "\"'":
            quote = c
        elif c in " \t|;&)":
            break
        else:
            out.append(c)
            literal = True
        i += 1
    return "".join(out), literal


def unparsed_requests(path: Path) -> list[str]:
    """Each call of a bare-request helper whose path `_REQUEST` would not
    read as a request."""
    from tools.transcript_diff import _REQUEST

    call = re.compile(rf"(?<![\w-])({'|'.join(sorted(bare_request_helpers()))})\s+")
    found = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in call.finditer(line):
            word, literal = static_word(line, m.end())
            if literal and not _REQUEST.fullmatch(word):
                found.append(f"{path.name}:{n}: {m[1]} {word}")
    return found


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_every_request_line_a_script_records_is_one_the_gate_parses(script):
    """`tools/transcript_diff.py` splits output.curl at the lines `_REQUEST`
    matches. #206's `curl_text "checkin/racer/<id>/voting-code/"`, a page
    only Django has, was no request to it, so the page's body joined the
    award.edit before it and the parity gate failed on both databases (run
    43345570671). Read a Django-only page with `lookup_get`, which records
    nothing, or teach `_REQUEST` the path."""
    assert unparsed_requests(script) == []


def test_a_request_line_the_gate_cannot_parse_is_found(tmp_path):
    script = tmp_path / "x.sh"
    script.write_text(
        # The line #206 fixed with lookup_get.
        'curl_text "checkin/racer/$(racerid_of $1)/voting-code/" | \\\n'
        '# curl_text "commented/out/"\n'
        'curl_page "kiosk.php?page=a&p=$(jq -rn --arg v "$X" \'$v\')" | x\n'
        'curl_page_missing "$PAGE"\n'
        "curl_getj action.php?query=poll.coordinator\n"
        'lookup_get "checkin/racer/$ID/voting-code/"\n'
    )

    assert {"curl_text", "curl_getj", "curl_page_missing"} <= bare_request_helpers()
    assert unparsed_requests(script) == [
        "x.sh:1: curl_text checkin/racer/x/voting-code/"
    ]
