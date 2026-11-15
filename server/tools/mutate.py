"""Runs one mutant: replaces a text in a file, runs the tests it targets,
restores the file, and names the result (#319 retro).

#319's hand-written mutation script asserted that its old text occurred once
and, when it occurred twice, ran pytest on the unmutated tree, so two
mutants read as survivors until the traceback was read. Its `-k` misspelt a
test name, and pytest deselected the test without a word. This refuses:

- an old text that does not occur exactly once in the file; give more
  context. A new text equal to it, which would change nothing (#324 retro);
- a run that deselects a test, runs none, or errors with no failure, so name
  the tests by node id (`path::test`, with `[param]` for one case of a
  parametrized test) and give `-m browser` for a browser test, which
  `addopts` deselects;
- xdist (`-n`): its summary leaves out the deselected count, so the run is
  in one process (`-p no:xdist`);
- a file git doesn't track, or one that differs from the index: a run
  killed with SIGKILL leaves its mutant in the file, and the next run would
  take it as the original. Stage a change under test with `git add` first;
- a second run on the same file while one holds its lock, a file under the
  git directory named for the file. A SIGKILL leaves the lock behind too,
  and the refusal names it.

Run it from server/, one mutant per call, in the worktree under test:

    uv run python tools/mutate.py --file ui/views.py --old 'a < b' --new 'a <= b' \\
        -- ui/tests/test_views.py::test_the_bound

It prints pytest's output, then one line: `KILLED` (a test failed),
`SURVIVED` (every test passed), or with `--expect pass`, for a should-pass
mutant, `PASSED` or `BROKE`. It exits 0 when the result is the one
expected, 1 when it is not, and 2 when it refuses, when pytest errors (an
error during collection, a usage error) or when the run passes `--timeout`
(seconds, 900 by default), naming why. The file is restored, and checked
against its saved bytes, on every exit, a SIGTERM, SIGINT or SIGHUP
included; a SIGKILL leaves it mutated, and the next run refuses it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

REFUSED = 2
# pytest's last summary line, such as "1 failed, 3 passed, 2 deselected in 1.2s".
COUNT = re.compile(r"(\d+) (passed|failed|skipped|deselected|xfailed|xpassed|error)")
XDIST = re.compile(r"^(-n|--numprocesses|--dist|--tx)")
OUTCOMES = {
    "fail": {1: "KILLED", 0: "SURVIVED"},
    "pass": {0: "PASSED", 1: "BROKE"},
}
EXPECTED = {"fail": "KILLED", "pass": "PASSED"}


class Refused(Exception):
    pass


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
    if found.get("error") and not found.get("failed"):
        return f"{found['error']} error and no failure: fix what errors first"
    return None


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=path.parent, capture_output=True, text=True
    )


def _check_tracked(path: Path) -> None:
    if _git(path, "ls-files", "--error-unmatch", "--", path.name).returncode:
        raise Refused(f"{path} is not tracked by git; add it first")


def _check_clean(path: Path) -> None:
    if _git(path, "diff", "--quiet", "--", path.name).returncode:
        raise Refused(
            f"{path} differs from the index: a killed run may have left its"
            f" mutant there (git diff -- {path}); stage a change under test"
            " with git add"
        )


def _lock_path(path: Path) -> Path:
    # `_check_tracked` has found the repository.
    found = _git(path, "rev-parse", "--absolute-git-dir")
    name = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
    return Path(found.stdout.strip()) / f"mutate-{name}.lock"


def _lock(lock: Path, path: Path) -> None:
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        owner = lock.read_text().strip() if lock.exists() else "?"
        raise Refused(
            f"another run (pid {owner}) holds {lock} for {path}; if that run"
            f" is gone, check git diff -- {path}, then remove the lock"
        ) from None
    with os.fdopen(fd, "w") as held:
        held.write(str(os.getpid()))


def _stop(signum, frame):
    sys.exit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--expect", choices=("fail", "pass"), default="fail")
    parser.add_argument("--timeout", type=float, default=900, help="seconds")
    parser.add_argument("pytest_args", nargs="+", help="after --: node ids, -m ...")
    args = parser.parse_args(argv)
    path = args.file
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, _stop)

    try:
        if args.new == args.old:
            raise Refused("--new is --old: the mutant would change nothing")
        if any(XDIST.match(arg) for arg in args.pytest_args):
            raise Refused(
                "xdist leaves the deselected count out of its summary;"
                " drop -n, and the run is in one process"
            )
        _check_tracked(path)
        lock = _lock_path(path)
        _lock(lock, path)
    except Refused as why:
        print(f"mutate.py: refused: {why}", file=sys.stderr)
        return REFUSED

    try:
        try:
            _check_clean(path)
        except Refused as why:
            print(f"mutate.py: refused: {why}", file=sys.stderr)
            return REFUSED
        saved = path.read_bytes()
        text = saved.decode()
        if (times := text.count(args.old)) != 1:
            print(
                f"mutate.py: refused: --old occurs {times} times in {path};"
                " it must occur once, so give more of the line around it",
                file=sys.stderr,
            )
            return REFUSED
        try:
            path.write_text(text.replace(args.old, args.new))
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "-p", "no:xdist", *args.pytest_args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired as late:
            run = None
            output = late.stdout or ""
            output = output if isinstance(output, str) else output.decode()
            print(output, end="" if output.endswith("\n") or not output else "\n")
        finally:
            path.write_bytes(saved)
            assert path.read_bytes() == saved, f"{path} was not restored"
    finally:
        lock.unlink()

    mutant = f"{path}: {args.old!r} -> {args.new!r}"
    if run is None:
        print(f"ERROR  {mutant}  (timed out after {args.timeout:g} s)")
        return REFUSED
    print(run.stdout, end="")
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
