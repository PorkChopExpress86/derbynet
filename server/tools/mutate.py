"""Runs one mutant: replaces a text in a file, runs the tests it targets,
restores the file, and names the result (#319 retro).

#319's hand-written mutation script asserted that its old text occurred once
and, when it occurred twice, ran pytest on the unmutated tree, so two
mutants read as survivors until the traceback was read. Its `-k` misspelt a
test name, and pytest deselected the test without a word. This refuses both:

- the old text must occur exactly once in the file; give more context when
  it does not;
- the run must deselect nothing and run at least one test, so name the tests
  by node id (`path::test`, with `[param]` for one case of a parametrized
  test) and give `-m browser` for a browser test, which `addopts` deselects.

Run it from server/, one mutant per call, in the worktree under test:

    uv run python tools/mutate.py --file ui/views.py --old 'a < b' --new 'a <= b' \\
        -- ui/tests/test_views.py::test_the_bound

It prints pytest's output, then one line: `KILLED` (a test failed),
`SURVIVED` (every test passed), or with `--expect pass`, for a should-pass
mutant, `PASSED` or `BROKE`. It exits 0 when the result is the one
expected, 1 when it is not, and 2 when it refuses or pytest errors (an error
during collection, a usage error), naming why. The file is restored on
every exit, a SIGTERM or SIGINT included, and checked against its saved
bytes.
"""

from __future__ import annotations

import argparse
import re
import signal
import subprocess
import sys
from pathlib import Path

REFUSED = 2
# pytest's last summary line, such as "1 failed, 3 passed, 2 deselected in 1.2s".
COUNT = re.compile(r"(\d+) (passed|failed|skipped|deselected|xfailed|xpassed|error)")
OUTCOMES = {
    "fail": {1: "KILLED", 0: "SURVIVED"},
    "pass": {0: "PASSED", 1: "BROKE"},
}
EXPECTED = {"fail": "KILLED", "pass": "PASSED"}


def counts(output: str) -> dict[str, int]:
    """The counts of pytest's summary line, the last line that has any."""
    for line in reversed(output.splitlines()):
        if found := COUNT.findall(line):
            return {kind: int(n) for n, kind in found}
    return {}


def refusal(found: dict[str, int]) -> str | None:
    """Why a run's tests don't show what the mutant does, or None."""
    if found.get("deselected"):
        return (
            f"{found['deselected']} deselected: name each test by node id,"
            " and give -m browser for a browser test"
        )
    if not found.get("passed") and not found.get("failed"):
        return "no test ran, or every one skipped"
    return None


def _stop(signum, frame):
    sys.exit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--expect", choices=("fail", "pass"), default="fail")
    parser.add_argument("pytest_args", nargs="+", help="after --: node ids, -m ...")
    args = parser.parse_args(argv)

    saved = args.file.read_bytes()
    text = saved.decode()
    if (times := text.count(args.old)) != 1:
        print(
            f"mutate.py: refused: --old occurs {times} times in {args.file};"
            " it must occur once, so give more of the line around it",
            file=sys.stderr,
        )
        return REFUSED

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        args.file.write_text(text.replace(args.old, args.new))
        run = subprocess.run(
            [sys.executable, "-m", "pytest", *args.pytest_args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        args.file.write_bytes(saved)
        assert args.file.read_bytes() == saved, f"{args.file} was not restored"
    print(run.stdout, end="")

    mutant = f"{args.file}: {args.old!r} -> {args.new!r}"
    # Exit status 5: pytest collected no test, or deselected every one.
    if run.returncode in (0, 1, 5) and (why := refusal(counts(run.stdout))):
        print(f"REFUSED  {mutant}  ({why})")
        return REFUSED
    outcome = OUTCOMES[args.expect].get(run.returncode)
    if outcome is None:
        print(f"ERROR  {mutant}  (pytest exit status {run.returncode})")
        return REFUSED
    print(f"{outcome}  {mutant}")
    return 0 if outcome == EXPECTED[args.expect] else 1


if __name__ == "__main__":
    sys.exit(main())
