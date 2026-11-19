"""Runs a mutant, or every listed one: replaces a text in a file, runs the
tests it targets, restores the file, and names the result (#319 retro).

#319's hand-written mutation script asserted that its old text occurred once
and, when it occurred twice, ran pytest on the unmutated tree, so two
mutants read as survivors until the traceback was read. Its `-k` misspelt a
test name, and pytest deselected the test without a word. This refuses:

- an old text that does not occur exactly once in the file; give more
  context. A new text equal to it, which would change nothing (#324 retro).
  A mutant of a Python file (a `.py` file, or a script whose shebang names
  Python) that no longer parses, where the file did: a test that loads the
  file only when it runs fails on it, which reads as KILLED (#345 retro);
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

Every pytest run it starts loads `tools/kill_guard.py` (`-p kill_guard`), so
`os.kill` and `os.killpg` refuse a process the run did not start: a mutant
that turns a parsed pid into the wrong one, as retro-391's `$PPID` named
`systemd --user` and ended the desktop session, fails its test instead of
signalling it. A run in which the guard refused a signal reads `KILLED`
(`BROKE` with `--expect pass`) with the refusal in its parentheses (#448).
The guard covers the Python tests' own calls, not a `kill` a script under
test runs. Before a mutant of code whose tests signal a pid, run those tests
once with `--kill-guard-print`, which sends nothing and lists each refusal:

    PYTHONPATH=tools uv run python -m pytest -p kill_guard --kill-guard-print \\
        tests/test_detach.py

Run it from server/, in the worktree under test, for one mutant:

    uv run python tools/mutate.py --file ui/views.py --old 'a < b' --new 'a <= b' \\
        -- ui/tests/test_views.py::test_the_bound

or, with `--listed`, for every mutant `tools/operand_mutants.py` lists for
the diff from the merge base with `--base` (origin/master by default),
narrowed to each `--file` given, one after another against the same tests:

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
#336 retro's last diff held 115 in its three test files. A test file that
`--file` names has the mutants of its helpers run, outside its test
functions, for a guard that lives beside its tests, such as #345's scanner
in `tests/test_docker_checks.py` (#345 retro). A mutant whose text occurs
more than once on its line is refused, to be run by hand. A stop restores each
file and names the mutants not run. The cases the listing reads past are
still mutated by hand, one per call.

Each run stops at its first failure (`-x`; a `--maxfail` given after it
wins), since one failure kills the mutant (#344 retro). It prints
pytest's output, then one line: `KILLED` (a test failed; with
`failed in helper NAME` when a helper in a test file raised, not the test
itself, so read whether the assert the mutant aims at failed),
`SURVIVED` (every test passed), or with `--expect pass`, for a should-pass
mutant, `PASSED` or `BROKE`. It exits 0 when the result is the one
expected, 1 when it is not, and 2 when it refuses, when pytest errors (an
error during collection, a usage error) or when the run passes `--timeout`
(seconds, 900 by default), naming why. The file is restored, and checked
against its saved bytes, on every exit, a SIGTERM, SIGINT or SIGHUP
included; a SIGKILL leaves it mutated, and the next run refuses it.

A second SIGTERM, SIGINT or SIGHUP waits while the file is written or put
back and the lock is taken or removed, so it can't leave either half done
(#358 review). Before each write it checks that the file still holds what
it last wrote. When it holds anything else, an edit by someone else, it
stops, leaves the file as it is, names it, and exits 2.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
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
# The summary line tools/kill_guard.py prints for each signal it refused.
GUARD_REFUSAL = re.compile(r"^kill_guard refused .*$", re.M)
GUARD = Path(__file__).resolve().with_name("kill_guard.py")
# The folder holding the copy of GUARD that each pytest run loads; main() sets it.
_guard_folder: str | None = None
# What --listed mutates: a `min()` a Markdown file quotes is no code.
SOURCES = (".py", ".js", ".css")


class Refused(Exception):
    pass


class Changed(Exception):
    """The file holds something mutate.py didn't write."""


STOPS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


@contextlib.contextmanager
def _held():
    """Holds SIGTERM, SIGINT and SIGHUP back; one that comes in meanwhile
    is delivered, and stops the run, when the block ends."""
    old = signal.pthread_sigmask(signal.SIG_BLOCK, STOPS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def _swap(path: Path, expect: bytes, new: bytes) -> None:
    """Writes `new` where the file still holds `expect`, with the stops held;
    raises Changed and leaves the file when it holds anything else."""
    with _held():
        if path.read_bytes() != expect:
            raise Changed(
                f"{path} changed while mutate.py held it: it is not what"
                f" mutate.py last wrote, so it is left as it is (git diff -- {path})"
            )
        path.write_bytes(new)
        if path.read_bytes() != new:
            raise Changed(f"{path} does not hold what mutate.py wrote")
        _drop_bytecode(path)


def _drop_bytecode(path: Path) -> None:
    """Removes the file's .pyc files, pytest's rewritten ones too. Python
    reuses a .pyc whose source has the same mtime, in whole seconds, and
    size: the next mutant of the same length written in the same second ran
    the last one's bytecode (test_listed_runs_a_member_taken_away), and a
    mutant that keeps the file's length ran one any import of the original
    wrote in its second (test_a_mutant_runs_past_a_stale_pyc_of_its_file).
    _swap() calls it after each write, the restore's too, so the .pyc a
    mutant's run writes goes with it."""
    for pyc in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
        pyc.unlink(missing_ok=True)


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


def stopped_on_error(found: dict[str, int]) -> bool:
    """Whether a `-x` run stopped on an error before any failure: an error
    shows nothing about the mutant, so the run goes again without `-x`."""
    return bool(found.get("error")) and not found.get("failed")


def _pytest(pytest_args: list[str], timeout: float):
    """pytest's run, stopped at its first failure, or None and what it
    printed when it passes `timeout`. One failure kills the mutant, so the
    tests after it only cost time: #344's first `--listed` rounds ran
    without `-x` and were stopped part-way, their mutants NOT RUN (#344
    retro). A `--maxfail` in the arguments comes after `-x` and wins. `-x`
    stops at a fixture's error too (stopped_on_error())."""
    run, output = _pytest_once(["-x", *pytest_args], timeout)
    if stopped_on_error(counts(output)):
        return _pytest_once(pytest_args, timeout)
    return run, output


def _pythonpath() -> list[str]:
    return [os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []


def _pytest_once(pytest_args: list[str], timeout: float):
    """pytest's run, or None and what it printed when it passes `timeout`."""
    # A mutant's .pyc goes with each write (_drop_bytecode). An empty
    # pycache prefix, tried first, recompiled every module each run, and the
    # tool's timing tests missed their bounds (#370 review round).

    # tools/kill_guard.py refuses a signal to a process outside the run. Its
    # copy from before the first mutant is what each run loads, so a mutant of
    # the guard is judged by the tests and not by itself (#448).
    assert _guard_folder, "main() copies the guard before a mutant is written"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([_guard_folder, *_pythonpath()]),
    }
    try:
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:xdist", "-p", "kill_guard"]
            + pytest_args,
            env=env,
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
    """The result word for a finished run, and why when it shows nothing,
    or for a failed run, the helper that raised (failed_in_helper()). A run
    in which tools/kill_guard.py refused a signal reads KILLED (BROKE for
    `--expect pass`) with its first refusal, ahead of the counts: a refusal in
    a fixture's teardown is an error with no failure beside it (#448)."""
    if run.returncode == 1 and (refused := GUARD_REFUSAL.search(output)):
        return OUTCOMES[expect][1], refused.group(0)
    # Exit status 5: pytest collected no test, or deselected every one.
    if run.returncode in (0, 1, 5) and (why := refusal(counts(output))):
        return "REFUSED", why
    outcome = OUTCOMES[expect].get(run.returncode)
    if outcome is None:
        return "ERROR", f"pytest exit status {run.returncode}"
    return outcome, failed_in_helper(output)


# A traceback entry's place in pytest's output: `test_x.py:12: AssertionError`
# for the entry that raised, `test_x.py:9: in join` or `test_x.py:9: ` for
# one on the way to it.
PLACE = re.compile(r"^(\S+\.py):(\d+): ", re.M)
FAILURES = re.compile(r"^=+ FAILURES =+$", re.M)
SECTION_END = re.compile(r"^={3,} ", re.M)
# The test side of a traceback: below it, in the code under test or a
# library, a raise is the mutant's effect, not the test's check.
TEST_FILE = re.compile(r"^(?:test_\w*|\w+_test|conftest)\.py$")


def failed_in_helper(output: str) -> str:
    """`failed in helper NAME at FILE:LINE, not a test` when pytest's first
    failure was raised outside a test function: in the last entry of its
    traceback that lies in a test file (test_*.py, *_test.py or
    conftest.py) under the working directory, the innermost function is
    not named `test*`. Empty otherwise. #417's M3, M10 and M11 read KILLED,
    killed by join_at's check that a run was still waiting, not by the
    order assert they aimed at (#417 retro)."""
    failures = FAILURES.search(output)
    if failures is None:
        return ""
    end = SECTION_END.search(output, failures.end())
    section = output[failures.end() : end.start() if end else len(output)]
    place = None
    for m in PLACE.finditer(section):
        path = Path(m.group(1))
        if TEST_FILE.match(path.name) and not path.is_absolute() and path.is_file():
            place = (m.group(1), int(m.group(2)))
    if place is None:
        return ""
    name = _function_at(Path(place[0]), place[1])
    if name is None or name.startswith("test"):
        return ""
    return f"failed in helper {name} at {place[0]}:{place[1]}, not a test"


def _function_at(path: Path, line: int) -> str | None:
    """The innermost function whose lines hold `line`, or None."""
    try:
        tree = ast.parse(path.read_bytes())
    except SyntaxError:
        return None
    holders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= line <= node.end_lineno
    ]
    # Each function that holds the line starts inside the one before it.
    holders.sort(key=lambda node: node.lineno)
    return holders[-1].name if holders else None


def _listing():
    try:
        return importlib.import_module("operand_mutants")
    except ImportError:
        return importlib.import_module("tools.operand_mutants")


def _unparsed(path: Path, saved: bytes, mutated: bytes) -> str | None:
    """Why a mutant of a Python file that parsed no longer parses, or None.
    A test that loads the file only when it runs fails on a broken file, and
    reads as KILLED: #345's m28 lost a newline to `$(...)` (#345 retro)."""
    if not _listing().is_python(Path.cwd(), str(path)):
        return None
    try:
        ast.parse(saved)
    except SyntaxError:
        return None
    try:
        ast.parse(mutated)
    except SyntaxError as err:
        return (
            f"the mutant does not parse as Python (line {err.lineno}: {err.msg});"
            " build --old and --new from a file or a heredoc, since $(...) drops"
            " trailing newlines"
        )
    return None


def _test_lines(path: Path) -> set[int]:
    """The lines of each test function in a test file, as pytest finds
    them: a top-level function named `test*`, `async def` too, or such a
    method of a top-level class, from its first decorator's line. A helper
    nested in a function is no test, whatever its name. Every line when the
    file does not parse."""
    text = path.read_bytes().decode()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set(range(1, text.count("\n") + 2))
    lines = set()
    for node in tree.body:
        for f in node.body if isinstance(node, ast.ClassDef) else [node]:
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                f.name.startswith("test")
            ):
                start = f.decorator_list[0].lineno if f.decorator_list else f.lineno
                lines |= set(range(start, f.end_lineno + 1))
    return lines


def _one(path: Path, data: bytes, m, args) -> tuple[str, str, str]:
    """One listed mutant's result word, why, and pytest's output; the file
    holds `data` again when it returns or raises, unless it raises Changed."""
    text = data.decode()
    if m.span is None or text[m.span[0] : m.span[1]] != m.old:
        return "REFUSED", "not once on its line: run it by hand", ""
    start, end = m.span
    mutated = (text[:start] + m.new + text[end:]).encode()
    try:
        _swap(path, data, mutated)
        run, output = _pytest(args.pytest_args, args.timeout)
    finally:
        _swap(path, mutated, data)
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
        # From the merge base, as operand_mutants.py diffs (#348 retro).
        [
            "git",
            "diff",
            "-U0",
            "--merge-base",
            args.base,
            "--",
            *map(str, args.file or []),
        ],
        capture_output=True,
        text=True,
    )
    if diff.returncode:
        print(f"mutate.py: refused: git diff: {diff.stderr.strip()}", file=sys.stderr)
        return REFUSED
    listing = _listing()
    named = {Path(f).resolve() for f in args.file or []}
    found, skipped, tests = [], {}, {}
    for m in listing.mutants(diff.stdout, root):
        if Path(m.path).name.startswith("test_"):
            # A test file named by --file has its helpers mutated, such as
            # #345's scanner in tests/test_docker_checks.py (#345 retro).
            if (root / m.path).resolve() not in named:
                why = "in a test file (name it with --file to mutate its helpers)"
            else:
                if m.path not in tests:
                    tests[m.path] = _test_lines(root / m.path)
                if m.line not in tests[m.path]:
                    found.append(m)
                    continue
                why = "in a test function"
        elif m.path.endswith(SOURCES) or listing.is_python(root, m.path):
            found.append(m)
            continue
        else:
            why = "in no source file"
        skipped[m.path, why] = skipped.get((m.path, why), 0) + 1
    for (path, why), n in skipped.items():
        print(f"SKIPPED   {path}: {n} listed, {why}", flush=True)
    if not found:
        # #367's diff (PR #392) changed only tests/test_mutate.py, so it
        # listed no mutant, and its dropped marker write died only by a clock
        # race (#367 retro).
        print(
            "mutate.py: refused: no mutant listed for the diff. Mutate each step"
            " the changed tests take by hand, one per call (--file, --old, --new),"
            " and see each fail on the test's own assert, not on a bound",
            file=sys.stderr,
        )
        return REFUSED
    paths = list(dict.fromkeys(root / m.path for m in found))
    locks, saved = [], {}
    rows: list[tuple[str, object]] = []
    try:
        try:
            for path in paths:
                _check_tracked(path)
                lock = _lock_path(path)
                with _held():
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
        except (SystemExit, Changed) as stop:
            left = found[len(rows) :]
            if isinstance(stop, Changed):
                print(f"mutate.py: stopped: {stop}", file=sys.stderr)
            print(f"STOPPED  {len(left)} of {len(found)} not run:", flush=True)
            for m in left:
                print(f"  NOT RUN  {m}", flush=True)
            if isinstance(stop, Changed):
                return REFUSED
            raise
    finally:
        with _held():
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
    global _guard_folder
    _guard_folder = tempfile.mkdtemp(prefix="mutate-guard-")
    try:
        shutil.copy(GUARD, Path(_guard_folder) / "kill_guard.py")
        return _main(argv)
    finally:
        shutil.rmtree(_guard_folder, ignore_errors=True)
        _guard_folder = None


def _main(argv: list[str] | None) -> int:
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

    lock = None
    try:
        if args.new == args.old:
            raise Refused("--new is --old: the mutant would change nothing")
        _check_tracked(path)
        lock_path = _lock_path(path)
        with _held():
            _lock(lock_path, path)
            lock = lock_path
        _check_clean(path)
        saved = path.read_bytes()
        text = saved.decode()
        if (times := text.count(args.old)) != 1:
            raise Refused(
                f"--old occurs {times} times in {path};"
                " it must occur once, so give more of the line around it"
            )
        mutated = text.replace(args.old, args.new).encode()
        if why := _unparsed(path, saved, mutated):
            raise Refused(why)
        try:
            _swap(path, saved, mutated)
            run, output = _pytest(args.pytest_args, args.timeout)
            if run is None:
                print(output, end="" if output.endswith("\n") or not output else "\n")
        finally:
            _swap(path, mutated, saved)
    except Refused as why:
        print(f"mutate.py: refused: {why}", file=sys.stderr)
        return REFUSED
    except Changed as why:
        print(f"mutate.py: stopped: {why}", file=sys.stderr)
        return REFUSED
    finally:
        if lock is not None:
            with _held():
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
    print(f"{outcome}  {mutant}" + (f"  ({why})" if why else ""))
    return 0 if outcome == EXPECTED[args.expect] else 1


if __name__ == "__main__":
    sys.exit(main())
