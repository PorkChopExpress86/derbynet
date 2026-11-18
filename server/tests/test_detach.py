"""`tools/detach.sh` starts a long command in a session of its own and
names the pid that lives until it ends. `setsid CMD &` then `$!` named a
pid that had already exited: #391's implementer reported one for its
queued hand-off, and the lead's #367 waiter said "chain ended" on one while
the chain still waited for the load lock (#391 retro).

No test here signals a pid it read from the script's output unless
`ours()` has shown it is a session the test itself started. A mutant of
the script that named `$PPID` on its session line named systemd --user,
and the unguarded `os.killpg` this file once ran on it ended the owner's
desktop session twice on 2026-11-17. Every held command ends by itself
once the test writes `go` beside it, so cleanup sends no signal."""

import fcntl
import os
import re
import shutil
import signal
import subprocess
import termios
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "detach.sh"

# Every process a test starts carries this variable, set to the test's own
# folder, so `ours()` can tell its sessions from any other process.
MARK = "DERBYNET_DETACH_TEST"

# Ends 0 once `go` exists beside it, or 1 after 30 s.
RELEASE = "for _ in $(seq 300); do [ -e go ] && exit 0; sleep 0.1; done; exit 1"

# Writes its own session and process group ids to `ids`, whole, then waits
# for `go`.
HELD = f"ps -o sid=,pgid= -p $$ > ids.part && mv ids.part ids; {RELEASE}"


def env(tmp_path):
    # The script's git call reads no hook's GIT_DIR (#351 retro).
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**clean, MARK: str(tmp_path)}


def detach(tmp_path, *command, script=SCRIPT):
    return subprocess.run(
        [str(script), str(tmp_path / "run.log"), *command],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env(tmp_path),
        timeout=30,
    )


def wait(tmp_path, seconds="0"):
    return subprocess.run(
        [str(SCRIPT), "--wait", str(tmp_path / "run.log"), seconds],
        capture_output=True,
        text=True,
        env=env(tmp_path),
        timeout=30,
    )


def session(result):
    out = result if isinstance(result, str) else result.stdout
    found = re.match(r"session (\d+)[:, ]", out)
    assert found, result
    return int(found[1])


def ours(pid, tmp_path):
    """Raises AssertionError, so no signal follows, unless `pid` is alive,
    leads its own session and process group as the script's `--run` shell,
    and carries this test's MARK and folder. The session's parent is a
    subreaper, not this test, so no walk up the parents reaches the test:
    the environment and working directory it inherited from the test's own
    `subprocess` call are the proof."""
    proc = Path(f"/proc/{pid}")
    try:
        stat = (proc / "stat").read_text()
        environ = (proc / "environ").read_bytes().split(b"\0")
        cwd = os.readlink(proc / "cwd")
    except OSError as error:
        raise AssertionError(f"pid {pid}: {error}") from error
    comm = stat[stat.index("(") + 1 : stat.rindex(")")]
    pgid, sid = (int(x) for x in stat[stat.rindex(")") + 2 :].split()[2:4])
    assert comm == "detach.sh", (pid, comm)
    assert sid == pgid == pid, (pid, pgid, sid)
    assert f"{MARK}={tmp_path}".encode() in environ, pid
    assert cwd == os.path.realpath(tmp_path), (pid, cwd)


def stop(pid, tmp_path):
    """`kill -TERM -- -PID`, the stop the script prints, once `ours()` has
    passed: the only signal this file sends to a pid the script named."""
    ours(pid, tmp_path)
    os.killpg(pid, signal.SIGTERM)


def decoy(tmp_path, name="detach.sh", new_session=True, mark=True, cwd="."):
    """A process this test holds by its Popen handle, which differs from a
    detach.sh session only where the arguments say: its script's name (its
    comm), its session, its MARK, its working directory."""
    script = tmp_path / "decoys" / name
    script.parent.mkdir(exist_ok=True)
    # exec sets the new comm before the new environment: one read here saw
    # comm detach.sh beside an empty environ. So the script itself says
    # when it runs.
    script.write_text(f"#!/bin/bash\n: > '{tmp_path}/ready'\n{RELEASE}\n")
    script.chmod(0o755)
    (tmp_path / cwd).mkdir(exist_ok=True)
    held = subprocess.Popen(
        [str(script)],
        cwd=tmp_path / cwd,
        env=env(tmp_path) if mark else env(tmp_path) | {MARK: "another test"},
        start_new_session=new_session,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    until(lambda: (tmp_path / "ready").exists())
    return held


def test_ours_takes_a_session_the_test_started_and_marked(tmp_path):
    """The twin of the decoys below: a process like them in every way but
    the one each changes passes."""
    held = decoy(tmp_path)
    try:
        ours(held.pid, tmp_path)
    finally:
        held.kill()
        held.wait(timeout=10)


@pytest.mark.parametrize(
    "differs",
    [
        pytest.param({"new_session": False}, id="session"),
        pytest.param({"name": "other.sh"}, id="comm"),
        pytest.param({"mark": False}, id="mark"),
        pytest.param({"cwd": "elsewhere"}, id="cwd"),
    ],
)
def test_stop_sends_no_signal_to_a_process_ours_does_not_take(tmp_path, differs):
    held = decoy(tmp_path, **differs)
    try:
        with pytest.raises(AssertionError):
            stop(held.pid, tmp_path)
        with pytest.raises(subprocess.TimeoutExpired):
            held.wait(timeout=0.5)
    finally:
        held.kill()
        held.wait(timeout=10)


def test_ours_refuses_init_and_the_test_itself(tmp_path):
    for pid in (1, os.getpid()):
        with pytest.raises(AssertionError):
            ours(pid, tmp_path)


def release(tmp_path):
    (tmp_path / "go").touch()


def until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.05)


def ids_written(tmp_path):
    return (tmp_path / "ids").exists()


def test_the_session_it_names_is_the_command_s_and_lives_until_it_ends(tmp_path):
    started = detach(tmp_path, "bash", "-c", HELD)
    try:
        assert started.returncode == 0, started
        leader = session(started)
        ours(leader, tmp_path)
        until(lambda: ids_written(tmp_path))
        assert (tmp_path / "ids").read_text().split() == [str(leader)] * 2
        running = wait(tmp_path)
        assert (running.returncode, running.stdout) == (
            3,
            f"running: session {leader}\n",
        )
    finally:
        release(tmp_path)
    ended = wait(tmp_path, "20")
    assert (ended.returncode, ended.stdout) == (0, "ended: exit 0\n")


def test_the_log_holds_a_header_the_output_and_the_command_s_status(tmp_path):
    started = detach(tmp_path, "bash", "-c", "echo out; echo err >&2; exit 7")
    leader = session(started)
    ended = wait(tmp_path, "20")
    assert (ended.returncode, ended.stdout) == (1, "ended: exit 7\n")
    lines = (tmp_path / "run.log").read_text().splitlines()
    assert lines[0] == f"session {leader}"
    assert lines[1] == f"cwd {tmp_path}"
    assert re.fullmatch(r"head ([0-9a-f]{40}|unknown)", lines[2]), lines
    assert lines[3] == "args bash -c echo out; echo err >&2; exit 7"
    assert lines[4].startswith("cgroup /"), lines
    assert lines[5:] == ["out", "err", "detach.sh: exit 7"]


def test_a_stopped_session_is_reported_dead_not_ended(tmp_path):
    """The #367 waiter's mistake: a gone pid read as a finished chain."""
    try:
        leader = session(detach(tmp_path, "bash", "-c", HELD))
        until(lambda: ids_written(tmp_path))
        stop(leader, tmp_path)
        died = wait(tmp_path, "20")
    finally:
        release(tmp_path)
    assert died.returncode == 4, died
    assert died.stdout == (
        f"died: session {leader} is gone and {tmp_path / 'run.log'} has no exit line\n"
    )


def reaped():
    """A pid no process holds: a child this test started and reaped."""
    child = subprocess.Popen(["true"])
    child.wait(timeout=10)
    return str(child.pid)


def copy_with(tmp_path, old, new):
    """The script, copied beside the test with `old` replaced by `new`."""
    text = SCRIPT.read_text()
    assert text.count(old) == 1
    copy = tmp_path / "detach.sh"
    copy.write_text(text.replace(old, new))
    copy.chmod(0o755)
    return copy


@pytest.mark.parametrize(
    "line, command",
    [
        # The mutant that ended the desktop session: the subreaper that took
        # the session's parent's place (systemd --user here), or that parent.
        pytest.param(lambda: 'echo "session $PPID"', HELD, id="parent"),
        # A gone pid while COMMAND still runs, so the log has no exit line.
        pytest.param(lambda: f'echo "session {reaped()}"', HELD, id="gone"),
        # A pid another process holds, whether or not COMMAND has ended.
        pytest.param(lambda: 'echo "session 1"', "true", id="another"),
        # A child of the session leader: the script's name, the leader's
        # session, a pid of its own.
        pytest.param(lambda: f'( {RELEASE} ) & echo "session $!"', HELD, id="child"),
    ],
)
def test_a_session_line_naming_another_process_prints_no_pid(tmp_path, line, command):
    copy = copy_with(tmp_path, 'echo "session $$"', line())
    try:
        refused = detach(tmp_path, "bash", "-c", command, script=copy)
    finally:
        release(tmp_path)
    assert (refused.returncode, refused.stdout) == (1, ""), refused
    assert "which is not the session leader this script started" in refused.stderr


def test_a_command_that_ended_before_the_check_still_gets_its_pid(tmp_path):
    """The start form's check comes once the log has COMMAND's exit line and
    the leader has had 1 s to be reaped: a gone leader with its exit line
    still passes."""
    started_line = 'setsid -f "${scope[@]}" "$0" --run "$@" >>"$log" 2>&1 </dev/null\n'
    copy = copy_with(
        tmp_path,
        started_line,
        started_line
        + "for _ in $(seq 100); do\n"
        + '  case "$(tail -n 1 "$log")" in "detach.sh: exit "*) break ;; esac\n'
        + "  sleep 0.1\n"
        + "done\n"
        + "sleep 1\n",
    )
    started = detach(tmp_path, "true", script=copy)
    assert started.returncode == 0, started
    leader = session(started)
    assert not Path(f"/proc/{leader}").exists()
    assert (tmp_path / "run.log").read_text().splitlines()[-1] == "detach.sh: exit 0"


def test_a_log_that_exists_is_refused_and_left_as_it_is(tmp_path):
    (tmp_path / "run.log").write_text("detach.sh: exit 0\n")
    refused = detach(tmp_path, "touch", "ran")
    assert refused.returncode == 2
    assert "exists or cannot be written" in refused.stderr
    assert (tmp_path / "run.log").read_text() == "detach.sh: exit 0\n"
    time.sleep(0.5)
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize(
    "args",
    [[], ["only.log"], ["--wait"], ["--wait", "a", "1", "b"], ["--wait", "a", "x"]],
)
def test_a_wrong_command_line_prints_the_usage(tmp_path, args):
    result = subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, cwd=tmp_path, timeout=30
    )
    assert result.returncode == 2
    assert result.stderr.startswith("usage: detach.sh LOG COMMAND")


def test_a_wait_on_a_log_it_did_not_start_is_refused(tmp_path):
    (tmp_path / "run.log").write_text("pytest exit 0\n")
    result = wait(tmp_path)
    assert result.returncode == 2
    assert "does not start with a detach.sh session line" in result.stderr


def job_control_bash(tmp_path, command):
    """bash -m on a terminal of its own, as the Bash tool's shell has job
    control on; `command`'s output goes to `out`."""
    try:
        controller, terminal = os.openpty()
    except OSError:
        pytest.skip("no pseudo-terminal")
    try:
        subprocess.run(
            [
                "bash",
                "--norc",
                "--noprofile",
                "-m",
                "-c",
                f"{{ {command}; }} >out 2>&1",
            ],
            stdin=terminal,
            stdout=terminal,
            stderr=terminal,
            cwd=tmp_path,
            env=env(tmp_path),
            start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
            timeout=30,
        )
    finally:
        os.close(terminal)
        os.close(controller)
    return (tmp_path / "out").read_text()


def test_under_job_control_the_pid_setsid_leaves_in_bang_is_gone_and_its_is_not(
    tmp_path,
):
    """The header's reason for the script, in a shell like the Bash tool's:
    `$!` of `setsid CMD &` has exited 0.3 s later, while the session
    detach.sh names from the same shell is alive."""
    try:
        out = job_control_bash(
            tmp_path,
            f"echo flags $-; setsid bash -c '{RELEASE}' & p=$!; "
            "sleep 0.3; "
            "kill -0 $p 2>/dev/null && echo bang alive || echo bang gone; "
            f"'{SCRIPT}' run.log bash -c '{HELD}'",
        )
        leader = session(next(x for x in out.splitlines() if x.startswith("session ")))
        assert "m" in out.splitlines()[0].removeprefix("flags "), out
        assert out.splitlines()[1] == "bang gone", out
        ours(leader, tmp_path)
        running = wait(tmp_path)
        assert running.stdout == f"running: session {leader}\n", running
    finally:
        release(tmp_path)


def user_systemd():
    return (
        shutil.which("systemd-run")
        and subprocess.run(
            ["systemctl", "--user", "show-environment"], capture_output=True
        ).returncode
        == 0
    )


@pytest.mark.skipif(not user_systemd(), reason="no user systemd manager")
def test_a_command_outlives_the_scope_of_the_shell_that_started_it(tmp_path):
    """The Claude Code restart of 2026-11-17 stopped the editor's systemd
    scope, and every setsid session in it with it. Here the starter runs in
    a scope of its own, named for this test alone, which the test stops
    once the command has started: the starter's own sleep ends with it, and
    the command runs on in the scope the script made for it."""
    unit = f"derbynet-detach-test-{os.getpid()}-{time.time_ns()}.scope"
    starter = subprocess.Popen(
        [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            "--expand-environment=no",
            f"--unit={unit}",
            "bash",
            "-c",
            f"'{SCRIPT}' run.log bash -c '{HELD}' > started; exec sleep 30",
        ],
        cwd=tmp_path,
        env=env(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        until(lambda: ids_written(tmp_path))
        until(lambda: (tmp_path / "started").read_text())
        leader = session((tmp_path / "started").read_text())
        # systemd-run execs bash in its own process, so the Popen pid is the
        # starter, and the unit about to be stopped is the one it runs in.
        cgroup = Path(f"/proc/{starter.pid}/cgroup").read_text().strip()
        assert cgroup.endswith(f"/{unit}"), cgroup
        subprocess.run(["systemctl", "--user", "stop", unit], check=True, timeout=30)
        assert starter.wait(timeout=20) != 0
        header = (tmp_path / "run.log").read_text().splitlines()[4]
        assert re.fullmatch(r"cgroup /.*/derbynet-detach-\d+-\d+\.scope", header)
        assert wait(tmp_path).stdout == f"running: session {leader}\n"
    finally:
        release(tmp_path)
        if starter.poll() is None:
            starter.kill()
            starter.wait(timeout=20)
    ended = wait(tmp_path, "20")
    assert (ended.returncode, ended.stdout) == (0, "ended: exit 0\n")
