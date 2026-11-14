"""Under `set -o pipefail`, no command pipes into a reader that stops early:
`head`, or `grep -q`.

The reader exits at its first line or match, the writer's next write gets
SIGPIPE, and pipefail makes the whole pipe fail. A `set -e` script then stops,
as #58's `ls ... | head -1` in checks.sh did in image run 43490392705, and in
an `if` the match is lost (test_an_early_reader_loses_the_match). #58's
round 1 took `grep -q` off the pipes it judged mattered (M8) and left the
rest (#58 retro).

The forms that read the writer's whole output first:

    grep -q PATTERN <<<"$(command)"        # instead of command | grep -q
    command | sed -n 1p                     # instead of command | head -1

An `echo` or `printf` of a variable is a builtin, so it may pipe into
`grep -q`.

A workflow's `run:` step counts too when it runs under pipefail: its shell is
`bash`, which GitHub runs as `bash --noprofile --norc -eo pipefail {0}`, or it
sets pipefail itself. A step with no shell given runs as `bash -e {0}`, as
image.yml's logs print, so its pipes pass on the reader's status
(tests/workflow_steps.py).
"""

import re
import subprocess
from pathlib import Path

from tests.workflow_steps import WORKFLOWS, Step, steps

ROOT = Path(__file__).resolve().parents[2]
PIPEFAIL = re.compile(r"^\s*set\s+(?:-\w+\s+)*-\w*o\s+pipefail\b", re.M)
SHEBANG = re.compile(r"^#!\s*\S*\b(?:ba)?sh\b|^#!\s*/usr/bin/env\s+(?:ba)?sh\b")
# A reader that stops before its input ends.
EARLY = re.compile(r"^(?:head\b|grep\s+(?:-\S+\s+)*(?:-\w*q\w*|--quiet)\b)")
# What may come before a pipeline's first command.
LEAD = re.compile(r"^(?:if|then|elif|else|do|while|until|!|\{|\(|\$\(|time)\s*")
BUILTIN_WRITERS = {"echo", "printf"}
# A file left as it is, with the reason.
EXEMPT = {
    "server/tests/golden/capture/capture-http.sh": (
        "a golden input (tools/golden_inputs.py): an edit means a 25-minute "
        "recapture; its grep | head pipes read one response's headers, and "
        "each ends in `|| true`"
    ),
}


def shell_files() -> list[Path]:
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    files = []
    for name in names:
        if not name or name.startswith("website/"):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        if name.endswith(".sh"):
            files.append(path)
            continue
        try:
            with path.open(encoding="utf-8") as f:
                first = f.readline()
        except UnicodeDecodeError, OSError:
            continue
        if SHEBANG.match(first):
            files.append(path)
    return files


def logical_lines(text: str):
    """(first line number, line) with comments dropped and a line ending in
    `\\` or `|` joined to the next."""
    start, parts = None, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"(^|\s)#.*$", r"\1", raw).rstrip()
        if start is None:
            start = n
        if line.endswith("\\"):
            parts.append(line[:-1])
            continue
        parts.append(line)
        if line.endswith("|") and not line.endswith("||"):
            continue
        yield start, " ".join(parts)
        start, parts = None, []
    if parts:
        yield start, " ".join(parts)


def early_pipes(text: str) -> list[tuple[int, str]]:
    found = []
    for n, line in logical_lines(text):
        # Split on a single `|`, not `||`.
        segments = re.split(r"(?<!\|)\|(?!\|)", line)
        for writer, reader in zip(segments, segments[1:], strict=False):
            if not EARLY.match(reader.strip()):
                continue
            command = re.split(r"&&|\|\||;|\$\(", writer)[-1].strip()
            while (lead := LEAD.match(command)) and lead.end():
                command = command[lead.end() :]
            words = command.split()
            if words and words[0] in BUILTIN_WRITERS:
                continue
            found.append((n, line.strip()))
    return found


def workflow_pipes(name: str, text: str) -> list[str]:
    out = []
    for step in steps(text):
        if not (step.pipefail or PIPEFAIL.search(step.script)):
            continue
        # A block's first line is the one after `run: |`.
        first = step.line + (
            0 if "\n" not in step.script and _inline(step, text) else 1
        )
        for n, line in early_pipes(step.script):
            out.append(f"{name}:{first + n - 1}: {line}")
    return out


def _inline(step: Step, text: str) -> bool:
    value = text.splitlines()[step.line - 1].split("run:", 1)[1].strip()
    return not value.startswith(("|", ">"))


def offenders(exempt=EXEMPT) -> list[str]:
    out = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        name = str(path.relative_to(ROOT))
        if name not in exempt:
            out += workflow_pipes(name, path.read_text(encoding="utf-8"))
    for path in shell_files():
        if str(path.relative_to(ROOT)) in exempt:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not PIPEFAIL.search(text):
            continue
        for n, line in early_pipes(text):
            out.append(f"{path.relative_to(ROOT)}:{n}: {line}")
    return out


def test_each_exempt_file_still_has_such_a_pipe():
    """An exemption goes once its file has no such pipe left."""
    found = {line.split(":", 1)[0] for line in offenders(exempt={})}
    assert set(EXEMPT) <= found, set(EXEMPT) - found


def test_no_command_pipes_into_an_early_reader_under_pipefail():
    found = offenders()
    assert found == [], (
        "under pipefail these pipe a command into head or grep -q; read its "
        'whole output first (grep -q PATTERN <<<"$(command)", or sed -n 1p):\n'
        + "\n".join(found)
    )


def test_the_check_finds_each_form():
    text = """set -e -o pipefail
ls "$dir"/*.jpg | head -1
if docker exec x manage.py showmigrations --plan | grep -q '^\\[ \\]'; then :; fi
curl -sf "$url" \\
    | grep -qE success || fail
git diff --name-only a b |
  grep -q server/
echo "extensions: $(modules "$(ls /usr/sbin/php-fpm* | head -n 1)")"
"""
    assert [n for n, _ in early_pipes(text)] == [2, 3, 4, 6, 8]


def test_the_check_leaves_the_safe_forms():
    text = """set -euo pipefail
echo "$out" | grep -q 'not writable'
if printf '%s\\n' "$x" | grep -qx y; then :; fi
grep -q success <<<"$(curl -sf "$url")"
ls "$dir" | sed -n 1p
a || grep -q x file
docker logs x | grep -c POST
"""
    assert early_pipes(text) == []


WORKFLOW = """jobs:
  a:
{defaults}    steps:
      - name: one
{shell}        run: |
          echo start
          curl -sf -d 'action=setup.nodata' "$URL/action.php" | grep -q success
      - run: ls | head -1
"""


def test_a_workflow_step_under_pipefail_is_checked():
    """image.yml had two `curl ... | grep -q success` steps, the pipe
    checks.sh's new_event no longer uses (#58 retro review)."""
    bash_step = WORKFLOW.format(defaults="", shell="        shell: bash\n")
    curl = "curl -sf -d 'action=setup.nodata' \"$URL/action.php\" | grep -q success"
    assert workflow_pipes("w.yml", bash_step) == [f"w.yml:8: {curl}"]
    job_default = WORKFLOW.format(
        defaults="    defaults:\n      run:\n        shell: bash\n", shell=""
    )
    assert [line.split(":")[1] for line in workflow_pipes("w.yml", job_default)] == [
        "10",
        "11",
    ]
    sets_it = WORKFLOW.format(defaults="", shell="").replace(
        "echo start", "set -o pipefail"
    )
    assert [line.split(":")[1] for line in workflow_pipes("w.yml", sets_it)] == ["7"]


def test_a_workflow_step_without_pipefail_is_left():
    """No shell given runs as `bash -e {0}`: the pipe's status is grep's."""
    assert workflow_pipes("w.yml", WORKFLOW.format(defaults="", shell="")) == []
    sh = WORKFLOW.format(defaults="", shell="        shell: sh\n")
    assert workflow_pipes("w.yml", sh) == []


def test_the_pipefail_forms_count():
    for line in (
        "set -o pipefail",
        "set -e -E -u -o pipefail",
        "set -euo pipefail",
        "set -uo pipefail",
        "set -e -o pipefail",
    ):
        assert PIPEFAIL.search(line), line
    assert not PIPEFAIL.search("set -e")


def test_an_early_reader_loses_the_match():
    """`if seq 1000000 | grep -q 1` takes the else branch: grep matches the
    first line and exits, and seq's next write fails."""
    script = (
        "set -o pipefail; "
        "if seq 1000000 | grep -q 1; then echo hit; else echo missed; fi; "
        'if grep -q 1 <<<"$(seq 1000000)"; then echo hit; else echo missed; fi'
    )
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["missed", "hit"]
