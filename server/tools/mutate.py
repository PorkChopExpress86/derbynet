"""Runs a mutant, or every listed one: replaces a text in a file, runs the
tests it targets, restores the file, and names the result (#319 retro).

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

Run it from server/, in the worktree under test, for one mutant:

    uv run python tools/mutate.py --file ui/views.py --old 'a < b' --new 'a <= b' \\
        -- ui/tests/test_views.py::test_the_bound

or, with `--listed`, for every mutant `tools/operand_mutants.py` lists for
the diff from `--base` (origin/master by default), narrowed to each
`--file` given, one after another against the same tests:

    uv run python tools/mutate.py --listed --file ../plan/bin/affected-tests \\
        -- ../plan/tests/test_affected_tests.py::ChangedTests

#336's mutants were picked by hand, mostly one way, and 21 of 98 survived
on branches no test reached; the listing's every condition and operand,
each made true and made false, is what "each condition is mutated" means
(#336 retro). It prints a row per mutant as it runs (`KILLED`, `SURVIVED`,
or `REFUSED` or `ERROR` with pytest's last lines), then the count of each,
and exits 0 when every one is KILLED, 1 when one survived, and 2 when one
was refused or errored, or none is listed. It mutates `.py`, `.js` and
`.css` files and Python scripts, and names each other file whose mutants
it skips: a `min()` a Markdown file quotes, and a test file's own
conditions, since `affected-tests` prints this line with no `--file` and
#336 retro's diff held 61 in its test files. A mutant whose text occurs
more than once on its line is refused, to be run by hand. A stop restores each
file and names the mutants not run. The cases the listing reads past are
still mutated by hand, one per call.

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
import importlib
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
# What --listed mutates: a `min()` a Markdown file quotes is no code.
SOURCES = (".py", ".js", ".css")


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


def _pytest(pytest_args: list[str], timeout: float):
    """pytest's run, or None and what it printed when it passes `timeout`."""
    try:
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:xdist", *pytest_args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as late:
        # Bytes whatever `text` says (the subprocess docs, TimeoutExpired).
        return None, (late.stdout or b"").decode()
    return run, run.stdout


def _result(run, output: str, expect: str) -> tuple[str, str]:
    """The result word for a finished run, and why when it shows nothing."""
    # Exit status 5: pytest collected no test, or deselected every one.
    if run.returncode in (0, 1, 5) and (why := refusal(counts(output))):
        return "REFUSED", why
    outcome = OUTCOMES[expect].get(run.returncode)
    if outcome is None:
        return "ERROR", f"pytest exit status {run.returncode}"
    return outcome, ""


def _listing():
    try:
        return importlib.import_module("operand_mutants")
    except ImportError:
        return importlib.import_module("tools.operand_mutants")


def _one(path: Path, data: bytes, m, args) -> tuple[str, str, str]:
    """One listed mutant's result word, why, and pytest's output; the file
    holds `data` again when it returns or raises."""
    text = data.decode()
    if m.span is None or text[m.span[0] : m.span[1]] != m.old:
        return "REFUSED", "not once on its line: run it by hand", ""
    start, end = m.span
    try:
        path.write_text(text[:start] + m.new + text[end:])
        run, output = _pytest(args.pytest_args, args.timeout)
    finally:
        path.write_bytes(data)
        assert path.read_bytes() == data, f"{path} was not restored"
    if run is None:
        return "ERROR", f"timed out after {args.timeout:g} s", output
    return (*_result(run, output, "fail"), output)


def listed(args) -> int:
    """Runs every mutant tools/operand_mutants.py lists for the diff."""
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if top.returncode:
        print("mutate.py: refused: not in a git work tree", file=sys.stderr)
        return REFUSED
    root = Path(top.stdout.strip())
    diff = subprocess.run(
        ["git", "diff", "-U0", args.base, "--", *map(str, args.file or [])],
        capture_output=True,
        text=True,
    )
    if diff.returncode:
        print(f"mutate.py: refused: git diff: {diff.stderr.strip()}", file=sys.stderr)
        return REFUSED
    listing = _listing()
    found, skipped = [], {}
    for m in listing.mutants(diff.stdout, root):
        if Path(m.path).name.startswith("test_"):
            why = "in a test file"
        elif m.path.endswith(SOURCES) or listing.is_python(root, m.path):
            found.append(m)
            continue
        else:
            why = "in no source file"
        n, _ = skipped.get(m.path, (0, why))
        skipped[m.path] = (n + 1, why)
    for path, (n, why) in skipped.items():
        print(f"SKIPPED   {path}: {n} listed, {why}", flush=True)
    if not found:
        print("mutate.py: refused: no mutant listed for the diff", file=sys.stderr)
        return REFUSED
    paths = list(dict.fromkeys(root / m.path for m in found))
    locks, saved = [], {}
    rows: list[tuple[str, object]] = []
    try:
        try:
            for path in paths:
                _check_tracked(path)
                lock = _lock_path(path)
                _lock(lock, path)
                locks.append(lock)
                _check_clean(path)
                saved[path] = path.read_bytes()
        except Refused as why:
            print(f"mutate.py: refused: {why}", file=sys.stderr)
            return REFUSED
        try:
            for m in found:
                word, why, output = _one(root / m.path, saved[root / m.path], m, args)
                rows.append((word, m))
                print(f"{word:<8}  {m}" + (f"  ({why})" if why else ""), flush=True)
                if word in ("REFUSED", "ERROR") and output:
                    for line in output.splitlines()[-15:]:
                        print(f"    {line}")
        except SystemExit:
            left = found[len(rows) :]
            print(f"STOPPED  {len(left)} of {len(found)} not run:", flush=True)
            for m in left:
                print(f"  NOT RUN  {m}", flush=True)
            raise
    finally:
        for path, data in saved.items():
            if path.read_bytes() != data:
                path.write_bytes(data)
        for lock in locks:
            lock.unlink()
    tally = {
        w: sum(1 for word, _ in rows if word == w)
        for w in ("KILLED", "SURVIVED", "REFUSED", "ERROR")
    }
    print(f"{len(rows)} listed: " + ", ".join(f"{n} {w}" for w, n in tally.items()))
    for word, m in rows:
        if word != "KILLED":
            print(f"  {word:<8}  {m}")
    if tally["REFUSED"] or tally["ERROR"]:
        return REFUSED
    return 1 if tally["SURVIVED"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        help="the file to mutate; with --listed, each file to list",
    )
    parser.add_argument("--old")
    parser.add_argument("--new")
    parser.add_argument(
        "--listed",
        action="store_true",
        help="every mutant tools/operand_mutants.py lists",
    )
    parser.add_argument("--base", default="origin/master", help="with --listed")
    parser.add_argument("--expect", choices=("fail", "pass"), default="fail")
    parser.add_argument("--timeout", type=float, default=900, help="seconds")
    parser.add_argument("pytest_args", nargs="+", help="after --: node ids, -m ...")
    args = parser.parse_args(argv)
    if args.listed:
        if args.old is not None or args.new is not None or args.expect != "fail":
            parser.error("--listed takes no --old, --new or --expect")
    elif args.old is None or args.new is None or not args.file or len(args.file) > 1:
        parser.error("give one --file, --old and --new, or --listed")
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, _stop)
    if any(XDIST.match(arg) for arg in args.pytest_args):
        print(
            "mutate.py: refused: xdist leaves the deselected count out of its"
            " summary; drop -n, and the run is in one process",
            file=sys.stderr,
        )
        return REFUSED
    if args.listed:
        return listed(args)
    path = args.file[0]

    try:
        if args.new == args.old:
            raise Refused("--new is --old: the mutant would change nothing")
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
            run, output = _pytest(args.pytest_args, args.timeout)
            if run is None:
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
    outcome, why = _result(run, run.stdout, args.expect)
    if outcome in ("REFUSED", "ERROR"):
        print(f"{outcome}  {mutant}  ({why})")
        return REFUSED
    print(f"{outcome}  {mutant}")
    return 0 if outcome == EXPECTED[args.expect] else 1


if __name__ == "__main__":
    sys.exit(main())
