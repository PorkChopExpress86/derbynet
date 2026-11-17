"""`tools/pytest-under-load.sh` refuses a pipe and a `timeout` before it
starts anything: #47 and #48 each lost a 25-repeat run to
`timeout 3000 ... | grep` (#48 retro). It also refuses an output folder
that already holds files: #220 reused one, and a wait for its `junit.xml`
fired on an earlier session's results (#220 retro). A run holds a lock
for the machine, a second waits for it, and a SIGKILL frees it (#316
retro)."""

import fcntl
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "pytest-under-load.sh"


def run(
    command: str, out: str = "/nonexistent", lock: str | None = None
) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "DERBYNET_LOAD_RUN_DIR": out}
    if lock:
        env["DERBYNET_LOAD_LOCK"] = lock
    return subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, timeout=60, env=env
    )


def test_a_pipe_is_refused():
    result = run(f"'{SCRIPT}' --loops 0 tests/test_pytest_under_load.py | cat")
    assert "its output is a pipe" in result.stderr
    assert result.stdout == ""


@pytest.mark.skipif(
    not (shutil.which("timeout") and shutil.which("ps")), reason="no timeout or ps"
)
def test_a_timeout_is_refused():
    # Output to a file, as the Bash tool's run_in_background sends it.
    result = run(
        f"timeout 50 '{SCRIPT}' --loops 0 tests/test_pytest_under_load.py >/dev/null"
    )
    assert result.returncode == 2
    assert "it runs under timeout" in result.stderr


def test_an_output_folder_with_files_is_refused(tmp_path):
    (tmp_path / "junit.xml").write_text("an earlier run's results")
    # Output to a file, as the Bash tool's run_in_background sends it.
    result = run(
        f"'{SCRIPT}' --loops 0 tests/test_pytest_under_load.py >/dev/null",
        out=str(tmp_path),
    )
    assert result.returncode == 2
    assert "already holds files" in result.stderr
    assert (tmp_path / "junit.xml").read_text() == "an earlier run's results"
    assert not (tmp_path / "pytest.log").exists()


def test_the_summary_names_an_error_as_well_as_a_failure(tmp_path):
    """#220's retro run ended "1 error" with no test named: `-rf` lists only
    failures, and the error was a teardown (#220 retro)."""
    (tmp_path / "test_breaks.py").write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\ndef broken():\n    raise RuntimeError\n\n\n"
        "def test_errors_at_setup(broken):\n    pass\n\n\n"
        "def test_fails():\n    assert False\n"
    )
    result = subprocess.run(
        ["bash", "-c", f"'{SCRIPT}' --loops 0 -p no:cacheprovider '{tmp_path}' >out"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env={
            **os.environ,
            "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
            "DERBYNET_LOAD_LOCK": str(tmp_path / "load.lock"),
        },
    )
    summary = (tmp_path / "out").read_text()
    assert result.returncode == 1, summary
    assert re.search(r"^ERROR \S*test_breaks.py::test_errors_at_setup", summary, re.M)
    assert re.search(r"^FAILED \S*test_breaks.py::test_fails", summary, re.M)


# A stub test that holds its run open until the test writes `go` beside it,
# for at most 60 s, so the first run is still running when the second
# starts, on any load.
HOLD = """\
import os
import time
from pathlib import Path

HERE = Path(__file__).parent


def test_holds():
    held = os.environ.get("DERBYNET_LOAD_LOCK_HELD")
    (HERE / "started").write_text(f"{os.getpid()} {held}")
    for _ in range(600):
        if (HERE / "go").exists():
            break
        time.sleep(0.1)
    else:
        raise AssertionError("no go within 60 s")
"""


def start(tmp_path, name, lock, extra_env=None, go=False, loops=1, under=()):
    """The script on a stub of its own in `tmp_path/name`, in a session of
    its own so the test can kill all it started, with its output in
    `tmp_path/name.out` (a file, as run_in_background gives it). With `go`
    its stub passes at once; `under` is a command it runs under, such as
    `timeout`."""
    folder = tmp_path / name
    folder.mkdir()
    (folder / f"test_{name}.py").write_text(HOLD)
    if go:
        (folder / "go").write_text("")
    out = open(tmp_path / f"{name}.out", "w")
    env = {
        **os.environ,
        "DERBYNET_LOAD_RUN_DIR": str(tmp_path / f"{name}-run"),
        "DERBYNET_LOAD_LOCK": str(lock),
        **(extra_env or {}),
    }
    env.pop("DERBYNET_LOAD_LOCK_HELD", None)
    run = subprocess.Popen(
        [
            *under,
            str(SCRIPT),
            "--loops",
            str(loops),
            "-p",
            "no:cacheprovider",
            str(folder),
        ],
        stdout=out,
        stderr=subprocess.STDOUT,
        cwd=tmp_path,
        env=env,
        start_new_session=True,
    )
    out.close()
    return run


def wait_until(condition, what, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"no {what} within {seconds} s")
        time.sleep(0.1)


def wait_started(tmp_path, name, seconds=60):
    """Wait for the stub that start() gave `name` to start; when it never
    does, the message carries the script's output, such as its refusal."""
    try:
        wait_until((tmp_path / name / "started").exists, f"{name} run", seconds)
    except AssertionError as error:
        output = (tmp_path / f"{name}.out").read_text()
        raise AssertionError(f"{error}; the script printed:\n{output}") from None


def child_pids(pid):
    """(pid, command name) of each live process whose parent is `pid`."""
    found = []
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            name, rest = stat.read_text().rsplit(")", 1)
        except OSError:
            continue
        fields = rest.split()
        if int(fields[1]) == pid and fields[0] != "Z":
            found.append((int(stat.parent.name), name.split("(", 1)[1]))
    return found


def children(pid):
    """The command names of the processes whose parent is `pid`."""
    return [name for _, name in child_pids(pid)]


def alive(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def free(lock):
    """Whether the next load run would take `lock` at once."""
    fd = os.open(lock, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def holder_block(out):
    """The lines a waiting run printed under `held by:`."""
    text = out.read_text()
    if "held by:" not in text:
        return None
    block = text.split("held by:\n", 1)[1]
    return [line for line in block.splitlines() if line.startswith("  ")]


def finish(tmp_path, *runs):
    for folder in tmp_path.iterdir():
        if folder.is_dir() and list(folder.glob("test_*.py")):
            (folder / "go").write_text("")
    for run in runs:
        try:
            run.wait(timeout=90)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(run.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_second_load_run_waits_for_the_first_and_names_it(tmp_path):
    """#316's hand-off ran beside #317's load run and failed, then waited
    for a slot the lead handed out by message (#316 retro)."""
    lock = tmp_path / "load.lock"
    first = start(tmp_path, "first", lock)
    second = None
    try:
        wait_started(tmp_path, "first")
        second = start(tmp_path, "second", lock)
        out = tmp_path / "second.out"
        wait_until(
            lambda: (
                "Waiting for the load lock" in out.read_text()
                or (tmp_path / "second" / "started").exists()
            ),
            "wait or start of the second run",
        )
        assert not (tmp_path / "second" / "started").exists(), out.read_text()
        # It waits in flock, not in a sleep that would let it through later.
        wait_until(lambda: "flock" in children(second.pid), "flock under it", 10)
        held = out.read_text()
        assert f"  pid {first.pid}, since " in held
        assert f"  cwd {tmp_path}\n" in held
        assert re.search(r"^  head [0-9a-f]{40}$", held, re.M), held
        assert f"  args --loops 1 -p no:cacheprovider {tmp_path / 'first'}" in held
        (tmp_path / "first" / "go").write_text("")
        assert first.wait(timeout=90) == 0
        # Its pytest is told, so its header names no other run (conftest.py).
        started = (tmp_path / "first" / "started").read_text()
        assert started == f"{started.split()[0]} {lock}"
        wait_started(tmp_path, "second")
        (tmp_path / "second" / "go").write_text("")
        assert second.wait(timeout=90) == 0
        assert re.search(r"^Got the load lock after \d+ s$", out.read_text(), re.M)
    finally:
        finish(tmp_path, first, *([second] if second else []))


def test_a_sigkill_frees_the_lock_though_its_loops_and_pytest_live_on(tmp_path):
    """A SIGKILL skips the script's trap, so its busy loop and its pytest
    live on; neither keeps the lock, and the next run starts at once
    (plan/AGENT-BRIEF.md: a SIGKILL followed by the next run)."""
    lock = tmp_path / "load.lock"
    first = start(tmp_path, "first", lock)
    second = None
    try:
        wait_started(tmp_path, "first")
        stub = int((tmp_path / "first" / "started").read_text().split()[0])
        os.kill(first.pid, signal.SIGKILL)
        first.wait(timeout=10)
        second = start(tmp_path, "second", lock)
        wait_started(tmp_path, "second")
        os.kill(stub, 0)  # the first run's pytest still runs
        assert "Waiting" not in (tmp_path / "second.out").read_text()
    finally:
        finish(tmp_path, first, *([second] if second else []))


def test_two_runs_waiting_on_one_new_folder_do_not_both_take_it(tmp_path):
    """Both passed the used-folder check before the wait, so both wrote into
    the same folder; it is checked again once the lock is taken (#354
    review)."""
    lock = tmp_path / "load.lock"
    shared = {"DERBYNET_LOAD_RUN_DIR": str(tmp_path / "shared-run")}
    first = start(tmp_path, "first", lock)
    runs = [first]
    try:
        wait_started(tmp_path, "first")
        for name in ("second", "third"):
            runs.append(start(tmp_path, name, lock, shared, go=True))
        for name in ("second", "third"):
            out = tmp_path / f"{name}.out"
            wait_until(lambda o=out: "Waiting" in o.read_text(), f"{name} waiting")
        (tmp_path / "first" / "go").write_text("")
        codes = sorted(r.wait(timeout=90) for r in runs[1:])
        assert first.wait(timeout=90) == 0
        assert codes == [0, 2]
        refused = [
            out.read_text()
            for out in (tmp_path / "second.out", tmp_path / "third.out")
            if "already holds files" in out.read_text()
        ]
        assert len(refused) == 1
    finally:
        finish(tmp_path, *runs)


def test_a_waiter_never_reads_half_the_holder_s_lines(tmp_path):
    """The holder wrote its lines singly after a truncate, so a waiter
    that came while its git was slow printed only the pid and the folder
    (#354 review). Here the holder's git waits for the test."""
    lock = tmp_path / "load.lock"
    bin_dir = tmp_path / "slowbin"
    bin_dir.mkdir()
    git = shutil.which("git")
    (bin_dir / "git").write_text(
        "#!/bin/bash\n"
        f"touch '{tmp_path}/git-called'\n"
        "for _ in $(seq 600); do\n"
        f"  [ -e '{tmp_path}/git-go' ] && break\n"
        "  sleep 0.1\n"
        "done\n"
        f"exec '{git}' \"$@\"\n"
    )
    (bin_dir / "git").chmod(0o755)
    slow = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    first = start(tmp_path, "first", lock, slow)
    runs = [first]
    try:
        wait_until((tmp_path / "git-called").exists, "the holder's git")
        second = start(tmp_path, "second", lock, go=True)
        runs.append(second)
        out = tmp_path / "second.out"
        wait_until(
            lambda: "Waiting" in out.read_text() or second.poll() is not None,
            "the second run waiting or done",
        )
        (tmp_path / "git-go").write_text("")
        (tmp_path / "first" / "go").write_text("")
        assert second.wait(timeout=90) == 0
        assert first.wait(timeout=90) == 0
        held = holder_block(out)
        if held is not None:
            keys = [line.split()[0] for line in held]
            assert keys in (["(no"], ["pid", "cwd", "head", "args"]), held
    finally:
        finish(tmp_path, *runs)


def test_a_waiter_on_an_empty_lock_file_says_so(tmp_path):
    """Between the holder's truncation and its write the file is empty."""
    lock = tmp_path / "load.lock"
    lock.write_text("")
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    run = None
    try:
        run = start(tmp_path, "second", lock, go=True)
        out = tmp_path / "second.out"
        wait_until(lambda: "flock" in children(run.pid), "the run waiting")
        assert holder_block(out) == ["  (no details yet)"], out.read_text()
    finally:
        os.close(fd)
        if run:
            assert run.wait(timeout=90) == 0
            finish(tmp_path, run)


def test_no_flock_is_refused_with_how_to_install_it(tmp_path):
    """macOS has no `flock(1)`: the run printed the shell's `command not
    found` and a Waiting line with no holder (#354 review)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("ps", "tr", "dirname"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    out = tmp_path / "out"
    with out.open("w") as stdout:
        result = subprocess.run(
            [str(SCRIPT), "--loops", "0", str(tmp_path)],
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            env={
                "PATH": str(bin_dir),
                "HOME": str(tmp_path),
                "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
            },
        )
    assert result.returncode == 2
    assert "brew install flock" in result.stderr
    assert "not found" not in result.stderr + out.read_text()


def test_an_unwritable_lock_is_refused_by_name(tmp_path):
    folder = tmp_path / "read-only"
    folder.mkdir()
    folder.chmod(0o555)
    lock = folder / "derbynet" / "load-run.lock"
    try:
        result = run(
            f"'{SCRIPT}' --loops 0 tests/test_pytest_under_load.py >/dev/null",
            out=str(tmp_path / "run"),
            lock=str(lock),
        )
    finally:
        folder.chmod(0o755)
    assert result.returncode == 2
    assert f"cannot open the load lock {lock}" in result.stderr
    assert "Permission denied" not in result.stderr


def test_a_stub_that_never_starts_is_named_with_the_script_s_output(tmp_path):
    """Under `timeout` the script refuses to run before its stub starts, and
    the wait for the stub says why, not only "no first run within 60 s":
    #372's implementer lost runs to that refusal under a `timeout 150`."""
    run = start(tmp_path, "first", tmp_path / "load.lock", under=("timeout", "300"))
    try:
        assert run.wait(timeout=30) == 2
        with pytest.raises(AssertionError, match="it runs under timeout"):
            wait_started(tmp_path, "first", seconds=0)
    finally:
        finish(tmp_path, run)


# A `kill` for the scripts' cleanup traps, read through BASH_ENV: it writes
# `cleanup` into PAUSE_DIR and waits there, for at most 30 s, until the test
# writes `second`, then kills as the builtin does.  The test's second signal
# so lands inside the cleanup, before its first kill, on every run (#372).
# Its 30 s run from its own `cleanup` write, which the test waits for before
# it writes `second`, so the pause never misses it.
PAUSED_KILL = """kill() {
  : > "$PAUSE_DIR/cleanup"
  for _ in $(seq 300); do [ -e "$PAUSE_DIR/second" ] && break; sleep 0.1; done
  builtin kill "$@"
}
"""


def paused_kill(tmp_path):
    """The environment that gives a script PAUSED_KILL, pausing in
    `tmp_path`."""
    (tmp_path / "paused_kill.sh").write_text(PAUSED_KILL)
    return {"BASH_ENV": str(tmp_path / "paused_kill.sh"), "PAUSE_DIR": str(tmp_path)}


def second_signal_inside_the_cleanup(tmp_path, pid, signals):
    """Send `pid` the first of `signals`, wait for its cleanup to reach its
    first kill, then send the second while the cleanup waits there."""
    first, second = (getattr(signal, f"SIG{name}") for name in signals)
    os.kill(pid, first)
    wait_until((tmp_path / "cleanup").exists, "cleanup", 30)
    os.kill(pid, second)
    (tmp_path / "second").write_text("")


@pytest.mark.parametrize(
    "signals", [("TERM", "TERM"), ("TERM", "INT")], ids=["TERM-TERM", "TERM-INT"]
)
def test_a_second_signal_during_the_cleanup_still_frees_all(tmp_path, signals):
    """plan/AGENT-BRIEF.md: a tool that cleans up in a trap is tested with a
    second signal during the cleanup (#354 review), here held inside it by
    PAUSED_KILL (#372)."""
    lock = tmp_path / "load.lock"
    first = start(tmp_path, "first", lock, extra_env=paused_kill(tmp_path), loops=4)
    try:
        wait_started(tmp_path, "first")
        loops = [pid for pid, name in child_pids(first.pid) if name == "bash"]
        assert len(loops) == 4
        second_signal_inside_the_cleanup(tmp_path, first.pid, signals)
        first.wait(timeout=30)
        wait_until(lambda: not any(alive(pid) for pid in loops), "loops gone", 10)
        assert free(lock)
    finally:
        finish(tmp_path, first)


# A `date` that fails once the test writes `fail-date` into PAUSE_DIR, so the
# script's `ENDED=$(date +%s)` ends it under `set -e` with its loops still
# listed, and its EXIT trap starts the cleanup with no signal before it.
FAILING_DATE = """#! /bin/sh
[ -e "$PAUSE_DIR/fail-date" ] && exit 1
exec /usr/bin/date "$@"
"""


def test_a_signal_during_the_cleanup_after_an_error_still_frees_all(tmp_path):
    """The cleanup ignores a signal itself, for an EXIT trap no signal
    started, such as an error under `set -e` once pytest has ended: the
    signal trap's own ignore covers only a cleanup a signal started (#372
    review)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "date").write_text(FAILING_DATE)
    (bin_dir / "date").chmod(0o755)
    env = {**paused_kill(tmp_path), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    lock = tmp_path / "load.lock"
    first = start(tmp_path, "first", lock, extra_env=env, loops=4)
    try:
        wait_started(tmp_path, "first")
        loops = [pid for pid, name in child_pids(first.pid) if name == "bash"]
        assert len(loops) == 4
        (tmp_path / "fail-date").write_text("")
        (tmp_path / "first" / "go").write_text("")
        wait_until((tmp_path / "cleanup").exists, "cleanup", 60)
        os.kill(first.pid, signal.SIGTERM)
        (tmp_path / "second").write_text("")
        status = first.wait(timeout=30)
        wait_until(lambda: not any(alive(pid) for pid in loops), "loops gone", 10)
        # 1 is the failed `date`'s status: the TERM was ignored, not 143.
        assert status == 1
        assert free(lock)
    finally:
        finish(tmp_path, first)


HOLD_LOCK = Path(__file__).resolve().parents[1] / "tools" / "hold-load-lock.sh"

# A stand-in hand-off: it records the lock it was told it holds, then holds
# its run open until the test writes `go` beside it, for at most 60 s.
HAND_OFF = (
    'echo "$DERBYNET_LOAD_LOCK_HELD" > started; '
    "for _ in $(seq 600); do [ -e go ] && exit 0; sleep 0.1; done; exit 1"
)


def hold(tmp_path, lock, command=HAND_OFF, name="handoff", extra_env=None):
    """tools/hold-load-lock.sh running `command` through bash in
    `tmp_path/name`, in a session of its own, with its output in
    `tmp_path/name.out`, and `extra_env` added to its environment."""
    folder = tmp_path / name
    folder.mkdir()
    out = open(tmp_path / f"{name}.out", "w")
    env = {**os.environ, "DERBYNET_LOAD_LOCK": str(lock), **(extra_env or {})}
    env.pop("DERBYNET_LOAD_LOCK_HELD", None)
    run = subprocess.Popen(
        [str(HOLD_LOCK), "bash", "-c", command],
        stdout=out,
        stderr=subprocess.STDOUT,
        cwd=folder,
        env=env,
        start_new_session=True,
    )
    out.close()
    return run


def test_a_waiting_hand_off_goes_ahead_of_a_load_run_started_after_the_release(
    tmp_path,
):
    """#350's hand-off polled for a free lock and waited about 2 h, since
    #348's queued load runs took it again within seconds of each other. A
    hand-off waiting in hold-load-lock.sh's flock takes the lock as the
    holder lets it go, though a load run starts at once after the release,
    and that run then waits for the hand-off and names it."""
    lock = tmp_path / "load.lock"
    lock.write_text("pid 1, since then\n")
    held = os.open(lock, os.O_RDONLY)
    fcntl.flock(held, fcntl.LOCK_EX)
    handoff = hold(tmp_path, lock)
    load = None
    try:
        out = tmp_path / "handoff.out"
        wait_until(lambda: "flock" in children(handoff.pid), "flock under it", 30)
        assert "Waiting for the load lock" in out.read_text()
        assert holder_block(out) == ["  pid 1, since then"]
        fcntl.flock(held, fcntl.LOCK_UN)
        load = start(tmp_path, "load", lock, go=True)
        wait_until((tmp_path / "handoff" / "started").exists, "the hand-off's command")
        assert (tmp_path / "handoff" / "started").read_text() == f"{lock}\n"
        load_out = tmp_path / "load.out"
        wait_until(lambda: "Waiting" in load_out.read_text(), "the load run waiting")
        assert not (tmp_path / "load" / "started").exists()
        # It prints the holder's lines, then waits in flock (a read on
        # "Waiting" alone found none printed yet, 1 of 25 under load).
        wait_until(
            lambda: "flock" in children(load.pid), "flock under the load run", 30
        )
        block = holder_block(load_out)
        assert block[0].startswith(f"  pid {handoff.pid}, since "), block
        assert block[3] == f"  args hold-load-lock.sh: bash -c {HAND_OFF}", block
        (tmp_path / "handoff" / "go").write_text("")
        assert handoff.wait(timeout=30) == 0
        assert load.wait(timeout=90) == 0
    finally:
        os.close(held)
        finish(tmp_path, handoff, *([load] if load else []))


def test_a_hand_off_exits_with_its_command_s_status(tmp_path):
    run = hold(tmp_path, tmp_path / "load.lock", "exit 3")
    assert run.wait(timeout=30) == 3
    assert free(tmp_path / "load.lock")


@pytest.mark.parametrize("held", ["load.lock", "./sub/../load.lock"])
@pytest.mark.parametrize("script", [SCRIPT, HOLD_LOCK], ids=lambda p: p.name)
def test_a_run_under_a_holder_of_its_lock_is_refused(tmp_path, script, held):
    """Its flock would wait for ever on the holder above it, though the
    holder names the lock by another path (#370 review)."""
    (tmp_path / "sub").mkdir()
    lock = str(tmp_path / "load.lock")
    result = subprocess.run(
        # No such file, so a run that is not refused ends at once.
        [str(script), "--loops", "0", "no_such_test.py"],
        # A file, not a pipe, as run_in_background gives it.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
            "DERBYNET_LOAD_LOCK": lock,
            "DERBYNET_LOAD_LOCK_HELD": f"{tmp_path}/{held}",
        },
    )
    assert result.returncode == 2
    assert f"it runs under a holder of {lock}" in result.stderr
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "script, args, code",
    [
        (HOLD_LOCK, ["bash", "-c", 'echo "$DERBYNET_LOAD_LOCK_HELD"'], 0),
        # pytest finds no such file and exits 4, past the refusal.
        (SCRIPT, ["--loops", "0", "no_such_test.py"], 4),
    ],
    ids=["hold-load-lock.sh", "pytest-under-load.sh"],
)
def test_a_run_under_a_holder_of_another_lock_takes_its_own(
    tmp_path, script, args, code
):
    """A load run's pytest carries DERBYNET_LOAD_LOCK_HELD, and a test it
    runs that starts either script on another lock is not refused (#370
    review: `= "$LOCK"` made `-n` survived)."""
    lock = tmp_path / "load.lock"
    folder = tmp_path / "handoff"
    folder.mkdir()
    out = tmp_path / "out"
    with out.open("w") as stdout:
        result = subprocess.run(
            [str(script), *args],
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            cwd=folder,
            env={
                **os.environ,
                "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
                "DERBYNET_LOAD_LOCK": str(lock),
                "DERBYNET_LOAD_LOCK_HELD": str(tmp_path / "other.lock"),
            },
        )
    assert result.returncode == code, result.stderr + out.read_text()
    assert "runs under a holder" not in result.stderr
    # Each held its own lock: its holder lines name it.
    assert lock.read_text().startswith("pid ")


def stop_a_hand_off(tmp_path, sig):
    """A hand-off whose command runs a child that waits for `go`, stopped
    by `sig` once that child has started: (the command's pid and its
    child's, whether the lock is free once the hand-off has ended). No `go`
    is written, so only a signal ends them."""
    lock = tmp_path / "load.lock"
    handoff = hold(tmp_path, lock, f"({HAND_OFF}) & wait")
    wait_until((tmp_path / "handoff" / "started").exists, "the hand-off's command")
    command = [pid for pid, _ in child_pids(handoff.pid)]
    assert len(command) == 1
    group = command + [pid for pid, _ in child_pids(command[0])]
    assert len(group) == 2
    os.kill(handoff.pid, sig)
    handoff.wait(timeout=30)
    return group, free(lock)


def kill_all(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_sigterm_stops_the_hand_off_s_command_and_frees_the_lock(tmp_path):
    """TaskStop sends SIGTERM; the command's whole group goes with it."""
    group, freed = stop_a_hand_off(tmp_path, signal.SIGTERM)
    try:
        assert freed
        wait_until(lambda: not any(alive(p) for p in group), "its command gone", 10)
    finally:
        kill_all(group)


def test_a_sigkill_frees_the_lock_though_the_hand_off_s_command_lives_on(tmp_path):
    """A SIGKILL skips the trap; the command holds no copy of the lock
    (plan/AGENT-BRIEF.md: a SIGKILL followed by the next run)."""
    group, freed = stop_a_hand_off(tmp_path, signal.SIGKILL)
    try:
        assert freed
        assert all(alive(p) for p in group)
    finally:
        kill_all(group)


# A stand-in journalctl: it records its arguments and prints three veth
# lines and one other.
JOURNALCTL = """#! /bin/sh
echo "$@" > "$(dirname "$0")/journalctl.args"
printf 'veth1 entered\\nveth2 left\\nveth3 entered\\neth0 up\\n'
"""


def load_run_of(
    tmp_path, test_body, *args, journalctl=JOURNALCTL, options="", python3=None
):
    """pytest-under-load.sh, with `options` of its own, on a stub test file
    holding `test_body`, with `journalctl`, and `python3` when given, as
    stand-ins first on PATH: (its exit code, its output, the journalctl
    arguments file)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "journalctl").write_text(journalctl)
    (bin_dir / "journalctl").chmod(0o755)
    if python3:
        (bin_dir / "python3").write_text(python3)
        (bin_dir / "python3").chmod(0o755)
    (tmp_path / "test_net.py").write_text(
        f"def test_loads():\n{test_body}\n\n\ndef test_passes():\n    pass\n"
    )
    command = (
        f"'{SCRIPT}' --loops 0 {options} -p no:cacheprovider {' '.join(args)}"
        f" '{tmp_path}'"
    )
    result = subprocess.run(
        ["bash", "-c", command + " >out"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
            "DERBYNET_LOAD_LOCK": str(tmp_path / "load.lock"),
            "DERBYNET_BROWSER_FONT": "DejaVu Sans",
        },
    )
    return (
        result.returncode,
        (tmp_path / "out").read_text(),
        bin_dir / "journalctl.args",
    )


def test_a_network_change_names_the_veth_lines_and_reruns_each_file(tmp_path):
    """#348 read the kernel log by hand for three load runs that failed only
    on net::ERR_NETWORK_CHANGED (#348 retro). A failure and a setup error
    each name their file, and the rerun names both; the run lasts past a
    second, so its window does too. Both fail again in the rerun, so the
    status stays pytest's (D122)."""
    (tmp_path / "test_net_setup.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef page():\n"
        + PAGE_LOAD_ERROR.rstrip()
        + "\n\n\ndef test_opens(page):\n    pass\n"
    )
    code, out, args = load_run_of(
        tmp_path, "    import time\n\n    time.sleep(1.1)\n" + PAGE_LOAD_ERROR
    )
    assert code == 1, out
    found = re.search(
        r"^net::ERR_NETWORK_CHANGED in the log; the kernel log has 3 veth lines from"
        r" @(\d+) to @(\d+) \(journalctl -k --since @\1 --until @\2 \| grep veth\)\.$",
        out,
        re.M,
    )
    assert found, out
    assert int(found[1]) < int(found[2])
    assert args.read_text().split() == [
        "-k",
        "--since",
        f"@{found[1]}",
        "--until",
        f"@{found[2]}",
    ]
    # pytest names the file from server/, where the rerun runs.
    failed = [
        os.path.relpath(tmp_path / name, SCRIPT.parents[1])
        for name in ("test_net.py", "test_net_setup.py")
    ]
    assert (
        "Rerunning the failed files alone, with no load, before the lock is freed: "
        'DERBYNET_BROWSER_FONT="DejaVu Sans" uv run pytest -p no:cacheprovider -n 0 '
        f"{failed[0]} {failed[1]}\n"
    ) in out
    assert re.search(r"^rerun exited 1; log: .*/rerun\.log;", out, re.M), out
    assert re.search(r"^ERROR \S*test_net_setup\.py::test_opens", out, re.M)
    assert (
        "The first run exited 1, with 3 veth lines in its window; the rerun"
        " failed too (exit 1).\n"
    ) in out
    assert out.rstrip().splitlines()[-1].startswith("pytest exited 1; log: ")


# A stub's raise of Playwright's page-load error, as #348's load run failed:
#   playwright._impl._errors.Error: Page.goto: net::ERR_NETWORK_CHANGED at http://...
PAGE_LOAD_ERROR = """\
    class Error(Exception):
        pass

    Error.__module__ = "playwright._impl._errors"
    Error.__qualname__ = "Error"
    raise Error(
        "Page.goto: net::ERR_NETWORK_CHANGED at http://localhost:1/kiosk.php"
    )
"""


def once(raises):
    """A stub test body that runs `raises`, a body of its own, on its first
    run only, leaving a file beside it, and passes on the rerun."""
    return (
        "    from pathlib import Path\n\n"
        "    first = not Path(__file__).with_name('failed-once').exists()\n"
        "    Path(__file__).with_name('failed-once').write_text('')\n"
        "    if first:\n" + "".join(f"    {line}\n" for line in raises.splitlines())
    )


def test_a_network_change_alone_reruns_and_a_clean_rerun_passes_the_run(tmp_path):
    """D122: every failure is a changed network, the kernel log has veth
    lines over the run, and the rerun passes, so the script exits 0. The
    first run's log, junit file and status stay as they were (#373)."""
    code, out, _ = load_run_of(tmp_path, once(PAGE_LOAD_ERROR))
    assert code == 0, out
    run_dir = tmp_path / "run"
    assert "1 failed, 1 passed" in (run_dir / "pytest.log").read_text()
    assert 'failures="1"' in (run_dir / "junit.xml").read_text()
    assert "2 passed" in (run_dir / "rerun.log").read_text()
    assert 'failures="0"' in (run_dir / "rerun-junit.xml").read_text()
    assert re.search(r"^rerun exited 0; log: \S*/rerun\.log;", out, re.M), out
    assert (
        "The first run exited 1, with 3 veth lines in its window; the rerun"
        " passed: the run counts as passed for D101's repeats only (D122)"
    ) in out
    assert out.rstrip().splitlines()[-1].startswith("pytest exited 1; log: ")
    assert out.rstrip().endswith("the script exits 0 (D122)")


def test_a_flake_ticket_s_run_keeps_pytest_s_status_after_a_clean_rerun(tmp_path):
    """--flake-ticket: a flake ticket's race keeps its 25 runs (D122)."""
    code, out, _ = load_run_of(
        tmp_path, once(PAGE_LOAD_ERROR), options="--flake-ticket"
    )
    assert code == 1, out
    assert "2 passed" in (tmp_path / "run" / "rerun.log").read_text()
    assert "the rerun passed, and --flake-ticket keeps pytest's status 1" in out
    assert out.rstrip().splitlines()[-1].startswith("pytest exited 1; log: ")
    assert "script exits" not in out


def test_another_failure_beside_a_network_change_starts_no_rerun(tmp_path):
    (tmp_path / "test_other.py").write_text("def test_other():\n    assert False\n")
    code, out, _ = load_run_of(tmp_path, once(PAGE_LOAD_ERROR))
    assert code == 1, out
    assert "the kernel log has 3 veth lines" in out
    assert (
        "1 of 2 failures and errors are not a changed network"
        " (a Playwright net::ERR_NETWORK_CHANGED, or the browser harness's"
        " teardown naming one): no rerun, and the status stays pytest's.\n"
    ) in out
    assert not (tmp_path / "run" / "rerun.log").exists()
    assert "Rerunning" not in out


def test_the_bare_error_text_in_a_failure_starts_no_rerun(tmp_path):
    """This file's own asserts print the script's lines, which name the
    error (`assert code == 1, out`): a flake there, with veth churn, must
    keep its failure (#373, lead)."""
    code, out, _ = load_run_of(
        tmp_path,
        once(
            "    raise AssertionError('net::ERR_NETWORK_CHANGED in the log; the kernel"
            " log has 3 veth lines')"
        ),
    )
    assert code == 1, out
    assert "1 of 1 failures and errors are not a changed network" in out
    assert not (tmp_path / "run" / "rerun.log").exists()


@pytest.mark.parametrize(
    "journalctl, seen",
    [
        ("#! /bin/sh\necho 'eth0 up'\n", "0 veth lines"),
        ("#! /bin/sh\nexit 0\n", "no veth count"),
    ],
    ids=["no-veth", "unread"],
)
def test_a_clean_rerun_with_no_veth_count_keeps_pytest_s_status(
    tmp_path, journalctl, seen
):
    """A veth count of 0, or one not read, keeps pytest's status; the rerun
    still runs and is reported (#373)."""
    code, out, _ = load_run_of(tmp_path, once(PAGE_LOAD_ERROR), journalctl=journalctl)
    assert code == 1, out
    assert re.search(r"^rerun exited 0; log: ", out, re.M), out
    assert (
        f"The first run exited 1, with {seen} in its window; the rerun passed,"
        " but with no veth lines counted the status stays pytest's 1 (D122)."
    ) in out


def test_a_run_without_xdist_is_rerun_without_n(tmp_path):
    """`-n 0` beside `-p no:xdist` exits 4, "unrecognized arguments: -n"
    (#373 review)."""
    code, out, _ = load_run_of(tmp_path, once(PAGE_LOAD_ERROR), "-p", "no:xdist")
    assert code == 0, out
    assert " -n " not in out.split("Rerunning", 1)[1].splitlines()[0]
    assert "2 passed" in (tmp_path / "run" / "rerun.log").read_text()


def failing(command):
    """A stand-in python3 that fails network_changed.py's `command` with
    exit 2, as a wrong command line does, after printing the lines a
    passing outcome prints, a line and then `exit 0`, and runs the rest."""
    real = shutil.which("python3")
    return (
        f'#! /bin/sh\ncase "$3" in {command})'
        ' printf "the run counts as passed\\nexit 0\\n"; exit 2;; esac\n'
        f'exec {real} "$@"\n'
    )


def test_a_failed_outcome_is_named_and_keeps_pytest_s_status(tmp_path):
    """A tool error is no pass and no silent fold into pytest's status
    (#373 review)."""
    code, out, _ = load_run_of(
        tmp_path, once(PAGE_LOAD_ERROR), python3=failing("outcome")
    )
    assert code == 1, out
    assert re.search(r"^rerun exited 0; log: ", out, re.M), out
    assert (
        "network_changed.py outcome failed (exit 2): the status stays pytest's.\n"
    ) in out
    assert out.rstrip().splitlines()[-1].startswith("pytest exited 1; log: ")
    assert "script exits" not in out


def test_a_failed_rerun_args_is_named_and_starts_no_rerun(tmp_path):
    code, out, _ = load_run_of(
        tmp_path, once(PAGE_LOAD_ERROR), python3=failing("rerun-args")
    )
    assert code == 1, out
    assert (
        "network_changed.py rerun-args failed (exit 2): no rerun, and the status"
        " stays pytest's.\n"
    ) in out
    assert not (tmp_path / "run" / "rerun.log").exists()


# A stub whose first run fails on a changed network and whose rerun holds
# until the test writes `go` beside it, for at most 60 s.
HELD_RERUN = (
    "import os\nimport time\nfrom pathlib import Path\n\n\n"
    "def test_loads():\n"
    "    here = Path(__file__).parent\n"
    "    if (here / 'failed-once').exists():\n"
    "        (here / 'rerunning').write_text(str(os.getpid()))\n"
    "        for _ in range(600):\n"
    "            if (here / 'go').exists():\n"
    "                return\n"
    "            time.sleep(0.1)\n"
    "        raise AssertionError('no go within 60 s')\n"
    "    (here / 'failed-once').write_text('')\n" + PAGE_LOAD_ERROR
)


def start_held_rerun(tmp_path):
    """The script, with two loops, on HELD_RERUN in `tmp_path/held`, in a
    session of its own, with the stand-in journalctl: (the run, the lock)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "journalctl").write_text(JOURNALCTL)
    (bin_dir / "journalctl").chmod(0o755)
    folder = tmp_path / "held"
    folder.mkdir()
    (folder / "test_held.py").write_text(HELD_RERUN)
    lock = tmp_path / "load.lock"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "held-run"),
        "DERBYNET_LOAD_LOCK": str(lock),
    }
    env.pop("DERBYNET_LOAD_LOCK_HELD", None)
    with open(tmp_path / "held.out", "w") as out:
        run = subprocess.Popen(
            [str(SCRIPT), "--loops", "2", "-p", "no:cacheprovider", str(folder)],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=tmp_path,
            env=env,
            start_new_session=True,
        )
    wait_until((folder / "rerunning").exists, "the rerun", 90)
    return run, lock


def test_the_rerun_holds_the_lock_with_no_loops_and_no_xdist(tmp_path):
    """The rerun runs before the lock is freed, so the next load run waits
    for it, with the busy loops stopped (D122)."""
    run, lock = start_held_rerun(tmp_path)
    try:
        assert not free(lock)
        assert "bash" not in children(run.pid)
        rerun = int((tmp_path / "held" / "rerunning").read_text())
        argv = Path(f"/proc/{rerun}/cmdline").read_bytes().split(b"\0")
        assert b"-n" in argv and argv[argv.index(b"-n") + 1] == b"0", argv
        (tmp_path / "held" / "go").write_text("")
        assert run.wait(timeout=60) == 0, (tmp_path / "held.out").read_text()
    finally:
        finish(tmp_path, run)


@pytest.mark.parametrize(
    "signals",
    [("TERM",), ("TERM", "TERM"), ("TERM", "INT")],
    ids=["TERM", "TERM-TERM", "TERM-INT"],
)
def test_a_signal_during_the_rerun_stops_it_and_frees_the_lock(tmp_path, signals):
    """plan/AGENT-BRIEF.md: a tool that cleans up in a trap is tested with a
    second signal during the cleanup."""
    run, lock = start_held_rerun(tmp_path)
    try:
        rerun = int((tmp_path / "held" / "rerunning").read_text())
        for name in signals:
            os.kill(run.pid, getattr(signal, f"SIG{name}"))
        assert run.wait(timeout=30) == 143
        assert free(lock)
        wait_until(lambda: not alive(rerun), "the rerun's pytest gone", 10)
    finally:
        finish(tmp_path, run)


def test_a_sigkill_during_the_rerun_frees_the_lock_for_the_next_run(tmp_path):
    """The rerun's pytest gets no copy of the lock (plan/AGENT-BRIEF.md: a
    SIGKILL followed by the next run)."""
    run, lock = start_held_rerun(tmp_path)
    second = None
    try:
        rerun = int((tmp_path / "held" / "rerunning").read_text())
        os.kill(run.pid, signal.SIGKILL)
        run.wait(timeout=10)
        assert alive(rerun)
        second = start(tmp_path, "second", lock, go=True, loops=0)
        assert second.wait(timeout=90) == 0
        assert "Waiting" not in (tmp_path / "second.out").read_text()
    finally:
        finish(tmp_path, run, *([second] if second else []))


def test_another_failure_names_neither(tmp_path):
    code, out, args = load_run_of(tmp_path, "    raise AssertionError('other')")
    assert code == 1, out
    assert "kernel log" not in out
    assert "Rerun" not in out
    assert not args.exists()


def test_a_passing_run_that_prints_the_error_names_neither(tmp_path):
    """A test that prints the error and passes, with `-s`, leaves it in
    the log of a run that failed nothing."""
    code, out, args = load_run_of(
        tmp_path, "    print('net::ERR_NETWORK_CHANGED, as a page logs it')", "-s"
    )
    assert code == 0, out
    assert (
        "net::ERR_NETWORK_CHANGED, as a page logs it"
        in (tmp_path / "run" / "pytest.log").read_text()
    )
    assert "kernel log" not in out
    assert "Rerun" not in out
    assert not args.exists()


def test_a_failed_journalctl_is_named_not_counted(tmp_path):
    """A journalctl that cannot read the window, as one refusing `@epoch`
    would, prints its error: "0 veth lines" would read as a real failure."""
    code, out, _ = load_run_of(
        tmp_path,
        "    raise AssertionError('net::ERR_NETWORK_CHANGED at app.css')",
        journalctl=(
            "#! /bin/sh\n"
            "case \"$*\" in *--since*) ;; *) echo 'a kernel line'; exit 0;; esac\n"
            "echo 'Failed to parse timestamp' >&2\nexit 1\n"
        ),
    )
    assert code == 1, out
    assert (
        "the kernel log has no count (journalctl failed: Failed to parse timestamp)"
        in out
    )
    assert "veth lines" not in out


@pytest.mark.parametrize(
    "signals", [("TERM", "TERM"), ("TERM", "INT")], ids=["TERM-TERM", "TERM-INT"]
)
def test_a_second_signal_during_the_hand_off_s_cleanup_still_frees_all(
    tmp_path, signals
):
    """plan/AGENT-BRIEF.md: a tool that cleans up in a trap is tested with a
    second signal during the cleanup, here held inside it by PAUSED_KILL
    (#372)."""
    lock = tmp_path / "load.lock"
    handoff = hold(
        tmp_path, lock, f"({HAND_OFF}) & wait", extra_env=paused_kill(tmp_path)
    )
    wait_until((tmp_path / "handoff" / "started").exists, "the hand-off's command")
    command = [pid for pid, _ in child_pids(handoff.pid)]
    group = command + [pid for pid, _ in child_pids(command[0])]
    assert len(group) == 2
    try:
        second_signal_inside_the_cleanup(tmp_path, handoff.pid, signals)
        handoff.wait(timeout=30)
        assert free(lock)
        wait_until(lambda: not any(alive(p) for p in group), "its command gone", 10)
    finally:
        kill_all(group)


def test_a_journalctl_that_reads_no_kernel_log_is_named_not_counted(tmp_path):
    """A journalctl without permission to read the kernel log exits 0 with
    no lines (#370 review), which would count as "0 veth lines"."""
    code, out, _ = load_run_of(
        tmp_path,
        "    raise AssertionError('net::ERR_NETWORK_CHANGED at app.css')",
        journalctl="#! /bin/sh\nexit 0\n",
    )
    assert code == 1, out
    assert "the kernel log has no count (journalctl reads no kernel log here)" in out
    assert "veth lines" not in out


def test_a_network_change_with_no_failed_file_prints_no_rerun(tmp_path):
    """With no file named, `-m browser -n 0` alone would run every browser
    test (#370 review)."""
    code, out, _ = load_run_of(
        tmp_path,
        "    import pytest\n\n"
        "    pytest.exit('net::ERR_NETWORK_CHANGED at app.css', returncode=3)",
    )
    assert code == 3, out
    assert "veth lines" in out
    assert "Rerun each failed file" not in out
    assert "No failed file is named in the summary: rerun the run's own command." in out


def hold_with(tmp_path, args, env=None, stdin=None):
    """hold-load-lock.sh run with `args` in `tmp_path/handoff`: (its exit
    code, its stderr)."""
    folder = tmp_path / "handoff"
    folder.mkdir(exist_ok=True)
    result = subprocess.run(
        [str(HOLD_LOCK), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=folder,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "DERBYNET_LOAD_LOCK": str(tmp_path / "load.lock"),
            **(env or {}),
        },
    )
    return result.returncode, result.stderr


def test_a_hand_off_with_no_command_is_refused(tmp_path):
    code, err = hold_with(tmp_path, [])
    assert code == 2
    assert err == "usage: hold-load-lock.sh COMMAND [ARGUMENT ...]\n"
    assert not (tmp_path / "load.lock").exists()


def test_a_hand_off_with_no_flock_is_refused_with_how_to_install_it(tmp_path):
    """macOS has no `flock(1)` (#354 review)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("dirname", "mkdir"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    code, err = hold_with(tmp_path, ["true"], {"PATH": str(bin_dir)})
    assert code == 2
    assert "it needs flock(1)" in err
    assert "brew install flock" in err
    assert "not found" not in err


def test_a_hand_off_on_an_unwritable_lock_is_refused_by_name(tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    folder.chmod(0o555)
    lock = folder / "load.lock"
    try:
        code, err = hold_with(tmp_path, ["true"], {"DERBYNET_LOAD_LOCK": str(lock)})
    finally:
        folder.chmod(0o755)
    assert code == 2
    assert f"it cannot open the load lock {lock}" in err
    assert "Permission denied" not in err


def test_a_hand_off_s_command_reads_its_stdin(tmp_path):
    """bash gives a background command /dev/null for stdin (#370 review)."""
    code, err = hold_with(tmp_path, ["bash", "-c", "cat > got"], stdin="a line\n")
    assert code == 0, err
    assert (tmp_path / "handoff" / "got").read_text() == "a line\n"
