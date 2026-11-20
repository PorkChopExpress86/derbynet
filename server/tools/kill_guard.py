"""A pytest plugin that makes `os.kill` and `os.killpg` refuse a process the
test run did not start (#448).

On 2026-11-17 a mutant of `tools/detach.sh` changed `session $$` to
`session $PPID`. Under `setsid -f` that is `systemd --user`, and a test's
`os.killpg` on the parsed pid ended the owner's desktop session twice. A
mutation round deliberately makes a parsed pid wrong, so every pytest run
`tools/mutate.py` starts loads this plugin (`-p kill_guard`, with this
folder on the child's PYTHONPATH):

- a signal sent to a pid, or a process group's leader, that is not this
  pytest process or one it started raises KillRefused. A process is the
  run's when this one is among its parents (the chain read from
  `/proc/<pid>/stat`, afresh at each call), or when it carries the run's
  token in its environment (`MARK`). The token covers a process `setsid -f`
  or a killed parent left to the subreaper, which no parent chain reaches:
  `--kill-guard-print` over the tests that signal such processes showed
  tests/test_detach.py's `stop()`, `test_pytest_under_load.py`'s
  `kill_all()`, `test_mutate.py`'s `project` fixture and
  `test_ubuntu_mirror.py`'s dpkg-lock test refused by the chain alone. The
  error is a BaseException, so a cleanup's `except Exception` or `suppress(OSError)`
  does not take it for "already gone". The message names the pid, its
  `comm` and the call site. Signal 0, an existence check, passes through;
- the run records each refusal, and the test that took one fails even when
  it swallowed the exception, so a mutant that reaches the guard reads
  KILLED, never SURVIVED (`pytest_runtest_makereport`). The terminal summary
  lists each as a `kill_guard refused ...` line, which `mutate.py` reads;
- `--kill-guard-print` sends nothing and raises nothing: each refusal is
  recorded and listed, so a run shows what the guard would refuse before
  anything signals a real process (memory note `killpg-checks-target.md`).

A pid that exits before the check reads as ProcessLookupError, with
nothing signalled. For `os.kill` of one pid the signal goes through a handle
opened before the check (`pidfd_send_signal`), so a pid reused after the check
is never the target. A process group has no such handle: the leader is checked and the
group is signalled after, so a group that is reused in between is a
residual race this does not close.

What it does not cover: a `kill` command a test or a script under test runs
(`subprocess.run(["kill", ...])`), `signal.pthread_kill` and
`signal.pidfd_send_signal`.
"""

from __future__ import annotations

import errno
import os
import signal
import traceback
import uuid
from pathlib import Path

import pytest

OPTION = "kill_guard_print"
# Set in the run's environment to a token of its own: a process the run
# started carries it through `setsid -f`, which leaves it no parent in the run.
MARK = "DERBYNET_KILL_GUARD"


class KillRefused(BaseException):
    """A signal to a process outside this run; nothing was sent."""


class State:
    """What `install()` changed, and what the run has refused."""

    original: tuple = ()
    token = ""
    earlier_mark = None
    real_kill = None
    real_killpg = None
    installed = False
    print_only = False
    refusals: list[str] = []
    pending: list[str] = []


def stat(pid: int) -> tuple[str, int, int]:
    """`comm`, parent pid and process group of a pid, from /proc; OSError
    when it is gone. Read afresh at each call, so no process is taken for a
    descendant that was one a moment ago."""
    text = Path(f"/proc/{pid}/stat").read_text()
    comm = text[text.index("(") + 1 : text.rindex(")")]
    fields = text[text.rindex(")") + 2 :].split()
    return comm, int(fields[1]), int(fields[2])


def comm_of(pid: int) -> str:
    try:
        return stat(pid)[0]
    except OSError:
        return "?"


def carries_mark(pid: int) -> bool:
    """Whether the process was started with this run's token in its
    environment, as every process the run started is unless the test gave it
    an environment of its own. An unreadable environment is not the run's."""
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return False
    return f"{MARK}={State.token}".encode() in environ


def is_ours(pid: int, me: int) -> bool:
    """Whether `pid` is `me`, has `me` among its parents, or carries the
    run's mark (a process `setsid -f` parted from its parents). Raises
    OSError when a process on the way is gone: that reads as not provable,
    and the caller says so."""
    leader = pid
    seen = set()
    while pid > 0 and pid not in seen:
        if pid == me:
            return True
        seen.add(pid)
        pid = stat(pid)[1]
    return carries_mark(leader)


def leader_of(pid: int) -> int | None:
    """The pid whose ownership a `kill(pid, ...)` depends on: the pid, the
    leader of the group `-pid` names, or the caller's own group for 0. None
    for -1, which names every process the caller may signal."""
    if pid == -1:
        return None
    if pid == 0:
        return os.getpgrp()
    return abs(pid)


def call_site() -> str:
    for frame in reversed(traceback.extract_stack()):
        if Path(frame.filename) != Path(__file__):
            return f"{frame.filename}:{frame.lineno} in {frame.name}"
    return "?"


def refuse(call: str, pid: int, why: str) -> None:
    """Records the refusal, and raises KillRefused unless the run only
    prints."""
    message = (
        f"kill_guard refused {call} at {call_site()}: {why}"
        f" (pid {pid}, comm {comm_of(pid)!r})"
    )
    State.refusals.append(message)
    State.pending.append(message)
    if not State.print_only:
        raise KillRefused(message)


def check(call: str, target: int) -> bool:
    """Whether the signal may go on: True, or False for a refusal printed
    only. Raises KillRefused for a process not this run's, and
    ProcessLookupError for one that is gone."""
    leader = leader_of(target)
    if leader is None:
        refuse(call, target, "it names every process, not one of this run's")
        return False
    try:
        if is_ours(leader, os.getpid()):
            return True
    except OSError as error:
        raise ProcessLookupError(
            errno.ESRCH,
            f"kill_guard: pid {leader} is gone ({error}); nothing was signalled",
        ) from None
    refuse(
        call,
        leader,
        f"it is neither this pytest run (pid {os.getpid()}) nor a process"
        f" it started (a child, or one that carries its {MARK})",
    )
    return False


def signame(sig) -> str:
    try:
        return signal.Signals(sig).name
    except ValueError:
        return str(int(sig))


def guarded_killpg(pgid, sig):
    if int(sig) == 0 or check(f"os.killpg({pgid}, {signame(sig)})", int(pgid)):
        return State.real_killpg(pgid, sig)


def guarded_kill(pid, sig):
    """`os.kill`. A single pid is held through a handle opened before the
    check, the pid's /proc directory, which `pidfd_send_signal` takes for a
    pidfd (the conda Python here has no `os.pidfd_open`): only the process
    the check read can receive the signal, and one that exited and whose pid
    was reused gives ESRCH."""
    pid = int(pid)
    if int(sig) == 0:
        return State.real_kill(pid, sig)
    call = f"os.kill({pid}, {signame(sig)})"
    if pid <= 0:
        if check(call, pid):
            return State.real_kill(pid, sig)
        return None
    try:
        handle = os.open(f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError:
        raise ProcessLookupError(
            errno.ESRCH, f"kill_guard: pid {pid} is gone; nothing was signalled"
        ) from None
    except OSError as error:
        refuse(call, pid, f"no handle to hold it by ({error}); not signalled")
        return None
    try:
        if check(call, pid):
            signal.pidfd_send_signal(handle, sig)
    finally:
        os.close(handle)
    return None


def install(print_only: bool = False) -> None:
    if State.installed:
        raise RuntimeError("kill_guard is already installed")
    State.original = os.kill, os.killpg
    State.real_kill, State.real_killpg = State.original
    State.print_only = print_only
    State.token = uuid.uuid4().hex
    State.earlier_mark = os.environ.get(MARK)
    os.environ[MARK] = State.token
    os.kill, os.killpg = guarded_kill, guarded_killpg
    State.installed = True


def uninstall() -> None:
    if State.installed:
        os.kill, os.killpg = State.original
        State.installed = False
        if State.earlier_mark is None:
            os.environ.pop(MARK, None)
        else:
            os.environ[MARK] = State.earlier_mark
    State.refusals.clear()
    State.pending.clear()


def pytest_addoption(parser):
    parser.addoption(
        "--kill-guard-print",
        action="store_true",
        dest=OPTION,
        help="kill_guard: send nothing and raise nothing; list each refusal",
    )


def pytest_configure(config):
    install(print_only=bool(config.getoption(OPTION)))


def pytest_unconfigure(config):
    uninstall()


def pytest_report_header(config):
    how = "prints" if State.print_only else "raises"
    return f"kill_guard: os.kill and os.killpg {how} for a pid outside this run"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """A test that took a refusal fails, whatever it did with the exception;
    a refusal in a fixture's teardown fails that phase."""
    report = yield
    taken = list(State.pending)
    State.pending.clear()
    if taken and report.passed:
        report.outcome = "failed"
        report.longrepr = "\n".join(f"  {message}" for message in taken)
    return report


def pytest_terminal_summary(terminalreporter):
    for message in State.refusals:
        terminalreporter.write_line(message)
