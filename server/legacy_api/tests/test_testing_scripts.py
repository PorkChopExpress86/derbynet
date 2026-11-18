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


# The scripts CI runs against Django: each `bash testing/<name>` in a
# workflow, and each script one of those runs, as `dirname $0`/x.sh or as a
# step of exercise-one-database.sh (`step --resets x.sh`, testing/steps.sh).
WORKFLOWS = TESTING.parent / ".github" / "workflows"
CI_RUN = re.compile(r"^[^#\n]*\bbash testing/([A-Za-z0-9._-]+)", re.MULTILINE)
STEP = re.compile(r"\bstep\s+(?:--resets\s+)?([A-Za-z0-9._-]+\.sh)\b")


def ci_script_names(workflows: Path, testing: Path) -> set[str]:
    names = {
        found[1]
        for workflow in sorted(workflows.glob("*.yml"))
        for found in CI_RUN.finditer(workflow.read_text(encoding="utf-8"))
    }
    todo = sorted(names)
    while todo:
        called = {
            found[1]
            for line in code_lines(testing / todo.pop())
            for found in [*CALLED.finditer(line), *STEP.finditer(line)]
        }
        todo += sorted(called - names)
        names |= called
    return names


def ci_scripts() -> list[Path]:
    names = ci_script_names(WORKFLOWS, TESTING)
    return [path for path in SCRIPTS if path.name in names]


# A variable named for a bound: a length in seconds or milliseconds, a
# timeout, a delay, or how long before something happens (DUMP_AFTER).
BOUND_NAME = r"\w*(?:TIMEOUT\w*|SECONDS|_MS|AFTER|DELAY)"
# A bound set to a number, or to a default its caller may override, in
# quotes or not, after export, local or readonly: `NAME=30`,
# `NAME=${NAME:-30}`, `NAME=${NAME-30}`, `: ${NAME:=30}`.
BOUND = re.compile(
    rf"""^\s*(?:(?:export|local|readonly)\s+)?
    (?:
      (?P<name>{BOUND_NAME})=(?P<q>"?)
        (?:[0-9.]+|\$\{{(?P=name):?-(?P<default>[^}}]+)\}})(?P=q)
    | :\s+(?P<q2>"?)\$\{{(?P<name2>{BOUND_NAME}):?=(?P<default2>[^}}]+)\}}(?P=q2)
    )""",
    re.IGNORECASE | re.VERBOSE,
)
# Where a shell command starts: a line, or after ; & | ( { ` ! or a keyword.
AT_COMMAND = r"(?:^|[;&|({`!]|\b(?:do|then|else|if|while|until|exec|time)\s)\s*"
# A wait: sleep, timeout or read -t as a command, or curl's --max-time,
# --connect-timeout or -m; its length a number, a variable ("$N", ${N:-3})
# or a command's output ($(...)).
WAIT = re.compile(
    rf"(?:{AT_COMMAND}(?:sleep\s+|timeout\s+(?:-\S+\s+)*|read\s+(?:-\S+\s+)*?-t\s*)"
    r"|\s--(?:max-time|connect-timeout)[\s=]+|\bcurl\b[^;|&]*?\s-m\s*)"
    r'(?P<length>"?(?:\$\{?\w+|\$\(|[0-9.]))'
)
QUOTED = re.compile(r""""[^"]*"|'[^']*'""")
EXPANSION = re.compile(r'"\$\{?\w+[^"\s]*"')
# A source: a measurement with its unit ("the 5 s a heat takes", "250 ms"),
# a ticket, an owner decision, or a file ("playlist.inc:157").
SOURCE = re.compile(
    r"\b[0-9.]+ ?(?:ms|s|seconds?|min|minutes?)\b|#\d+\b|\bD\d+\b"
    r"|\w\.(?:java|py|php|inc|js|sh|yml)\b"
)
# The waits whose source sits here, since a comment in the script would
# change a golden capture input (server/tools/golden_inputs.py) and need a
# recapture: script and line -> source.
SOURCED_HERE = {
    ("test-playlist.sh", "sleep 11"): "the scene switches 10 s after the round"
    " ends (playlist.inc:157, PLAYLIST_SCENE_DELAY in"
    " server/racing/services/playlist.py), and 1 s more",
}


def blank_quotes(line: str) -> str:
    """The line with each quoted string's text turned to x's, so that
    `echo "sleep 1"` holds no command; a lone expansion ("$N") stays."""
    return QUOTED.sub(
        lambda m: (
            m[0]
            if EXPANSION.fullmatch(m[0])
            else m[0][0] + "x" * (len(m[0]) - 2) + m[0][-1]
        ),
        line,
    )


def is_wait(code: str) -> bool:
    """Whether a line's code sets a bound, or waits for a length that is no
    variable named for a bound. Such a variable (`--max-time $POLL_SECONDS`)
    takes its source from the line that sets it, a bound this scan checks
    where a script CI runs sets it."""
    if BOUND.search(code):
        return True
    found = WAIT.search(blank_quotes(code))
    if not found:
        return False
    var = re.search(r"\$\{?(\w+)", found["length"])
    return not (var and re.fullmatch(BOUND_NAME, var[1], re.IGNORECASE))


def unsourced_waits(text: str) -> list[tuple[int, str]]:
    """Each wait or bound, as its line number and line, whose comment names
    no source: the comment on its line, the comment block right above it,
    or a comment in the three lines above it."""
    lines = text.splitlines()
    found = []
    for n, line in enumerate(lines):
        hash_at = re.search(r"(?:^|\s)#", blank_quotes(line))
        cut = hash_at.start() if hash_at else len(line)
        if not is_wait(line[:cut]):
            continue
        notes = [line[cut:]]
        above = n - 1
        while above >= 0 and lines[above].lstrip().startswith("#"):
            notes.append(lines[above])
            above -= 1
        notes += [
            prev for prev in lines[max(0, n - 3) : n] if prev.lstrip().startswith("#")
        ]
        if not any(SOURCE.search(note) for note in notes):
            found.append((n + 1, line.strip()))
    return found


def uncased_bounds(script: Path) -> list[str]:
    """Each bound the script lets its caller set (`NAME=${NAME:-30}`) that
    no case of its `<script>-cases.sh` sets."""
    bounds = [
        found["name"] or found["name2"]
        for line in code_lines(script)
        if (found := BOUND.search(line)) and (found["default"] or found["default2"])
    ]
    cases = script.with_name(f"{script.stem}-cases.sh")
    text = cases.read_text(encoding="utf-8") if cases.is_file() else ""
    return [name for name in bounds if not re.search(rf"\b{name}=", text)]


def test_the_scan_covers_the_scripts_ci_runs():
    """With the steps exercise-one-database.sh runs, which no workflow
    names (#408 review)."""
    assert {
        "race-with-derby-timer.sh",
        "test-playlist.sh",
        "test-each-role.sh",
        "test-racer-query.sh",
        "common.sh",
    } <= {path.name for path in ci_scripts()}


def test_the_scan_follows_what_a_ci_script_runs(tmp_path):
    workflows, testing = tmp_path / "workflows", tmp_path / "testing"
    workflows.mkdir()
    testing.mkdir()
    (workflows / "a.yml").write_text(
        "      run: |\n"
        "        bash testing/suite.sh http://x\n"
        "        # bash testing/commented.sh\n"
    )
    for name, text in {
        "suite.sh": "source `dirname $0`/common.sh\nstep one.sh\n"
        "step --resets two.sh\n",
        "common.sh": "# step commented.sh\n",
        "one.sh": "$(dirname $0)/suite.sh\n$(dirname $0)/three.sh\n",
        "two.sh": "",
        "three.sh": "",
    }.items():
        (testing / name).write_text(text)

    assert ci_script_names(workflows, testing) == {
        "suite.sh",
        "common.sh",
        "one.sh",
        "two.sh",
        "three.sh",
    }


@pytest.mark.parametrize("script", ci_scripts(), ids=lambda path: path.name)
def test_every_wait_in_a_script_ci_runs_names_its_source(script):
    """A bound's comment says where its number comes from (AGENT-BRIEF's
    Evidence rules); #399's review sent back the waits the next test
    holds."""
    found = unsourced_waits(script.read_text(encoding="utf-8"))
    assert [
        (n, line) for n, line in found if (script.name, line) not in SOURCED_HERE
    ] == []


def test_a_wait_with_no_source_is_found():
    """The first lines are the waits #399's review sent back, as 8726c4ef
    had them; the rest pass, but for a source comment too far above."""
    text = (
        "# HEAT_SECONDS bounds each heat: six times the 5 s a heat takes.\n"
        "HEAT_SECONDS=${HEAT_SECONDS:-30}\n"
        "# A thread dump after this long, if the timer is not identified yet;\n"
        "# the wait goes on.\n"
        "DUMP_AFTER=${DUMP_AFTER:-30}\n"
        "        kill -QUIT $JAR_PID\n"
        "        sleep 1\n"
        '    thread_dump "$1"\n'
        "    sleep 2\n"
        "curl --max-time 10 -s $URL\n"
        "    sleep 0.25  # replay.php polls every 250 ms\n"
        "# The scene switches 10 s after the round (playlist.inc:157).\n"
        'echo "Waiting..."\n'
        "sleep 11\n"
        "curl --max-time $POLL_SECONDS -s $URL\n"
        "# See #399.\n"
        "timeout -k 5 300 java -jar x.jar\n"
        "# Up to 5 s.\n"
        "for _ in $(seq 10); do\n"
        "    test -e x && break\n"
        "    true\n"
        "    sleep 0.5\n"
        "done\n"
    )

    assert unsourced_waits(text) == [
        (5, "DUMP_AFTER=${DUMP_AFTER:-30}"),
        (7, "sleep 1"),
        (9, "sleep 2"),
        (10, "curl --max-time 10 -s $URL"),
        (22, "sleep 0.5"),
    ]


@pytest.mark.parametrize(
    ("line", "found"),
    [
        # The #408 review's probes.
        ("sleep $N", True),
        ('sleep "$N"', True),
        ("sleep ${N:-3}", True),
        ("sleep 1m", True),
        ("sleep\t1", True),
        ('curl --max-time "$X" u', True),
        ("curl -m 10 u", True),
        ("curl --connect-timeout 5 u", True),
        ('timeout "$X" cmd', True),
        ("timeout 5s cmd", True),
        ('echo "step #2"; sleep 1', True),
        ("echo ok; sleep 1", True),
        ("until x; do sleep 2; done", True),
        ("x && sleep 1", True),
        ("x | timeout 5 y", True),
        ("(sleep 1)", True),
        ("{ sleep 1; }", True),
        ("`sleep 1`", True),
        ("! timeout 5 x", True),
        ("X=$(timeout 5 y)", True),
        ("if timeout 5 x; then", True),
        ("while sleep 1; do", True),
        ("then sleep 1", True),
        ("else sleep 1", True),
        ("exec timeout 5 x", True),
        ("time sleep 1", True),
        ("timeout -k 5 300 java", True),
        ("timeout --foreground 5 x", True),
        ("sleep $((N * 2))", True),
        ("curl --max-time=5 u", True),
        ("echo $#; sleep 1", True),
        ("until timeout 5 x; do", True),
        ('sleep "${N}"', True),
        ("TIMEOUT_S=5", True),
        ("read -t 5 x", True),
        ("read -r -t 5 x", True),
        ("local timeout=30", True),
        ("readonly POLL_SECONDS=10", True),
        ("X_MS=300", True),
        ('NAME_SECONDS="${NAME_SECONDS:-30}"', True),
        ("export NAME_SECONDS=${NAME_SECONDS:-30}", True),
        (": ${NAME_SECONDS:=30}", True),
        (': "${NAME_SECONDS=30}"', True),
        ("NAME_SECONDS=${NAME_SECONDS-30}", True),
        ("DUMP_AFTER=${DUMP_AFTER:-30}", True),
        # A variable named for a bound is sourced where it is set.
        ("curl --max-time $POLL_SECONDS u", False),
        ('sleep "$HEAT_DELAY"', False),
        ("sleep ${HEAT_DELAY}", False),
        # No wait: text in quotes, a comment, a value that is no number.
        ('echo "sleep 1 minute"', False),
        ('grep -c "sleep 1" f', False),
        ("echo timeout 5 reached", False),
        ('printf "timeout 5\\n"', False),
        ("grep -m 1 'Notice' f", False),
        ("curl -s u | grep -m 1 x", False),
        ("echo 'sleep 1'", False),
        ("echo 'a; sleep 1'", False),
        ("echo sleep 1", False),
        ("LOG_AFTER=$(date)", False),
        ("LANES=${LANES:-4}", False),
        ("# sleep 1", False),
        # Waits the rule leaves out.
        ("HEAT_LIMIT=30", False),
        ("ping -w 3 h", False),
        ("nc -w 3 h p", False),
        ("wait $PID", False),
        ("tail --pid=1 -f x", False),
    ],
)
def test_which_lines_are_waits(line, found):
    assert (unsourced_waits(line + "\n") == [(1, line)]) is found


def test_each_wait_sourced_here_is_still_in_its_script_with_no_source():
    for (name, line), source in SOURCED_HERE.items():
        assert SOURCE.search(source)
        found = unsourced_waits((TESTING / name).read_text(encoding="utf-8"))
        assert line in [text for _, text in found], (name, line)


def test_where_a_waits_source_may_sit():
    """A source in a long comment block counts, a code line or a source
    past the block does not, and a commented-out wait is no wait."""
    long_block = "# About twice that 151 s.\n# a\n# b\n# c\nIDENTIFY_SECONDS=300\n"
    assert unsourced_waits(long_block) == []
    assert unsourced_waits("source common.sh\nsleep 1\n") == [(2, "sleep 1")]
    assert unsourced_waits("# Was: sleep 3\n") == []
    assert unsourced_waits("# 5 s\nx\nsleep 1\n") == []
    assert unsourced_waits("sleep 1  # 5 s\n") == []
    assert unsourced_waits("sleep 1  # D85\n") == []
    assert unsourced_waits("sleep 1  # wait\n") == [(1, "sleep 1  # wait")]
    assert unsourced_waits('echo "#5 s"; sleep 1\n') == [(1, 'echo "#5 s"; sleep 1')]
    # The block ends at the first line, not at the last one, read as -1.
    assert unsourced_waits("# no source\nsleep 1\nx\ny\nz\n# 5 s\n") == [(2, "sleep 1")]


@pytest.mark.parametrize("script", ci_scripts(), ids=lambda path: path.name)
def test_every_bound_a_caller_can_set_has_a_case(script):
    """Each `NAME=${NAME:-N}` bound is set by a case of the script's
    `-cases.sh` beside it, which runs the script with that bound and checks
    how the run ends. #399's cases first ran after its first push, and the
    dump guard and the missing-log path each failed there (#399 retro)."""
    assert uncased_bounds(script) == []


def test_a_bound_with_no_case_is_found(tmp_path):
    script = tmp_path / "race.sh"
    script.write_text(
        "IDENTIFY_SECONDS=${IDENTIFY_SECONDS:-300}\n"
        '  RACE_SECONDS="${RACE_SECONDS:-180}"\n'
        "export HEAT_SECONDS=${HEAT_SECONDS-30}\n"
        ": ${DUMP_AFTER:=30}\n"
        "POLL_SECONDS=10\n"
        "OTHER=${HEAT_SECONDS:-30}\n"
        "OTHER_SECONDS=${HEAT_SECONDS:-30}\n"
        "LANES=${LANES:-4}\n"
        "# JAVA_DELAY=${JAVA_DELAY:-0}\n"
    )

    assert uncased_bounds(script) == [
        "IDENTIFY_SECONDS",
        "RACE_SECONDS",
        "HEAT_SECONDS",
        "DUMP_AFTER",
    ]

    (tmp_path / "race-cases.sh").write_text(
        "identify-1s|IDENTIFY_SECONDS=1|did not complete\n"
        "race-60s|XRACE_SECONDS=60|did not complete\n"
        "heat-3s|HEAT_SECONDS=3|x\n"
        "dump|DUMP_AFTER=0|x\n"
    )

    assert uncased_bounds(script) == ["RACE_SECONDS"]
