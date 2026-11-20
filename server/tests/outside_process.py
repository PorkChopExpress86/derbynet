"""A process a test starts that lies outside its tree, as `systemd --user`
lies outside a mutant's (#448). Tests of `tools/kill_guard.py` and of
`tools/mutate.py` signal it only to see the guard refuse, and read that it
lives."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from tools import kill_guard

# `setsid -f` forks and returns at once, so the sleeper's parent is gone and
# the subreaper (init, or `systemd --user`) takes it. The pid is moved into
# place whole.
SCRIPT = "echo $$ > outside.part && mv outside.part outside.pid; exec sleep 120"


def until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not (value := predicate()):
        assert time.monotonic() < deadline, "gave up waiting"
        time.sleep(0.02)
    return value


def pid_in(path):
    """The pid a script wrote whole to `path`, or None until it has."""
    return int(path.read_text()) if path.exists() else None


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except OSError:
        return False


def start(folder, mark=None):
    """The pid of a sleeper started in `folder`, in a session of its own; its
    pid is also in `folder/outside.pid`. Its environment holds no guard
    token, unless `mark` gives one."""
    env = {k: v for k, v in os.environ.items() if k != kill_guard.MARK}
    if mark is not None:
        env[kill_guard.MARK] = mark
    subprocess.run(
        ["setsid", "-f", "sh", "-c", SCRIPT], cwd=folder, env=env, check=True
    )
    pid = until(lambda: pid_in(folder / "outside.pid"))
    # The shell writes its pid before it execs: until then its comm is `sh`.
    until(lambda: Path(f"/proc/{pid}/comm").read_text().strip() == "sleep")
    return pid


def stop(pid, folder):
    """`kill PID` by the command, which the guard does not wrap, once the pid
    is shown to be the sleeper started in `folder`."""
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return
    assert comm == "sleep" and cwd == os.path.realpath(folder), (pid, comm, cwd)
    subprocess.run(["kill", "-KILL", str(pid)], check=True)


@pytest.fixture
def outside(tmp_path):
    """A sleeper outside the test's tree, started in `tmp_path`."""
    pid = start(tmp_path)
    yield pid
    stop(pid, tmp_path)
