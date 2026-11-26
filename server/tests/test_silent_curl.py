"""Under `set -e`, no variable takes a `curl -s` reply that hides curl's error.

`-s` silences curl's error message too, so when the server is down,
`REPLY=$(curl -s "$url")` ends a `set -e` script with curl's exit status (7,
28) and no word on stderr. #537's verify seed exited 7 that way, and the
baseline seed had the same flaw (#537 retro). In a `while` or `if` condition
the failure ends the loop or takes the branch, as #537's poll loop did, and
nothing says why.

Either form names the failure:

    reply="$(curl -sS "$url")"                      # -S prints curl's error
    reply="$(curl -s "$url")" || fail "no answer"   # or the script says it

`-v` prints it too, and a curl behind `docker exec`, `timeout` or `command`
counts as one at the start. A curl after a `(` inside the substitution, as in
a nested `$(...)` or `sh -c "(curl ...)"`, is left. A substitution with a pipe
in it counts only under pipefail, since otherwise its status is its last
command's. A workflow's `run:` step is left: its log prints the command and
its exit code.
"""

import re
import subprocess
from pathlib import Path

from tests.file_kinds import shell_files
from tests.test_pipefail_pipes import logical_lines

ROOT = Path(__file__).resolve().parents[2]
SET_E = re.compile(r"^\s*set\s+(?:-\w+\s+)*-[a-zA-Z]*e", re.M)
PIPEFAIL = re.compile(r"^\s*set\s+(?:-\w+\s+)*-\w*o\s+pipefail\b", re.M)
# `name=$(curl` or `name="$(docker exec "$c" curl`, anywhere on the line, and
# what follows curl.  A curl after a `(` inside the substitution is left, as in
# a nested `$(...)`, whose status is lost.
CAPTURE = re.compile(r"""(?:^|[\s;&|!(])\w+=["']?\$\([^()]*?\bcurl\s(?P<rest>.*)""")
SILENT = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*s[a-zA-Z]*|--silent)(?=\s|$)")
SHOWS = re.compile(
    r"(?:^|\s)(?:-[a-zA-Z]*[Sv][a-zA-Z]*|--show-error|--verbose)(?=\s|$)"
)
EXEMPT = {
    "server/tests/golden/capture/capture-http.sh": (
        "a golden input (tools/golden_inputs.py): an edit means a 25-minute recapture"
    ),
}


def silent_captures(text: str) -> list[tuple[int, str]]:
    if not SET_E.search(text):
        return []
    pipefail = bool(PIPEFAIL.search(text))
    found = []
    for n, line in logical_lines(text):
        for match in CAPTURE.finditer(line):
            rest = match["rest"]
            # curl's own words: up to the substitution's end or a pipe.
            args = re.split(r"\)|(?<!\|)\|(?!\|)", rest)[0]
            piped = re.match(r"[^)]*(?<!\|)\|(?!\|)", rest)
            if piped and not pipefail:
                continue
            if not SILENT.search(args) or SHOWS.search(args):
                continue
            if "||" in rest:
                continue
            found.append((n, line.strip()))
    return found


def offenders(exempt=EXEMPT) -> list[str]:
    out = []
    for path in shell_files():
        name = str(path.relative_to(ROOT))
        if name in exempt:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out += [f"{name}:{n}: {line}" for n, line in silent_captures(text)]
    return out


def test_each_exempt_file_still_has_such_a_capture():
    """An exemption goes once its file has no such capture left."""
    found = {line.split(":", 1)[0] for line in offenders(exempt={})}
    assert set(EXEMPT) <= found, set(EXEMPT) - found


def test_no_set_e_script_captures_a_silent_curl():
    found = offenders()
    assert found == [], (
        "under set -e these capture `curl -s`, which exits with no message "
        "when the server is down; use -sS, or end the line with "
        '|| fail "..." (#537 retro):\n' + "\n".join(found)
    )


def test_the_check_finds_each_form():
    text = """set -euo pipefail
REPLY=$(curl -s "$url")
code="$(curl -m 120 -s -o /dev/null -w '%{http_code}' \\
    "$url")"
racer="$(curl -sf -b "$jar" "$url" | jq -r .id)"
if poll=$(curl -s "$url") && jq -e .ok <<<"$poll"; then :; fi
local page; page="$(curl --silent "$url")"
"""
    assert [n for n, _ in silent_captures(text)] == [2, 3, 5, 6, 7]


def test_the_check_finds_a_curl_behind_a_wrapper():
    """docker exec, timeout and command pass curl's status and stderr on."""
    text = """set -e
code="$(docker exec "$name" curl -m 120 -s -o /dev/null "$url")"
reply=$(timeout 5 curl -s "$url")
reply=$(command curl --silent "$url")
"""
    assert [n for n, _ in silent_captures(text)] == [2, 3, 4]


def test_the_check_leaves_the_safe_forms():
    text = """set -euo pipefail
REPLY=$(curl -sS "$@") || fail "$what: no answer"
code=$(curl -s -o /dev/null "$url") || true
page="$(curl -s -S "$url")"
page="$(curl --show-error -s "$url")"
page="$(curl -sv "$url")"
page="$(curl -m 5 "$url")"
grep -q success <<<"$(curl -sf "$url")"
echo "racers $(curl -s "$url" | jq length)"
code="$(docker exec "$name" curl -m 120 -sS "$url")"
bin=$(command -v curl)
page=$(jq -r .x <<<"$(curl -s "$url")")
"""
    assert silent_captures(text) == []


def test_a_pipe_counts_only_under_pipefail():
    line = 'racer="$(curl -s "$url" | jq -r .id)"\n'
    assert silent_captures("set -e\n" + line) == []
    assert [n for n, _ in silent_captures("set -e -o pipefail\n" + line)] == [2]
    assert silent_captures("set -o pipefail\n" + line) == []
    unpiped = 'reply="$(curl -s "$url")"\n'
    assert [n for n, _ in silent_captures("set -e\n" + unpiped)] == [2]


def test_a_silent_curl_ends_a_set_e_script_with_no_message(tmp_path):
    """Port 9 on 127.0.0.1 refuses the connection: curl exits 7. With -s the
    script prints nothing; with -sS curl names the failure."""
    for flags, says in (("-s", False), ("-sS", True)):
        script = f"set -e\nREPLY=$(curl {flags} -m 5 http://127.0.0.1:9/)\necho reached"
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert out.returncode == 7
        assert out.stdout == ""
        assert bool(out.stderr.strip()) == says, (flags, out.stderr)
