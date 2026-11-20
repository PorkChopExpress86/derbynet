"""`tools/kill_guard.py` makes `os.kill` and `os.killpg` refuse a process the
test run did not start (#448).

Each process this file signals is a stand-in it started itself, so a mutant
of the guard that lets a call through kills a stand-in and nothing else.
`check()` classifies and never signals, so the calls a mutant would turn on
every process the user may signal, on init or on the test's own parent are
made to it, never to `os.kill`. No test here sends a signal to a pid it did
not start (the `systemd --user` kill of retro-391, memory note
`killpg-checks-target.md`)."""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from tests import outside_process
from tests.outside_process import alive, pid_in, until
from tools import kill_guard
from tools.kill_guard import KillRefused

TOOLS = Path(kill_guard.__file__).resolve().parent


@pytest.fixture
def guard():
    """The guard installed in this process, over a guard that
    `mutate.py`'s run already loaded, if any."""
    kill_guard.install()
    yield kill_guard
    kill_guard.uninstall()


@pytest.fixture
def outside(tmp_path):
    pid = outside_process.start(tmp_path)
    yield pid
    outside_process.stop(pid, tmp_path)


@pytest.fixture
def child():
    """A sleeper of this test, in a session of its own."""
    held = subprocess.Popen(["sleep", "120"], start_new_session=True)
    yield held
    held.kill()
    held.wait(timeout=10)


def test_a_kill_of_the_test_s_own_child_goes_through(guard, child):
    os.kill(child.pid, signal.SIGTERM)
    assert child.wait(timeout=10) == -signal.SIGTERM


def test_a_killpg_of_the_test_s_own_setsid_child_goes_through(guard, child):
    assert os.getpgid(child.pid) == child.pid
    os.killpg(child.pid, signal.SIGTERM)
    assert child.wait(timeout=10) == -signal.SIGTERM


def test_a_child_with_an_environment_of_its_own_is_a_descendant(guard, tmp_path):
    """Its parent chain reaches the test, with no token in its environment."""
    env = {k: v for k, v in os.environ.items() if k != kill_guard.MARK}
    held = subprocess.Popen(["sleep", "120"], env=env)
    os.kill(held.pid, signal.SIGTERM)
    assert held.wait(timeout=10) == -signal.SIGTERM


def test_a_session_parted_from_its_parents_carries_the_runs_token(guard, tmp_path):
    """`setsid -f` leaves the sleeper to the subreaper, as it leaves
    detach.sh's session; the token it inherited is the run's."""
    pid = outside_process.start(tmp_path, mark=guard.State.token)
    try:
        assert os.getppid() != guard.stat(pid)[1]
        os.killpg(pid, signal.SIGTERM)
        until(lambda: not alive(pid))
    finally:
        outside_process.stop(pid, tmp_path)


def test_a_token_of_another_run_is_refused(guard, tmp_path):
    pid = outside_process.start(tmp_path, mark="another run's")
    try:
        with pytest.raises(KillRefused):
            os.kill(pid, signal.SIGTERM)
        assert alive(pid)
    finally:
        outside_process.stop(pid, tmp_path)


def test_the_environment_holds_the_token_only_while_the_guard_is_installed():
    earlier = os.environ.get(kill_guard.MARK)
    kill_guard.install()
    try:
        assert os.environ[kill_guard.MARK] == kill_guard.State.token != earlier
    finally:
        kill_guard.uninstall()
    assert os.environ.get(kill_guard.MARK) == earlier


def test_a_grandchild_is_a_descendant(guard, tmp_path):
    held = subprocess.Popen(
        ["sh", "-c", "sleep 120 & echo $! > g.part; mv g.part grandchild.pid; wait"],
        cwd=tmp_path,
    )
    try:
        grandchild = until(lambda: pid_in(tmp_path / "grandchild.pid"))
        os.kill(grandchild, signal.SIGKILL)
        until(lambda: not alive(grandchild))
    finally:
        held.kill()
        held.wait(timeout=10)


@pytest.mark.parametrize("call", ["kill", "killpg", "negative"])
def test_a_signal_to_a_process_outside_the_tree_is_refused_and_it_lives(
    guard, outside, call
):
    """The `negative` form, `os.kill(-pgid, ...)`, is a group's kill by
    another spelling."""
    send = {
        "kill": lambda: os.kill(outside, signal.SIGTERM),
        "killpg": lambda: os.killpg(outside, signal.SIGTERM),
        "negative": lambda: os.kill(-outside, signal.SIGTERM),
    }[call]
    with pytest.raises(KillRefused) as refused:
        send()
    message = str(refused.value)
    assert f"pid {outside}, comm 'sleep'" in message
    assert f"test_kill_guard.py:{send.__code__.co_firstlineno}" in message
    assert "in <lambda>" in message
    assert alive(outside)


def test_the_refusal_is_no_oserror_for_a_cleanup_to_swallow(guard, outside):
    with pytest.raises(KillRefused) as refused:
        os.kill(outside, signal.SIGTERM)
    assert not isinstance(refused.value, Exception)


def test_signal_0_is_an_existence_check_for_any_pid(guard, outside):
    os.kill(outside, 0)
    os.killpg(outside, 0)
    child = subprocess.Popen(["true"])
    child.wait()
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)


def test_every_process_init_and_the_test_s_parent_are_not_the_test_s(guard):
    for target in (-1, 1, os.getppid()):
        with pytest.raises(KillRefused):
            guard.check("os.kill(...)", target)


def test_pid_0_names_the_callers_own_group_and_negative_pids_a_leader():
    assert kill_guard.leader_of(0) == os.getpgrp()
    assert kill_guard.leader_of(-1) is None
    assert kill_guard.leader_of(-4242) == 4242
    assert kill_guard.leader_of(4242) == 4242


def test_a_process_that_exits_before_the_check_reads_as_gone_and_is_not_signalled(
    guard, monkeypatch, child
):
    read = kill_guard.stat

    def exits_first(pid):
        monkeypatch.setattr(kill_guard, "stat", read)
        subprocess.run(["kill", "-KILL", str(child.pid)], check=True)
        child.wait(timeout=10)
        return read(pid)

    monkeypatch.setattr(kill_guard, "stat", exits_first)
    with pytest.raises(ProcessLookupError, match="kill_guard: pid .* is gone"):
        os.kill(child.pid, signal.SIGTERM)


def test_a_process_that_exits_after_the_check_is_not_signalled_by_its_pid(
    guard, monkeypatch, child
):
    """The signal goes through the pidfd the check's pid was held by, so the
    exit, and a reuse of the pid after it, reach no other process."""

    def never(pid, sig):
        pytest.fail(f"a kill by pid {pid} after the check")

    monkeypatch.setattr(kill_guard.State, "real_kill", never)
    send = signal.pidfd_send_signal

    def exits_first(handle, sig):
        subprocess.run(["kill", "-KILL", str(child.pid)], check=True)
        child.wait(timeout=10)
        return send(handle, sig)

    monkeypatch.setattr(signal, "pidfd_send_signal", exits_first)
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, signal.SIGTERM)


def test_no_descendant_set_outlives_a_call(guard, tmp_path):
    """A child started after a call that killed another is a descendant, and
    the group of the one that died is gone, not the test's."""
    first = subprocess.Popen(["sleep", "120"])
    os.kill(first.pid, signal.SIGKILL)
    first.wait(timeout=10)
    second = subprocess.Popen(["sleep", "120"])
    try:
        os.kill(second.pid, signal.SIGTERM)
        assert second.wait(timeout=10) == -signal.SIGTERM
    finally:
        second.kill()
        second.wait(timeout=10)
    with pytest.raises(ProcessLookupError):
        os.killpg(first.pid, signal.SIGTERM)


def inner_python(tmp_path, body, *args, **popen):
    """A Python that loads the guard from this folder, in a process of its
    own, and runs `body`."""
    script = tmp_path / "inner.py"
    script.write_text(
        f"import os, signal, sys\nsys.path.insert(0, {str(TOOLS)!r})\n"
        f"import kill_guard\nkill_guard.install()\n{body}\n"
    )
    return [sys.executable, str(script), *args]


def test_a_killpg_of_the_parent_s_group_is_refused_and_the_group_lives(tmp_path):
    """The parent here is a bash this test holds, the leader of its group: a
    mutant's `$PPID` and `systemd --user` have the same shape."""
    body = (
        "try:\n"
        "    os.killpg(os.getpgid(os.getppid()), signal.SIGTERM)\n"
        "except BaseException as error:\n"
        "    print(type(error).__name__, error)\n"
    )
    command = " ".join(map(str, inner_python(tmp_path, body)))
    parent = subprocess.Popen(
        ["bash", "-c", f"{command} > out; touch survived; exec sleep 120"],
        cwd=tmp_path,
        start_new_session=True,
    )
    try:
        until(lambda: (tmp_path / "survived").exists())
        said = (tmp_path / "out").read_text()
        assert said.startswith("KillRefused kill_guard refused os.killpg("), said
        assert f"(pid {parent.pid}, comm 'bash')" in said
        assert alive(parent.pid)
    finally:
        parent.kill()
        parent.wait(timeout=10)


INNER_TESTS = """\
import os
import signal
import subprocess
from pathlib import Path


def test_swallows_the_refusal():
    try:
        os.kill(int(Path("outside.pid").read_text()), signal.SIGTERM)
    except BaseException:
        pass


def test_goes_on_after_a_refusal():
    os.killpg(int(Path("outside.pid").read_text()), signal.SIGTERM)
    Path("after").write_text("reached")


def test_signals_its_own_child():
    held = subprocess.Popen(["sleep", "120"], start_new_session=True)
    os.killpg(held.pid, signal.SIGTERM)
    assert held.wait(timeout=10) == -signal.SIGTERM


def test_the_test_after_a_refusal_is_not_failed_for_it():
    held = subprocess.Popen(["sleep", "120"])
    os.kill(held.pid, signal.SIGTERM)
    assert held.wait(timeout=10) == -signal.SIGTERM
"""


def inner_pytest(tmp_path, *args, only):
    """pytest in `tmp_path` under the plugin over the inner tests named by
    `only`: a name, or a tuple of them."""
    names = [only] if isinstance(only, str) else only
    (tmp_path / "test_inner.py").write_text(INNER_TESTS)
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = str(TOOLS)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "kill_guard",
            *args,
            *[f"test_inner.py::{name}" for name in names],
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_test_that_swallows_the_refusal_still_fails_with_its_message(
    tmp_path, outside
):
    (tmp_path / "outside.pid").write_text(str(outside))
    run = inner_pytest(tmp_path, only="test_swallows_the_refusal")
    assert run.returncode == 1, run.stdout
    assert "1 failed" in run.stdout
    assert "kill_guard: os.kill and os.killpg raises for a pid" in run.stdout
    lines = [x for x in run.stdout.splitlines() if x.startswith("kill_guard refused ")]
    assert len(lines) == 1 and f"(pid {outside}, comm 'sleep')" in lines[0], run.stdout
    assert alive(outside)


def test_a_refusal_raises_and_the_test_does_not_go_on(tmp_path, outside):
    (tmp_path / "outside.pid").write_text(str(outside))
    run = inner_pytest(tmp_path, only="test_goes_on_after_a_refusal")
    assert run.returncode == 1, run.stdout
    assert "KillRefused" in run.stdout
    assert not (tmp_path / "after").exists()
    assert alive(outside)


def test_the_print_mode_sends_nothing_raises_nothing_and_lists_the_refusal(
    tmp_path, outside
):
    (tmp_path / "outside.pid").write_text(str(outside))
    run = inner_pytest(
        tmp_path, "--kill-guard-print", only="test_goes_on_after_a_refusal"
    )
    assert (tmp_path / "after").read_text() == "reached"
    assert "KillRefused" not in run.stdout
    assert "kill_guard: os.kill and os.killpg prints for a pid" in run.stdout
    lines = [x for x in run.stdout.splitlines() if x.startswith("kill_guard refused ")]
    assert len(lines) == 1 and f"(pid {outside}, comm 'sleep')" in lines[0], run.stdout
    assert run.returncode == 1, "a refusal printed still fails its test"
    assert alive(outside)


def test_a_test_that_signals_its_own_child_passes_under_the_plugin(tmp_path):
    run = inner_pytest(tmp_path, only="test_signals_its_own_child")
    assert run.returncode == 0, run.stdout
    assert "1 passed" in run.stdout
    assert "kill_guard refused" not in run.stdout


def test_the_test_after_one_that_took_a_refusal_passes(tmp_path, outside):
    (tmp_path / "outside.pid").write_text(str(outside))
    run = inner_pytest(
        tmp_path,
        only=(
            "test_swallows_the_refusal",
            "test_the_test_after_a_refusal_is_not_failed_for_it",
        ),
    )
    assert run.returncode == 1, run.stdout
    assert "1 failed, 1 passed" in run.stdout
    assert alive(outside)


def test_a_signal_leaves_no_descriptor_open(guard, child):
    before = len(os.listdir("/proc/self/fd"))
    os.kill(child.pid, signal.SIGTERM)
    assert len(os.listdir("/proc/self/fd")) == before
