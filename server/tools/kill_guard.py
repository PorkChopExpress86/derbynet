"""A pytest plugin that makes `os.kill` and `os.killpg` refuse a process the
test run did not start (#448).

On 2026-11-17 a mutant of `tools/detach.sh` changed `session $$` to
`session $PPID`. Under `setsid -f` that is `systemd --user`, and a test's
`os.killpg` on the parsed pid ended the owner's desktop session twice. A
mutation round deliberately makes a parsed pid wrong, so every pytest run
`tools/mutate.py` starts loads this plugin (`-p kill_guard`, with this
folder on the child's PYTHONPATH):

- a signal sent to a pid, or to a process group, that is not this pytest
  process or one it started raises KillRefused. A process is the run's when
  this one is among its parents (the chain read from `/proc/<pid>/stat`,
  afresh at each call), or when it carries the run's token in its
  environment (`MARK`). The token covers a process `setsid -f` or a killed
  parent left to the subreaper, which no parent chain reaches:
  `--kill-guard-print` over the tests that signal such processes showed
  tests/test_detach.py's `stop()`, `test_pytest_under_load.py`'s
  `kill_all()`, `test_mutate.py`'s `project` fixture and
  `test_ubuntu_mirror.py`'s dpkg-lock test refused by the chain alone. A
  guard installed over another in the same process, as test_kill_guard.py
  installs one under mutate.py's, shares its token: one run, one token. The
  error is a BaseException, so a cleanup's `except Exception` or
  `suppress(OSError)` does not take it for "already gone". The message
  names the pid, its `comm` and the call site. Signal 0, an existence
  check, passes through;
- the run records each refusal, and the test that took one fails even when
  it swallowed the exception, so a mutant that reaches the guard reads
  KILLED, never SURVIVED (`pytest_runtest_makereport`). The terminal summary
  lists each as a `kill_guard refused ...` line, which `mutate.py` reads;
- `--kill-guard-print` sends nothing and raises nothing: each refusal is
  recorded and listed, so a run shows what the guard would refuse before
  anything signals a real process (memory note `killpg-checks-target.md`).

A group is the run's when its leader is. When the leader is gone the group
may live on, so each of its live processes must be the run's instead; a
group with no process left reads as ProcessLookupError, with nothing
signalled.

The signal goes through a handle opened before the check, the target's
`/proc/<pid>` directory, which `pidfd_send_signal` takes for a pidfd: only
the process the check read, or the group it leads, can receive it, and one
that exited and whose pid was reused is never the target. A group goes
through its leader's handle with `PIDFD_SIGNAL_PROCESS_GROUP`. Two paths
still check and then signal by number, so a group reused in between is a
residual race on them: a kernel older than 6.9, which refuses that flag
(EINVAL), and a group whose leader was gone before its handle was opened.

What it does not cover: a `kill` command a test or a script under test runs
(`subprocess.run(["kill", ...])`), `signal.pthread_kill` and
`signal.pidfd_send_signal`.
"""

from __future__ import annotations

import errno
import functools
import os
import signal
import traceback
import uuid
from pathlib import Path
from typing import NoReturn

import pytest

OPTION = "kill_guard_print"
# Set in the run's environment to a token of its own: a process the run
# started carries it through `setsid -f`, which leaves it no parent in the run.
MARK = "DERBYNET_KILL_GUARD"
# include/uapi/linux/pidfd.h, Linux 6.9.
PIDFD_SIGNAL_PROCESS_GROUP = 4


class KillRefused(BaseException):
    """A signal to a process outside this run; nothing was sent."""


class State:
    """What `install()` changed, and what the run has refused since."""

    token = ""
    earlier_mark = None
    real_kill = None
    real_killpg = None
    installed = False
    print_only = False
    refusals: list[str]
    pending: list[str]


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
    while pid > 0:
        if pid == me:
            return True
        pid = stat(pid)[1]
    return carries_mark(leader)


def leader_of(pid: int) -> int | None:
    """The group a `kill(pid, ...)` with `pid` <= 0 names, by its leader's
    pid: the group `-pid` names, or the caller's own for 0. None for -1,
    which names every process the caller may signal."""
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


def refuse(call: str, pid: int, why: str) -> NoReturn:
    """Records the refusal and raises KillRefused."""
    message = (
        f"kill_guard refused {call} at {call_site()}: {why}"
        f" (pid {pid}, comm {comm_of(pid)!r})"
    )
    State.refusals.append(message)
    State.pending.append(message)
    raise KillRefused(message)


def check(call: str, pid: int) -> None:
    """Returns when `pid` is this run's; raises KillRefused for a process
    not this run's, and ProcessLookupError for one that is gone. Sends
    nothing."""
    try:
        ours = is_ours(pid, os.getpid())
    except OSError as error:
        raise ProcessLookupError(
            errno.ESRCH,
            f"kill_guard: pid {pid} is gone ({error}); nothing was signalled",
        ) from None
    if not ours:
        refuse(
            call,
            pid,
            f"it is neither this pytest run (pid {os.getpid()}) nor a process"
            f" it started (a child, or one that carries its {MARK})",
        )


def members(pgid: int) -> list[int]:
    """The live processes of group `pgid`, from /proc."""
    found = []
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            try:
                if stat(int(entry.name))[2] == pgid:
                    found.append(int(entry.name))
            except OSError:
                pass
    return found


def check_members(call: str, pgid: int) -> None:
    """`check()` of each live process of group `pgid`, whose leader is gone:
    returns when each is this run's, raises KillRefused at the first that is
    not, and ProcessLookupError when none is left. Sends nothing."""
    left = members(pgid)
    if not left:
        raise ProcessLookupError(
            errno.ESRCH,
            f"kill_guard: group {pgid} has no process left; nothing was signalled",
        )
    for pid in left:
        try:
            check(call, pid)
        except ProcessLookupError:
            pass


def hold(call: str, pid: int) -> int | None:
    """A handle on the process `pid`, opened before its check: its /proc
    directory, which `pidfd_send_signal` takes for a pidfd. None when it is
    gone."""
    try:
        return os.open(f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as error:
        refuse(call, pid, f"no handle to hold it by ({error}); not signalled")


def signal_one(call: str, pid: int, sig) -> None:
    handle = hold(call, pid)
    if handle is None:
        raise ProcessLookupError(
            errno.ESRCH, f"kill_guard: pid {pid} is gone; nothing was signalled"
        )
    try:
        check(call, pid)
        signal.pidfd_send_signal(handle, sig)
    finally:
        os.close(handle)


def signal_group(call: str, pgid: int, sig, by_number) -> None:
    """Signals group `pgid` through its leader's handle. `by_number`, the
    real call, signals it instead on a kernel that refuses the flag, or when
    the leader was gone before its handle was opened."""
    handle = hold(call, pgid)
    if handle is None:
        check_members(call, pgid)
        by_number()
        return
    try:
        try:
            check(call, pgid)
        except ProcessLookupError:
            check_members(call, pgid)
        try:
            signal.pidfd_send_signal(handle, sig, None, PIDFD_SIGNAL_PROCESS_GROUP)
        except OSError as error:
            if error.errno != errno.EINVAL:
                raise
            by_number()
    finally:
        os.close(handle)


def signame(sig) -> str:
    try:
        return signal.Signals(sig).name
    except ValueError:
        return str(int(sig))


def printed_only(guarded):
    """`guarded` as `os.kill` or `os.killpg`: it takes the target as an int
    and returns None; in print mode a refusal returns too, unsent."""

    @functools.wraps(guarded)
    def call(target, sig):
        try:
            guarded(int(target), sig)
        except KillRefused:
            if not State.print_only:
                raise

    return call


@printed_only
def guarded_killpg(pgid: int, sig) -> None:
    if int(sig) == 0:
        State.real_killpg(pgid, sig)
        return
    call = f"os.killpg({pgid}, {signame(sig)})"
    signal_group(call, pgid, sig, lambda: State.real_killpg(pgid, sig))


@printed_only
def guarded_kill(pid: int, sig) -> None:
    if int(sig) == 0:
        State.real_kill(pid, sig)
        return
    call = f"os.kill({pid}, {signame(sig)})"
    if pid > 0:
        signal_one(call, pid, sig)
        return
    pgid = leader_of(pid)
    if pgid is None:
        refuse(call, pid, "it names every process, not one of this run's")
    signal_group(call, pgid, sig, lambda: State.real_kill(pid, sig))


def install(print_only: bool = False) -> None:
    if State.installed:
        raise RuntimeError("kill_guard is already installed")
    # A guard installed earlier in this process left its token on os.kill.
    State.token = getattr(os.kill, "run_token", None) or uuid.uuid4().hex
    State.real_kill, State.real_killpg = os.kill, os.killpg
    State.print_only = print_only
    State.refusals, State.pending = [], []
    State.earlier_mark = os.environ.get(MARK)
    os.environ[MARK] = State.token
    guarded_kill.run_token = State.token
    os.kill, os.killpg = guarded_kill, guarded_killpg
    State.installed = True


def uninstall() -> None:
    if State.installed:
        os.kill, os.killpg = State.real_kill, State.real_killpg
        State.installed = False
        if State.earlier_mark is None:
            os.environ.pop(MARK, None)
        else:
            os.environ[MARK] = State.earlier_mark


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
