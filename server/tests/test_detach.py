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
once the test writes `go` beside it, so cleanup signals no pid the script
named: it kills only the test's own Popen children."""

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

# Returns once `go` exists beside it, or after 30 s.
HOLD = "for _ in $(seq 300); do [ -e go ] && break; sleep 0.1; done"

# Ends 0 once `go` exists beside it, or 1 after the same 30 s.
RELEASE = f"{HOLD}; [ -e go ] && exit 0; exit 1"

# Writes its own session and process group ids to `ids`, whole, then waits
# for `go`.
HELD = f"ps -o sid=,pgid= -p $$ > ids.part && mv ids.part ids; {RELEASE}"


def env(tmp_path):
    # The script's git call reads no hook's GIT_DIR (#351 retro).
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**clean, MARK: str(tmp_path)}


def detach(tmp_path, *command, script=SCRIPT, path=None):
    return subprocess.run(
        [str(script), str(tmp_path / "run.log"), *command],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env(tmp_path) | ({"PATH": path} if path else {}),
        timeout=30,
    )


def start_line(leader, where, log):
    """The line the start form prints, as its header gives it: the session,
    where it runs, and the commands that wait on it and stop it."""
    return (
        f"session {leader}{where}: wait with `{SCRIPT} --wait {log}`;"
        f" stop it with `kill -TERM -- -{leader}`\n"
    )


def logged(tmp_path, key, seconds=10):
    """The value of the log's `key` header line, once the `--run` shell has
    written it. The start form returns as soon as the `session` line is
    there, while the rest of the header may still be on its way: CI read
    the log before its `cgroup` line once (#412)."""
    log = tmp_path / "run.log"
    lines = until(
        lambda: [x for x in log.read_text().splitlines() if x.startswith(f"{key} ")],
        seconds,
        f"no `{key}` line in {log} after {seconds} s",
    )
    return lines[0].removeprefix(f"{key} ")


def stubs(tmp_path, **scripts):
    """A PATH whose first folder holds each named stub, ahead of the real
    one."""
    folder = tmp_path / "stubs"
    folder.mkdir()
    for name, body in scripts.items():
        (folder / name).write_text(f"#!/bin/sh\n{body}\n")
        (folder / name).chmod(0o755)
    return f"{folder}:{os.environ['PATH']}"


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


def until(predicate, seconds=10, message=""):
    """The first truthy value `predicate` returns, or a failure with `message`
    after `seconds`."""
    deadline = time.monotonic() + seconds
    while not (value := predicate()):
        assert time.monotonic() < deadline, message
        time.sleep(0.05)
    return value


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


def test_a_header_line_written_after_the_start_form_returns_is_still_read(
    tmp_path,
):
    """A copy whose `--run` shell holds its `cgroup` line until `go` exists:
    the start form has returned with that line not yet in the log, and
    `logged()` still reads it once it comes (#412)."""
    args_line = '  echo "args $*"\n'
    copy = copy_with(
        tmp_path,
        args_line,
        args_line + f"  {HOLD}\n",
    )
    try:
        started = detach(tmp_path, "true", script=copy)
        assert started.returncode == 0, started
        lines = (tmp_path / "run.log").read_text().splitlines()
        assert not [x for x in lines if x.startswith("cgroup ")], lines
    finally:
        release(tmp_path)
    assert logged(tmp_path, "cgroup").startswith("/")
    ended = wait(tmp_path, "20")
    assert (ended.returncode, ended.stdout) == (0, "ended: exit 0\n")


def test_a_header_line_that_never_comes_fails_naming_the_key_and_the_log(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("session 1\n")
    start = time.monotonic()
    with pytest.raises(AssertionError) as failed:
        logged(tmp_path, "cgroup", seconds=0.2)
    assert time.monotonic() - start < 5, "gave up after its own `seconds`"
    message = str(failed.value)
    assert message.startswith(f"no `cgroup` line in {log} after 0.2 s"), message


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
    [
        [],
        ["only.log"],
        ["--wait"],
        ["--wait", "a", "1", "b"],
        ["--wait", "a", "x"],
        # A leading 0 reads as octal: `08` dropped the deadline (#417 retro).
        ["--wait", "a", "08"],
    ],
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
        # Ubuntu 24.04's bash 5.2 may print the job's `[1]+ Done` line
        # first.
        assert [x for x in out.splitlines() if x.startswith("bang ")] == [
            "bang gone"
        ], out
        ours(leader, tmp_path)
        running = wait(tmp_path)
        assert running.stdout == f"running: session {leader}\n", running
    finally:
        release(tmp_path)


NO_MANAGER = (
    "detach.sh: no user systemd manager: COMMAND stops with this shell's"
    " scope, as in a Claude Code restart.\n"
)


def test_with_no_user_manager_it_says_so_and_names_no_scope(tmp_path):
    """With no user systemd manager, as in a container, `systemctl --user`
    fails; a stub that fails stands in for it. The script says so, names no
    scope on its start line, and COMMAND runs in the starter's own
    cgroup."""
    started = detach(
        tmp_path, "bash", "-c", HELD, path=stubs(tmp_path, systemctl="exit 1")
    )
    try:
        leader = session(started)
        assert (started.returncode, started.stdout, started.stderr) == (
            0,
            start_line(leader, ", with no scope of its own", tmp_path / "run.log"),
            NO_MANAGER,
        )
        own = Path("/proc/self/cgroup").read_text().strip().split(":", 2)[2]
        assert logged(tmp_path, "cgroup") == own
    finally:
        release(tmp_path)
    ended = wait(tmp_path, "20")
    assert (ended.returncode, ended.stdout) == (0, "ended: exit 0\n")


def test_a_copy_under_a_longer_name_still_finds_its_session(tmp_path):
    """comm holds the first 15 characters of a longer name, and the start
    form's check compares that much of the script's name."""
    copy = tmp_path / "detach-under-a-long-name.sh"
    copy.write_text(SCRIPT.read_text())
    copy.chmod(0o755)
    try:
        started = detach(tmp_path, "bash", "-c", HELD, script=copy)
        assert started.returncode == 0, started
        comm = Path(f"/proc/{session(started)}/comm").read_text()
    finally:
        release(tmp_path)
    assert comm == copy.name[:15] + "\n"


def user_systemd(timeout=10):
    """Whether a user systemd manager answers within `timeout` seconds. A
    bus that hangs reads as none, since this runs as the file is collected,
    in every server pytest run."""
    if not shutil.which("systemd-run"):
        return False
    try:
        answer = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False
    return answer.returncode == 0


def test_a_user_bus_that_hangs_reads_as_no_manager(tmp_path, monkeypatch):
    """The stub systemctl outlasts the bound and would then exit 0, so only
    the timeout makes the answer False, and well before the stub ends."""
    monkeypatch.setenv(
        "PATH", stubs(tmp_path, systemctl="exec sleep 20", **{"systemd-run": ""})
    )
    began = time.monotonic()
    assert user_systemd(timeout=0.5) is False
    assert time.monotonic() - began < 10


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
            f"'{SCRIPT}' run.log bash -c '{HELD}' > started 2> started.err;"
            " exec sleep 30",
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
        cgroup = logged(tmp_path, "cgroup")
        assert re.fullmatch(r"/.*/derbynet-detach-\d+-\d+\.scope", cgroup), cgroup
        # The start line names the unit COMMAND runs in, with no word on
        # stderr.
        assert (tmp_path / "started").read_text() == start_line(
            leader, f" in {cgroup.rsplit('/', 1)[1]}", "run.log"
        )
        assert (tmp_path / "started.err").read_text() == ""
        assert wait(tmp_path).stdout == f"running: session {leader}\n"
    finally:
        release(tmp_path)
        if starter.poll() is None:
            starter.kill()
            starter.wait(timeout=20)
    ended = wait(tmp_path, "20")
    assert (ended.returncode, ended.stdout) == (0, "ended: exit 0\n")
