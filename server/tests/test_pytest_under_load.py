"""`tools/pytest-under-load.sh` refuses a pipe and a `timeout` before it
starts anything: #47 and #48 each lost a 25-repeat run to
`timeout 3000 ... | grep` (#48 retro). It also refuses an output folder
that already holds files: #220 reused one, and a wait for its `junit.xml`
fired on an earlier session's results (#220 retro). A run holds a lock
for the machine, a second waits for it, and a SIGKILL frees it (#316
retro). Waiters take it in the order they came (#417), every hand-off
ahead of the load runs (#419)."""

import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "pytest-under-load.sh"

# The argv prefix start() and hold() start a script through: it puts TERM,
# INT and HUP, the signals the scripts trap, back to their default, with the
# two Python ignores (PIPE, XFSZ), then execs the command in the same pid.
# conftest.py puts INT and QUIT back for pytest itself (#407,
# tests/test_signals_at_default.py); the prefix stays for the TERM, INT or
# HUP a test here ignores itself.
RESET_NAMES = ("SIGTERM", "SIGINT", "SIGHUP", "SIGPIPE", "SIGXFSZ")
RESET_SIGNALS_ARGV = (
    sys.executable,
    "-c",
    "import os, signal, sys\n"
    f"for name in {RESET_NAMES!r}:\n"
    "    signal.signal(getattr(signal, name), signal.SIG_DFL)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n",
)


def ignored_in(status):
    """The names of the signals ignored in `status`'s SigIgn mask, the
    text of a /proc/PID/status."""
    mask = int(re.search(r"^SigIgn:\s*([0-9a-f]+)$", status, re.M).group(1), 16)
    return {s.name for s in signal.Signals if mask >> (s.value - 1) & 1}


def ignored(pid):
    """The names of the signals `pid` ignores."""
    return ignored_in(Path(f"/proc/{pid}/status").read_text())


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


@pytest.mark.parametrize("count", ["--count 3", "--count=3"])
def test_a_case_s_node_id_under_count_is_refused_before_the_lock(tmp_path, count):
    """--count renames `[TERM]` to `[TERM-1-3]`, so #463's node ids selected
    nothing, after the run had waited for the lock (#463 retro)."""
    case_id = "tests/test_x.py::test_signal[TERM]"
    result = run(
        f"'{SCRIPT}' --loops 0 -n 4 {count} '{case_id}' >/dev/null",
        out=str(tmp_path / "run"),
        lock=str(tmp_path / "load.lock"),
    )
    assert result.returncode == 2
    assert f"renames the [case] of {case_id}" in result.stderr
    assert not (tmp_path / "load.lock").exists()


@pytest.mark.parametrize(
    "count, node, ran",
    [
        ("--count 2", "test_signal[TERM-1-2]", "1 passed"),
        ("--count 2", "test_signal", "2 passed"),
        ("--count 1", "test_signal[TERM]", "1 passed"),
        ("--count=1", "test_signal[TERM]", "1 passed"),
    ],
)
def test_a_node_id_that_selects_under_count_runs(tmp_path, count, node, ran):
    """The id pytest-repeat gives a repeat, `[case-1-N]`, selects that
    repeat, and a test's id without its case selects each. A count of 1
    renames nothing, as #463's light run selected its cases by id."""
    (tmp_path / "test_case.py").write_text(
        "import pytest\n\n\n"
        "@pytest.mark.parametrize('sig', ['TERM'])\n"
        "def test_signal(sig):\n    pass\n"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"'{SCRIPT}' --loops 0 -p no:cacheprovider {count}"
            f" '{tmp_path}/test_case.py::{node}' >out",
        ],
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
    assert result.returncode == 0, summary + result.stderr
    assert f"{ran} in" in summary


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
# starts, on any load.  It adds its folder's name to `order` beside that
# folder as it starts.  It writes `started` by a rename, since wait_started
# returns once the file exists and its callers read it at once (#407).
HOLD = """\
import os
import time
from pathlib import Path

HERE = Path(__file__).parent


def test_holds():
    held = os.environ.get("DERBYNET_LOAD_LOCK_HELD")
    with open(HERE.parent / "order", "a") as order:
        order.write(HERE.name + "\\n")
    (HERE / "started.tmp").write_text(f"{os.getpid()} {held}")
    os.replace(HERE / "started.tmp", HERE / "started")
    for _ in range(600):
        if (HERE / "go").exists():
            break
        time.sleep(0.1)
    else:
        raise AssertionError("no go within 60 s")
"""


def start(
    tmp_path, name, lock, extra_env=None, go=False, loops=1, under=(), script=SCRIPT
):
    """The script (or `script`, a copy of it) on a stub of its own in
    `tmp_path/name`, in a session of its own so the test can kill all it
    started, with its output in `tmp_path/name.out` (a file, as
    run_in_background gives it). With `go` its stub passes at once; `under`
    is a command it runs under, such as `timeout`."""
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
            *RESET_SIGNALS_ARGV,
            *under,
            str(script),
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


# The slowest load run of a rerun test #463 records, in seconds: on #455's
# push, at a load average of about 24, load_run_of's single bound over every
# step of the run, its two pytest starts among them, ran out at 120 s. One
# step may take all of it, so a wait over several steps gives each step
# STEP_S from the step before, twice that (#463).
SLOWEST_RUN_S = 120
STEP_S = 2 * SLOWEST_RUN_S


def load_run_steps(out, run_dir):
    """The steps a load run with its output in `out` and its results in
    `run_dir` prints or writes on the way to its end, as (step, check)."""
    return (
        ("the `Under load` line", lambda: "Under load (" in out.read_text()),
        ("the first run's junit file", (run_dir / "junit.xml").exists),
        ("the `Rerunning` line", lambda: "Rerunning the failed" in out.read_text()),
        ("the rerun's junit file", (run_dir / "rerun-junit.xml").exists),
    )


def assert_reran(tmp_path):
    """load_run_of's run in `tmp_path` printed and wrote every step of a
    rerun; the message names the first it ended without (#463)."""
    out = tmp_path / "out"
    for step, check in load_run_steps(out, tmp_path / "run"):
        assert check(), (
            f"the script ended without {step}; it printed:\n{out.read_text()}"
        )


def wait_through(run, out, what, done, steps=()):
    """Wait for `done()`, the `what`, while the script `run` lives, its output
    in `out`. `steps` are what the script prints or writes on the way, in
    order, as (step, check): each gets STEP_S from the one before it, and
    the `what` gets STEP_S from the last. A failed wait names the step that
    did not come and the last one that did: at once when the script exits
    first, and STEP_S after that last step otherwise (#463)."""
    seen = "the script's start"
    pending = list(steps)
    deadline = time.monotonic() + STEP_S
    while not done():
        while pending and pending[0][1]():
            seen = pending.pop(0)[0]
            deadline = time.monotonic() + STEP_S
        missing = pending[0][0] if pending else f"the {what}"
        if run.poll() is not None and not done():
            raise AssertionError(
                f"the script exited {run.returncode} before {missing}, after"
                f" {seen}; it printed:\n{out.read_text()}"
            )
        if time.monotonic() > deadline:
            raise AssertionError(
                f"{missing} did not come within {STEP_S} s of {seen}; the script"
                f" printed:\n{out.read_text()}"
            )
        time.sleep(0.1)


def wait_ended(run, out, steps=()):
    """Wait for the script `run` to end, its output in `out`, through
    `steps`: its status."""
    wait_through(run, out, "end of the script", lambda: run.poll() is not None, steps)
    return run.returncode


class FakeClock:
    """A stand-in for the `time` module in wait_through: its sleep moves
    its clock on, and refuses to pass `limit`, so a wait that never ends
    fails."""

    def __init__(self, limit):
        self.now = 0.0
        self.limit = limit

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        assert self.now < self.limit, "the wait ran on past every step"


class FakeRun:
    """A stand-in Popen whose script exits with `code` once `polls` polls
    have found it running."""

    def __init__(self, polls=float("inf"), code=1):
        self.polls = polls
        self.code = code
        self.returncode = None

    def poll(self):
        self.polls -= 1
        if self.polls < 0:
            self.returncode = self.code
        return self.returncode


def at(clock, when):
    return lambda: clock.now >= when


def fake_clock(monkeypatch):
    clock = FakeClock(limit=10 * STEP_S)
    monkeypatch.setitem(globals(), "time", clock)
    return clock


def test_a_wait_gives_each_step_its_own_bound(tmp_path, monkeypatch):
    """Each step comes just inside STEP_S of the one before, and the whole
    wait takes several times STEP_S (#463)."""
    clock = fake_clock(monkeypatch)
    out = tmp_path / "out"
    out.write_text("")
    gap = 0.9 * STEP_S
    steps = [(f"step {n}", at(clock, n * gap)) for n in (1, 2, 3)]
    wait_through(FakeRun(), out, "event", at(clock, 4 * gap), steps)
    assert clock.now >= 4 * gap > 3 * STEP_S


def test_a_step_past_its_bound_names_it_and_the_step_before(tmp_path, monkeypatch):
    clock = fake_clock(monkeypatch)
    out = tmp_path / "out"
    out.write_text("Under load\n")
    steps = [("its first step", at(clock, 1)), ("its second step", lambda: False)]
    with pytest.raises(AssertionError) as raised:
        wait_through(FakeRun(), out, "rerun", lambda: False, steps)
    assert str(raised.value) == (
        f"its second step did not come within {STEP_S} s of its first step;"
        " the script printed:\nUnder load\n"
    )
    assert 1 + STEP_S < clock.now < 2 + STEP_S


def test_a_script_that_exits_first_is_named_at_once(tmp_path, monkeypatch):
    clock = fake_clock(monkeypatch)
    out = tmp_path / "out"
    out.write_text("pytest exited 1\n")
    steps = [("its first step", at(clock, 1)), ("its second step", lambda: False)]
    with pytest.raises(AssertionError) as raised:
        wait_through(FakeRun(polls=20), out, "rerun", lambda: False, steps)
    assert str(raised.value) == (
        "the script exited 1 before its second step, after its first step;"
        " it printed:\npytest exited 1\n"
    )
    assert clock.now < 3


def test_a_script_that_ends_as_its_wait_reads_it_is_no_early_exit(
    tmp_path, monkeypatch
):
    """The script ends between the wait's check of the end and its check
    for an early exit, so the end, not an exit before it, is what it saw."""
    fake_clock(monkeypatch)
    assert wait_ended(FakeRun(polls=1, code=143), tmp_path / "out") == 143


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
    """The lines a waiting run printed under `held by:`, up to its place in
    the line."""
    text = out.read_text()
    if "held by:" not in text:
        return None
    block = []
    for line in text.split("held by:\n", 1)[1].splitlines():
        if not line.startswith("  "):
            break
        block.append(line)
    return block


def first_in_line(out):
    """Whether the run whose output is `out` has printed that it is first
    in the line: from then on it tries the load lock once a second."""
    return "Place 1 in line" in out.read_text()


def entries(lock):
    """The names of the entries in `lock`'s line, in the order they came."""
    line = Path(f"{os.path.realpath(lock)}.line")
    return sorted(p.name for p in line.glob("[0-9]*.[0-9]*"))


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
        # It waits first in the line, the place that takes the lock next.
        wait_until(lambda: first_in_line(out), "it first in the line", 10)
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
        # Out of the line once it holds the lock.
        assert entries(lock) == []
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
        wait_until(lambda: first_in_line(out), "the run waiting")
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

# A stand-in hand-off: it records the lock it was told it holds, by a
# rename as HOLD does, then holds its run open until the test writes `go`
# beside it, for at most 60 s.
HAND_OFF = (
    'echo "$DERBYNET_LOAD_LOCK_HELD" > started.tmp; mv started.tmp started; '
    "for _ in $(seq 600); do [ -e go ] && exit 0; sleep 0.1; done; exit 1"
)


def hold(
    tmp_path, lock, command=HAND_OFF, name="handoff", extra_env=None, script=HOLD_LOCK
):
    """tools/hold-load-lock.sh (or `script`, a copy of it) running
    `command` through bash in `tmp_path/name`, in a session of its own,
    with its output in `tmp_path/name.out`, and `extra_env` added to its
    environment."""
    folder = tmp_path / name
    folder.mkdir()
    out = open(tmp_path / f"{name}.out", "w")
    env = {**os.environ, "DERBYNET_LOAD_LOCK": str(lock), **(extra_env or {})}
    env.pop("DERBYNET_LOAD_LOCK_HELD", None)
    run = subprocess.Popen(
        [*RESET_SIGNALS_ARGV, str(script), "bash", "-c", command],
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
    hand-off first in the line takes the lock as the holder lets it go,
    though a load run starts at once after the release, and that run then
    waits behind the hand-off and names it."""
    lock = tmp_path / "load.lock"
    lock.write_text("pid 1, since then\n")
    held = os.open(lock, os.O_RDONLY)
    fcntl.flock(held, fcntl.LOCK_EX)
    handoff = hold(tmp_path, lock)
    load = None
    try:
        out = tmp_path / "handoff.out"
        wait_until(lambda: first_in_line(out), "it first in the line", 30)
        assert "Waiting for the load lock" in out.read_text()
        assert holder_block(out) == ["  pid 1, since then"]
        fcntl.flock(held, fcntl.LOCK_UN)
        load = start(tmp_path, "load", lock, go=True)
        wait_until((tmp_path / "handoff" / "started").exists, "the hand-off's command")
        assert (tmp_path / "handoff" / "started").read_text() == f"{lock}\n"
        load_out = tmp_path / "load.out"
        wait_until(lambda: "Waiting" in load_out.read_text(), "the load run waiting")
        assert not (tmp_path / "load" / "started").exists()
        # It prints the holder's lines, then its place (a read on
        # "Waiting" alone found no holder printed yet, 1 of 25 under load).
        wait_until(lambda: first_in_line(load_out), "its place", 30)
        # It names the hand-off: as the holder, or as the run ahead of it
        # when it joined in the up to 1 s before the hand-off's try took the
        # lock.
        said = load_out.read_text()
        assert f"  pid {handoff.pid}, since " in said, said
        assert f"; args hold-load-lock.sh: bash -c {HAND_OFF}" in said or (
            f"\n  args hold-load-lock.sh: bash -c {HAND_OFF}\n" in said
        ), said
        # The hand-off left the line once it held the lock; the load run's
        # ticket is 2 when it joined before that.
        assert [entry.split(".")[1] for entry in entries(lock)] == [str(load.pid)]
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
        # No such file, so a run that is not refused ends at once; the
        # hand-off's `true` ends at once too, and it takes no option.
        [str(script), *(["true"] if script == HOLD_LOCK else ["--loops", "0", "x.py"])],
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


def held_lock(tmp_path):
    """The test's own lock, held by the test: (its path, the held fd)."""
    lock = tmp_path / "load.lock"
    lock.write_text("pid 1, since then\n")
    held = os.open(lock, os.O_RDONLY)
    fcntl.flock(held, fcntl.LOCK_EX)
    return lock, held


def start_waiting(tmp_path, script, lock, extra_env=None, copy=None, under=()):
    """`script`, or `copy`, a copy of it, started on `lock`, which the test
    holds, once it is first in the line and tries the lock: the run; `under`
    is start()'s, for pytest-under-load.sh only.  A run that ends first,
    such as one refused under `timeout`, fails at once with the script's
    output: #59's runs under a `timeout 300` read "no flock under it within
    30 s" (#407)."""
    path = copy or script
    if script == SCRIPT:
        run = start(
            tmp_path, "waiter", lock, extra_env, go=True, under=under, script=path
        )
    else:
        assert not under, "hold() starts hold-load-lock.sh under nothing"
        run = hold(tmp_path, lock, name="waiter", extra_env=extra_env, script=path)
    out = tmp_path / "waiter.out"
    wait_until(
        lambda: run.poll() is not None or first_in_line(out),
        "it first in the line",
        30,
    )
    if run.returncode is not None:
        raise AssertionError(
            f"the run exited {run.returncode} before it was first in the line;"
            f" the script printed:\n{out.read_text()}"
        )
    # The run is the script asked for.
    assert str(path) in Path(f"/proc/{run.pid}/cmdline").read_text()
    return run


SCRIPTS = pytest.mark.parametrize("script", [SCRIPT, HOLD_LOCK], ids=lambda p: p.name)

# A `kill` read through BASH_ENV that logs each call's arguments into
# PAUSE_DIR/kills, then kills as the builtin does.
LOGGED_KILL = """kill() {
  echo "$*" >> "$PAUSE_DIR/kills"
  builtin kill "$@"
}
"""

# LOGGED_KILL, and a `pause_at NAME` that writes `at-NAME` into PAUSE_DIR
# and waits there, for at most 30 s, until the test writes `go-NAME`.
PAUSE_AT = (
    LOGGED_KILL
    + """pause_at() {
  : > "$PAUSE_DIR/at-$1"
  for _ in $(seq 300); do [ -e "$PAUSE_DIR/go-$1" ] && break; sleep 0.1; done
}
"""
)


def logged_kill(tmp_path):
    """The environment that gives a script LOGGED_KILL and PAUSE_AT's
    pause_at, writing into `tmp_path`."""
    (tmp_path / "hooks.sh").write_text(PAUSE_AT)
    return {"BASH_ENV": str(tmp_path / "hooks.sh"), "PAUSE_DIR": str(tmp_path)}


def kill_calls(tmp_path):
    """The arguments of each kill LOGGED_KILL logged, a line each."""
    kills = tmp_path / "kills"
    return kills.read_text().splitlines() if kills.exists() else []


def kill_args(tmp_path):
    """Every argument of the kills LOGGED_KILL logged, in one list."""
    return " ".join(kill_calls(tmp_path)).split()


LINE = Path(__file__).resolve().parents[1] / "tools" / "load-lock-line.sh"


def paused_copy(tmp_path, script, after, name):
    """injected_copy() that runs `pause_at NAME` after the line."""
    return injected_copy(tmp_path, script, after, f"  pause_at {name}\n")


def injected_copy(tmp_path, script, after, text):
    """A copy of `script` in `tmp_path/tools`, beside a copy of LINE, the
    file it sources, with `text` (whole lines) right after the one line of
    the two that matches `after` (a regex the whole line matches)."""
    folder = tmp_path / "tools"
    folder.mkdir(exist_ok=True)
    found = []
    for source in (script, LINE):
        lines = source.read_text().splitlines(keepends=True)
        at = [
            i for i, line in enumerate(lines) if re.fullmatch(after, line.rstrip("\n"))
        ]
        found += [source.name] * len(at)
        for i in reversed(at):
            lines.insert(i + 1, text)
        (folder / source.name).write_text("".join(lines))
    assert len(found) == 1, found
    copy = folder / script.name
    copy.chmod(0o755)
    return copy


def signal_at_the_pause(tmp_path, run, name):
    """TERM to `run` once it waits in `pause_at NAME`, then let it go on."""
    wait_until((tmp_path / f"at-{name}").exists, f"the pause at {name}", 30)
    os.kill(run.pid, signal.SIGTERM)
    (tmp_path / f"go-{name}").write_text("")


# The line right after a waiter's try of the load lock, under the mutex.
TRIED = r"      flock -n -x 9 7>&- 8>&- \|\| try_status=\$\?"


def paused_at_a_try(tmp_path, script, status):
    """A copy of `script` that runs `pause_at tried` right after a try of
    the load lock whose flock exited `status`: 1 found it held, 0 took it."""
    return injected_copy(
        tmp_path,
        script,
        TRIED,
        f'      if [ "$try_status" = {status} ]; then pause_at tried; fi\n',
    )


def at_a_try(tmp_path, script, took):
    """A waiter on the test's held lock, paused right after a try that found
    the lock held, or with `took`, right after the try that took it once the
    test let it go: (the run, the lock, the test's fd or None)."""
    copy = paused_at_a_try(tmp_path, script, 0 if took else 1)
    lock, held = held_lock(tmp_path)
    if took:
        run = start_waiting(tmp_path, script, lock, logged_kill(tmp_path), copy)
        os.close(held)
        held = None
    else:
        run = join(tmp_path, script, "waiter", lock, logged_kill(tmp_path), copy)
    wait_until((tmp_path / "at-tried").exists, "the pause after a try", 30)
    return run, lock, held


@pytest.mark.parametrize("took", [False, True], ids=["held", "took"])
@SCRIPTS
def test_a_signal_right_after_its_try_frees_both_locks(tmp_path, script, took):
    """A TERM right after the try, under the line's mutex, held there by
    pause_at: whether the try found the lock held or took it, the run ends
    with 143 and leaves no entry, the mutex free, and runs no command
    (plan/AGENT-BRIEF.md: a signal at each line between starting a child and
    recording it)."""
    run, lock, held = at_a_try(tmp_path, script, took)
    try:
        assert not free(f"{lock}.line/mutex")
        signal_at_the_pause(tmp_path, run, "tried")
        assert run.wait(timeout=30) == 143
        assert entries(lock) == []
        assert free(f"{lock}.line/mutex")
        assert not (tmp_path / "waiter" / "started").exists()
        if held is not None:
            os.close(held)
            held = None
        assert free(lock)
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, run)


@SCRIPTS
def test_a_sigkill_as_it_takes_the_lock_lets_the_next_run_take_it(tmp_path, script):
    """A SIGKILL right after a try that took the lock, while the run holds
    the lock and the mutex: the kernel frees both, and the next run takes
    the lock at once (plan/AGENT-BRIEF.md: a SIGKILL followed by the next
    run)."""
    run, lock, _ = at_a_try(tmp_path, script, took=True)
    following = None
    try:
        assert not free(lock)
        os.kill(run.pid, signal.SIGKILL)
        run.wait(timeout=10)
        following = start(tmp_path, "next", lock)
        wait_started(tmp_path, "next")
        assert "Waiting" not in (tmp_path / "next.out").read_text()
        assert entries(lock) == []
    finally:
        finish(tmp_path, run, *([following] if following else []))


# PAUSE_AT, and a `sleep` that, called as the line's `sleep 1` between two
# reads, writes `at-sleep` and waits for `go-sleep`, read through BASH_ENV.
PAUSED_SLEEP = (
    PAUSE_AT.replace("sleep 0.1", "command sleep 0.1")
    + """sleep() {
  if [ "$*" = 1 ]; then
    : > "$PAUSE_DIR/at-sleep"
    for _ in $(seq 300); do
      [ -e "$PAUSE_DIR/go-sleep" ] && return
      command sleep 0.1
    done
  else
    command sleep "$@"
  fi
}
"""
)


@SCRIPTS
def test_a_waiter_killed_in_its_sleep_leaves_no_lock_held(tmp_path, script):
    """The line's mutex is held only for a read and a try, never across the
    sleep between tries, and the sleep gets no copy of the entry or the
    load lock: while the live run sleeps, held there by PAUSED_SLEEP, the
    mutex is free; with the run SIGKILLed while its sleep lives on, the
    mutex, its entry and, once the test lets it go, the load lock are
    free."""
    (tmp_path / "paused_sleep.sh").write_text(PAUSED_SLEEP)
    env = {"BASH_ENV": str(tmp_path / "paused_sleep.sh"), "PAUSE_DIR": str(tmp_path)}
    lock, held = held_lock(tmp_path)
    run = start_waiting(tmp_path, script, lock, env)
    try:
        wait_until((tmp_path / "at-sleep").exists, "its sleep", 30)
        assert alive(run.pid)
        assert free(f"{lock}.line/mutex")
        os.kill(run.pid, signal.SIGKILL)
        run.wait(timeout=10)
        [entry] = entries(lock)
        assert free(f"{lock}.line/mutex")
        assert free(f"{os.path.realpath(lock)}.line/{entry}")
        os.close(held)
        held = None
        assert free(lock)
    finally:
        (tmp_path / "go-sleep").write_text("")
        if held is not None:
            os.close(held)
        finish(tmp_path, run)


# A stand-in flock(1) first on PATH: a `flock -n -x 9`, a try of the load
# lock, lists the descriptors it was started with into PAUSE_DIR/fds.PID,
# runs the real one, and once it has ended writes its exit status into
# PAUSE_DIR/status.PID.
FD_FLOCK = """#! /bin/sh
if [ "$*" = "-n -x 9" ]; then
  ls /proc/$$/fd > "$PAUSE_DIR/fds.$$"
  {flock} "$@"
  status=$?
  echo "$status" > "$PAUSE_DIR/status.$$"
  exit "$status"
fi
exec {flock} "$@"
"""


def try_statuses(folder):
    """The exit status of each try of the load lock FD_FLOCK has seen end."""
    return [path.read_text().strip() for path in folder.glob("status.*")]


@SCRIPTS
def test_a_try_of_the_load_lock_gets_no_copy_of_the_mutex_or_the_entry(
    tmp_path, script
):
    """No child keeps a copy of either lock (#419): each `flock -n -x 9`,
    the join's and each try of its turn, starts with neither fd 7, the
    line's mutex, nor fd 8, its entry. Its join and at least two tries of
    its turn have ended finding the test's lock held before the test lets
    it go, then a try takes it. A count of the tries begun, not ended, let
    the test free the lock during the third, which then took it, and CI
    saw 3 tries where the test wanted 4 (bc28196f)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "flock").write_text(FD_FLOCK.format(flock=shutil.which("flock")))
    (bin_dir / "flock").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "PAUSE_DIR": str(tmp_path)}
    lock, held = held_lock(tmp_path)
    run = join(tmp_path, script, "waiter", lock, env)
    try:
        wait_until(
            lambda: try_statuses(tmp_path).count("1") >= 3,
            "three tries ended finding the lock held",
            30,
        )
        os.close(held)
        held = None
        assert run.wait(timeout=90) == 0
        statuses = try_statuses(tmp_path)
        assert statuses.count("0") == 1, statuses
        assert statuses.count("1") >= 3, statuses
        tries = [path.read_text().split() for path in tmp_path.glob("fds.*")]
        assert len(tries) == len(statuses), (tries, statuses)
        for fds in tries:
            assert "9" in fds and "7" not in fds and "8" not in fds, fds
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, run)


@pytest.mark.parametrize("sig", ["TERM", "INT", "HUP"])
@SCRIPTS
def test_a_signal_while_it_waits_for_the_lock_stops_it(tmp_path, script, sig):
    """#372's implementer stopped a queued run of each script with TERM,
    and each left its `flock -x 9` waiting on the lock (#391); INT and HUP
    reach the same trap. First in the line, it now tries the lock once a
    second and leaves no child waiting on it: once it ends, the test's
    lock is the only one held."""
    lock, held = held_lock(tmp_path)
    run = start_waiting(tmp_path, script, lock)
    try:
        os.kill(run.pid, getattr(signal, f"SIG{sig}"))
        assert run.wait(timeout=30) == 143
        assert entries(lock) == []
        os.close(held)
        held = None
        assert free(lock)
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, run)


@pytest.mark.parametrize("sig", ["TERM", "INT", "HUP"])
@SCRIPTS
def test_a_signal_reaches_the_trap_though_pytest_ignores_it(tmp_path, script, sig):
    """A pytest that a non-interactive shell started with `&`, as
    pytest-under-load.sh and hold-load-lock.sh start theirs, ignores SIGINT;
    the scripts it started ignored it too, and an INT left each waiting
    until the test's 30 s wait ran out (#407). A TERM or HUP that pytest
    ignores is reset the same way."""
    number = getattr(signal, f"SIG{sig}")
    before = signal.signal(number, signal.SIG_IGN)
    try:
        assert f"SIG{sig}" in ignored(os.getpid())
        lock, held = held_lock(tmp_path)
        run = start_waiting(tmp_path, script, lock)
    finally:
        signal.signal(number, before)
    try:
        # RESET_SIGNALS_ARGV left it none of its five ignored, Python's own
        # PIPE and XFSZ included; spelled out rather than read from
        # RESET_NAMES, so a name dropped there fails here.
        five = {"SIGTERM", "SIGINT", "SIGHUP", "SIGPIPE", "SIGXFSZ"}
        assert not ignored(run.pid) & five, ignored(run.pid)
        os.kill(run.pid, number)
        assert run.wait(timeout=30) == 143
        assert entries(lock) == []
    finally:
        os.close(held)
        finish(tmp_path, run)


def test_a_command_the_scripts_start_ignores_sigint(tmp_path):
    """From a pytest with SIGINT at its default, a non-interactive bash
    starts a command in the foreground with SIGINT at its default and one
    with `&` with it ignored, and so pytest-under-load.sh's pytest and
    hold-load-lock.sh's command ignore it: the fact RESET_SIGNALS_ARGV's
    comment states (#407)."""
    status = "cat /proc/self/status"
    env = {**os.environ, "DERBYNET_LOAD_LOCK": str(tmp_path / "own.lock")}
    env.pop("DERBYNET_LOAD_LOCK_HELD", None)
    before = signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        foreground, job, held = (
            subprocess.run(
                argv, capture_output=True, text=True, timeout=60, env=env, check=True
            ).stdout
            for argv in (
                ["bash", "-c", status],
                ["bash", "-c", f"{status} & wait"],
                [str(HOLD_LOCK), "bash", "-c", status],
            )
        )
        code, out, _ = load_run_of(
            tmp_path,
            "    from pathlib import Path\n"
            "    status = Path('/proc/self/status').read_text()\n"
            "    Path(__file__).with_name('status').write_text(status)",
        )
    finally:
        signal.signal(signal.SIGINT, before)
    assert code == 0, out
    assert "SIGINT" not in ignored_in(foreground)
    assert "SIGINT" in ignored_in(job)
    # The load run's pytest and the hand-off's command ignore SIGINT, and
    # not SIGTERM, which start_held_rerun's callers send first.
    for text in (held, (tmp_path / "status").read_text()):
        assert "SIGINT" in ignored_in(text)
        assert "SIGTERM" not in ignored_in(text)


def test_start_waiting_starts_hold_load_lock_sh_under_nothing(tmp_path):
    """start_waiting's `under` is for pytest-under-load.sh only: hold()
    takes none, and it refuses one before it starts a run."""
    with pytest.raises(AssertionError, match="under nothing"):
        start_waiting(tmp_path, HOLD_LOCK, tmp_path / "load.lock", under=("nice",))
    assert not (tmp_path / "waiter.out").exists()


def test_a_run_refused_before_its_flock_is_named_at_once(tmp_path):
    """Under `timeout` the script refuses before the line, and the wait for
    its place says so at once: #59's runs under a `timeout 300` waited 30 s
    each and read "no flock under it within 30 s" (#407)."""
    lock, held = held_lock(tmp_path)
    began = time.monotonic()
    try:
        with pytest.raises(AssertionError, match="it runs under timeout") as error:
            start_waiting(tmp_path, SCRIPT, lock, under=("timeout", "300"))
        assert "exited 2 before it was first in the line" in str(error.value)
        assert time.monotonic() - began < 30
    finally:
        os.close(held)


@pytest.mark.parametrize(
    "signals", [("TERM", "TERM"), ("TERM", "INT")], ids=["TERM-TERM", "TERM-INT"]
)
@SCRIPTS
def test_a_second_signal_while_it_stops_its_wait_still_leaves_the_line(
    tmp_path, script, signals
):
    """plan/AGENT-BRIEF.md: a second signal during the cleanup, here held
    inside it by pause_at, after it removes its entry: the cleanup goes on,
    and the run ends with 143, no entry and the mutex free (#391). Waiting
    first in the line, it has no child for the cleanup to kill."""
    copy = paused_copy(tmp_path, script, r"    rm -f -- \"\$ENTRY\"", "left")
    lock, held = held_lock(tmp_path)
    run = start_waiting(tmp_path, script, lock, logged_kill(tmp_path), copy)
    try:
        first, second = (getattr(signal, f"SIG{name}") for name in signals)
        os.kill(run.pid, first)
        wait_until((tmp_path / "at-left").exists, "the cleanup", 30)
        os.kill(run.pid, second)
        (tmp_path / "go-left").write_text("")
        assert run.wait(timeout=30) == 143
        assert entries(lock) == []
        assert free(f"{lock}.line/mutex")
        assert kill_calls(tmp_path) == []
    finally:
        os.close(held)
        finish(tmp_path, run)


@pytest.mark.parametrize("waits", [False, True], ids=["free", "after-a-wait"])
@SCRIPTS
def test_a_signal_after_it_took_the_lock_kills_each_child_once(tmp_path, script, waits):
    """On a free lock or after a wait: once it holds the lock, the cleanup
    kills each child it started once, from its own list, and nothing else
    (#391)."""
    env = logged_kill(tmp_path)
    lock = tmp_path / "load.lock"
    held = None
    if waits:
        lock, held = held_lock(tmp_path)
    if script == SCRIPT:
        run = start(tmp_path, "first", lock, env)
    else:
        run = hold(tmp_path, lock, extra_env=env)
    try:
        if waits:
            name = "first" if script == SCRIPT else "handoff"
            out = tmp_path / f"{name}.out"
            wait_until(lambda: first_in_line(out), "it first in the line", 30)
            os.close(held)
            held = None
        if script == SCRIPT:
            wait_started(tmp_path, "first")
            # Its loop and its pytest.
            expected = sorted(str(pid) for pid, _ in child_pids(run.pid))
            assert len(expected) == 2
        else:
            wait_until(
                (tmp_path / "handoff" / "started").exists, "the hand-off's command"
            )
            [(command, _)] = child_pids(run.pid)
            expected = [f"-TERM -- -{command}"]
        os.kill(run.pid, signal.SIGTERM)
        assert run.wait(timeout=30) == 143
        assert sorted(kill_calls(tmp_path)) == expected
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, run)


@SCRIPTS
def test_after_a_sigkill_while_it_waits_the_next_run_takes_the_lock(tmp_path, script):
    """A SIGKILL skips the trap; once the lock frees, the next run starts
    at once (plan/AGENT-BRIEF.md: a SIGKILL followed by the next run)."""
    lock, held = held_lock(tmp_path)
    run = start_waiting(tmp_path, script, lock)
    following = None
    try:
        os.kill(run.pid, signal.SIGKILL)
        run.wait(timeout=10)
        # Its entry stays until the next run finds its owner dead.
        assert entries(lock) == [f"0000000001.{run.pid}"]
        os.close(held)
        held = None
        following = start(tmp_path, "next", lock)
        wait_started(tmp_path, "next")
        assert "Waiting" not in (tmp_path / "next.out").read_text()
        assert entries(lock) == []
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, run, *([following] if following else []))


# A hand-off command that adds its folder's name to `order` beside it, as
# HOLD does, and ends at once.
ORDER_HAND_OFF = 'echo "${PWD##*/}" >> ../order'


def join(tmp_path, script, name, lock, extra_env=None, copy=None):
    """`script`, or `copy`, a copy of it, started on `lock` with a stub or
    a command that adds `name` to `tmp_path/order` and ends at once."""
    path = copy or script
    if script == SCRIPT:
        run = start(tmp_path, name, lock, extra_env, go=True, script=path)
    else:
        run = hold(tmp_path, lock, ORDER_HAND_OFF, name, extra_env, path)
    return run


def join_at(tmp_path, script, name, lock, place):
    """join(), once the run has printed that it is at `place` in the line."""
    run = join(tmp_path, script, name, lock)
    out = tmp_path / f"{name}.out"
    try:
        wait_until(lambda: f"Place {place} in line" in out.read_text(), "", 30)
    except AssertionError:
        raise AssertionError(
            f"{name} not at place {place} within 30 s; it printed:\n{out.read_text()}"
        ) from None
    return run


def order(tmp_path):
    """The names the runs added to `tmp_path/order`, in the order they ran."""
    return (tmp_path / "order").read_text().split()


def places(out):
    """The places a waiting run printed, in the order it printed them."""
    return [int(n) for n in re.findall(r"^Place (\d+) in line", out.read_text(), re.M)]


# How long the first waiter stays held after the holder lets go: time enough
# for a waiter that skipped the line to start and take the free lock first.
SLOW_HEAD_S = 3

# PAUSE_AT, and an flock that runs `pause_at turn` before a waiter's first
# read of the line once it has joined (its `flock -x 7` with an entry),
# read through BASH_ENV.
SLOW_HEAD = (
    PAUSE_AT
    + """flock() {
  if [ "$*" = "-x 7" ] && [ -n "$ENTRY" ]; then pause_at turn; fi
  command flock "$@"
}
"""
)


@SCRIPTS
def test_waiters_take_the_lock_in_the_order_they_came(tmp_path, script):
    """On 2026-11-18 a queued step lost the lock to a run that started
    waiting after it (#417). Three waiters join behind the holder one by
    one, the third naming the lock by a link to it. SLOW_HEAD holds the
    first before it waits on the lock, as the scheduler runs a waiter late
    under load, for SLOW_HEAD_S after the holder lets go; the two behind it
    wait their turn, and so does a run that comes while the lock is free.
    Each prints its place again only when it moves up."""
    (tmp_path / "slow_head.sh").write_text(SLOW_HEAD)
    env = {"BASH_ENV": str(tmp_path / "slow_head.sh"), "PAUSE_DIR": str(tmp_path)}
    lock, held = held_lock(tmp_path)
    (tmp_path / "link.lock").symlink_to(lock)
    runs = []
    try:
        runs.append(join(tmp_path, script, "first", lock, env))
        wait_until((tmp_path / "at-turn").exists, "the first at its turn", 30)
        assert entries(lock) == [f"0000000001.{runs[0].pid}"]
        runs.append(join_at(tmp_path, script, "second", lock, 2))
        runs.append(join_at(tmp_path, script, "third", tmp_path / "link.lock", 3))
        os.close(held)
        held = None
        late = join(tmp_path, script, "late", lock)
        runs.append(late)
        # Until it joins the line, or takes the free lock past it, which the
        # order below then names.
        wait_until(
            lambda: (
                any(e.endswith(f".{late.pid}") for e in entries(lock))
                or lock.read_text().startswith(f"pid {late.pid},")
            ),
            "the late run in the line or holding the lock",
            30,
        )
        time.sleep(SLOW_HEAD_S)
        (tmp_path / "go-turn").write_text("")
        assert [run.wait(timeout=90) for run in runs] == [0, 0, 0, 0]
        assert order(tmp_path) == ["first", "second", "third", "late"]
        assert entries(lock) == []
        # Each printed its place once each time it moved up, up to 1.
        for name in ("second", "third", "late"):
            moved = places(tmp_path / f"{name}.out")
            assert moved == sorted(set(moved), reverse=True), (name, moved)
            assert moved[-1] == 1, (name, moved)
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, *runs)


@pytest.mark.parametrize("killed", ["first", "second"])
@SCRIPTS
def test_a_waiter_killed_in_line_does_not_block_the_ones_behind(
    tmp_path, script, killed
):
    """A SIGKILL skips the trap, so the killed waiter's entry stays in the
    line; its flock on that entry ends with it, and the waiters behind it
    pass it. The third prints its place again each time it moves up, and names the runs
    ahead of it."""
    lock, held = held_lock(tmp_path)
    names = ["first", "second", "third"]
    runs = {}
    try:
        for place, name in enumerate(names, 1):
            runs[name] = join_at(tmp_path, script, name, lock, place)
        third = (tmp_path / "third.out").read_text()
        for name in ("first", "second"):
            assert re.search(rf"^  pid {runs[name].pid}, since .*; cwd ", third, re.M)
        os.kill(runs[killed].pid, signal.SIGKILL)
        runs[killed].wait(timeout=10)
        wait_until(
            lambda: places(tmp_path / "third.out")[-1] == 2, "the third at place 2", 30
        )
        os.close(held)
        held = None
        rest = [name for name in names if name != killed]
        assert [runs[name].wait(timeout=90) for name in rest] == [0, 0]
        assert order(tmp_path) == rest
        assert places(tmp_path / "third.out") == [3, 2, 1]
        assert entries(lock) == []
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, *runs.values())


@pytest.mark.parametrize("sig", ["TERM", "INT", "HUP"])
@SCRIPTS
def test_a_signal_to_a_waiter_behind_another_takes_it_out_of_the_line(
    tmp_path, script, sig
):
    """The run behind the first waiter sleeps between its reads of the line
    rather than waiting in flock; a signal there ends it with no entry left,
    and the first keeps its place."""
    lock, held = held_lock(tmp_path)
    first = hold(tmp_path, lock, name="first")
    run = None
    try:
        wait_until(
            lambda: first_in_line(tmp_path / "first.out"), "the first waiting", 30
        )
        run = join_at(tmp_path, script, "second", lock, 2)
        os.kill(run.pid, getattr(signal, f"SIG{sig}"))
        assert run.wait(timeout=30) == 143
        assert entries(lock) == [f"0000000001.{first.pid}"]
    finally:
        os.close(held)
        finish(tmp_path, first, *([run] if run else []))


@pytest.mark.parametrize(
    "after",
    [
        r"  exec 8>\"\$ENTRY\"",
        r"  flock -x 8",
        r"  printf 'kind %s\\n%s\\n' \"\$1\" \"\$2\" >&8",
    ],
    ids=["created", "locked", "written"],
)
@SCRIPTS
def test_a_signal_as_it_joins_the_line_leaves_no_entry(tmp_path, script, after):
    """A TERM at each line between naming its entry and leaving the mutex,
    held there by pause_at (plan/AGENT-BRIEF.md: a signal at each line
    between starting a child and recording it). It leaves no entry, and the
    mutex is free."""
    copy = paused_copy(tmp_path, script, after, "join")
    lock, held = held_lock(tmp_path)
    run = join(tmp_path, script, "waiter", lock, logged_kill(tmp_path), copy)
    try:
        signal_at_the_pause(tmp_path, run, "join")
        assert run.wait(timeout=30) == 143
        assert entries(lock) == []
        assert free(f"{lock}.line/mutex")
    finally:
        os.close(held)
        finish(tmp_path, run)


@SCRIPTS
def test_a_signal_as_it_leaves_the_line_leaves_no_entry(tmp_path, script):
    """A TERM once it holds the lock, held by pause_at between removing its
    entry and forgetting it: the cleanup's second removal finds nothing,
    and the lock is free."""
    copy = paused_copy(tmp_path, script, r"    rm -f -- \"\$ENTRY\"", "left")
    lock, held = held_lock(tmp_path)
    run = start_waiting(tmp_path, script, lock, logged_kill(tmp_path), copy)
    try:
        os.close(held)
        held = None
        signal_at_the_pause(tmp_path, run, "left")
        assert run.wait(timeout=30) == 143
        assert entries(lock) == []
        assert free(lock)
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, run)


@SCRIPTS
def test_tickets_count_on_in_decimal_past_9(tmp_path, script):
    """Two live entries, tickets 7 and 8, each held by an flock the test
    owns: the next two waiters join as 9 and 10, read in base 10 (08 and 09
    are no octal numbers), and 10 waits behind 9, its name padded to sort
    after it. Once the test lets 7 and 8 go, 9 takes the lock first."""
    lock, held = held_lock(tmp_path)
    line = Path(f"{os.path.realpath(lock)}.line")
    line.mkdir()
    seeds = []
    runs = []
    try:
        for ticket in (7, 8):
            entry = line / f"{ticket:010d}.{os.getpid()}"
            entry.write_text(f"seed {ticket}\n")
            seeds.append(os.open(entry, os.O_RDONLY))
            fcntl.flock(seeds[-1], fcntl.LOCK_EX)
        runs.append(join_at(tmp_path, script, "ninth", lock, 3))
        runs.append(join_at(tmp_path, script, "tenth", lock, 4))
        assert entries(lock) == [
            f"0000000007.{os.getpid()}",
            f"0000000008.{os.getpid()}",
            f"0000000009.{runs[0].pid}",
            f"0000000010.{runs[1].pid}",
        ]
        assert "  seed 7\n  seed 8\n" in (tmp_path / "tenth.out").read_text()
        for fd in seeds:
            os.close(fd)
        seeds = []
        os.close(held)
        held = None
        assert [run.wait(timeout=90) for run in runs] == [0, 0]
        assert order(tmp_path) == ["ninth", "tenth"]
        assert entries(lock) == []
    finally:
        for fd in seeds:
            os.close(fd)
        if held is not None:
            os.close(held)
        finish(tmp_path, *runs)


# What a line folder holds besides the scripts' own entries, as (name, kind,
# whether the test holds an flock on it): a name that breaks the arithmetic
# on tickets, one with a suffix as an editor's backup has, a ticket of the
# wrong length, and a folder named as an entry, which a bare removal fails
# on.
STRAYS = [
    pytest.param("1abc.2", "file", True, id="not-a-ticket-held"),
    pytest.param("1abc.2", "file", False, id="not-a-ticket-free"),
    pytest.param("0000000003.4~", "file", True, id="backup-suffix-held"),
    pytest.param("0000000003.4~", "file", False, id="backup-suffix-free"),
    pytest.param("0.0", "file", True, id="short-ticket-held"),
    pytest.param("0000000005.7", "folder", False, id="folder-free"),
]


@pytest.mark.parametrize(("name", "kind", "held_by_test"), STRAYS)
@SCRIPTS
def test_a_stray_in_the_line_folder_is_ignored(
    tmp_path, script, name, kind, held_by_test
):
    """On the first head of this ticket an entry name the scripts did not
    make, one a test-owned flock held, broke the arithmetic that numbers the
    next ticket, bash dropped the whole join, and the run went on with no
    lock while another run held it (#417 round 3's N1). A stray, held or
    not, is no entry: the run takes place 1 and waits on the load lock the
    test holds, then runs once it lets go, and leaves the stray where it
    was."""
    lock, held = held_lock(tmp_path)
    line = Path(f"{os.path.realpath(lock)}.line")
    line.mkdir()
    stray = line / name
    if kind == "folder":
        stray.mkdir()
    else:
        stray.write_text("stray\n")
    stray_fd = os.open(stray, os.O_RDONLY)
    if held_by_test:
        fcntl.flock(stray_fd, fcntl.LOCK_EX)
    run = None
    try:
        run = start_waiting(tmp_path, script, lock)
        out = tmp_path / "waiter.out"
        assert "Place 1 in line: it takes the load lock next." in out.read_text()
        assert not (tmp_path / "waiter" / "started").exists()
        os.close(held)
        held = None
        (tmp_path / "waiter" / "go").write_text("")
        assert run.wait(timeout=60) == 0
        assert (tmp_path / "waiter" / "started").exists()
        assert stray.exists()
    finally:
        os.close(stray_fd)
        if held is not None:
            os.close(held)
        finish(tmp_path, *([run] if run else []))


# What a script says when a step before the lock fails.
JOIN_FAILED = "it could not join the line or take the load lock"

# A flock that fails its CALL-th call of `flock ARGS` with 65, neither the
# 0 of a lock taken nor the 1 of a lock held, read through BASH_ENV, and
# passes every other.
FAIL_FLOCK = """flock() {
  if [ "$*" = "$FAIL_ARGS" ]; then
    FAILS_SEEN=$((${FAILS_SEEN:-0} + 1))
    if [ "$FAILS_SEEN" = "$FAIL_CALL" ]; then return 65; fi
  fi
  command flock "$@"
}
"""

FAILING_STEPS = [
    pytest.param("-x 7", 1, id="the-mutex-of-the-join"),
    pytest.param("-x 8", 1, id="the-entry"),
    pytest.param("-x 7", 2, id="the-mutex-of-its-turn"),
    # The first `flock -n -x 9` is the join's, which takes a failure as a
    # held lock; the second is the first try of its turn.
    pytest.param("-n -x 9", 2, id="the-try-of-the-load-lock"),
]


@pytest.mark.parametrize(("args", "call"), FAILING_STEPS)
@SCRIPTS
def test_a_step_that_fails_before_the_lock_ends_the_run_with_2_and_no_command(
    tmp_path, script, args, call
):
    """The test holds the lock, so a run that went on past the failure
    would run its command beside the holder. Each step from joining the
    line to the wait on the lock, made to fail, ends the run with 2 and a
    message, runs no command, and leaves no entry."""
    (tmp_path / "fail_flock.sh").write_text(FAIL_FLOCK)
    env = {
        "BASH_ENV": str(tmp_path / "fail_flock.sh"),
        "FAIL_ARGS": args,
        "FAIL_CALL": str(call),
    }
    lock, held = held_lock(tmp_path)
    run = join(tmp_path, script, "waiter", lock, env)
    try:
        assert run.wait(timeout=30) == 2
        said = (tmp_path / "waiter.out").read_text()
        assert f"{script.name}: {JOIN_FAILED}" in said
        assert not (tmp_path / "order").exists()
        assert entries(lock) == []
        assert lock.read_text() == "pid 1, since then\n"
    finally:
        os.close(held)
        finish(tmp_path, run)


@SCRIPTS
def test_a_join_that_ends_with_no_verdict_ends_the_run_with_2_and_no_command(
    tmp_path, script
):
    """Bash drops a whole command, a join here, on an arithmetic error and
    goes on with the next, with no failed status for `set -e` or an ERR
    trap to see; the join's own division by zero stands for any such
    error. The run reads the join's verdict, and a join that gave none ends
    it with 2 and a message, not as the run that holds the lock."""
    copy = injected_copy(tmp_path, script, r"  line_scan", "  last=$((1 / 0))\n")
    lock, held = held_lock(tmp_path)
    run = join(tmp_path, script, "waiter", lock, copy=copy)
    try:
        assert run.wait(timeout=30) == 2
        said = (tmp_path / "waiter.out").read_text()
        assert f"{script.name}: {JOIN_FAILED}" in said
        assert not (tmp_path / "order").exists()
        assert entries(lock) == []
        assert lock.read_text() == "pid 1, since then\n"
    finally:
        os.close(held)
        finish(tmp_path, run)


@SCRIPTS
def test_a_script_beside_no_line_file_ends_the_run_with_2_and_no_command(
    tmp_path, script
):
    """A copy of the script with no load-lock-line.sh beside it, as a
    checkout missing the file, would find no functions to join the line
    with; it ends with 2 and names the file, and runs no command."""
    folder = tmp_path / "tools"
    folder.mkdir()
    copy = folder / script.name
    shutil.copy(script, copy)
    lock, held = held_lock(tmp_path)
    run = join(tmp_path, script, "waiter", lock, copy=copy)
    try:
        assert run.wait(timeout=30) == 2
        said = (tmp_path / "waiter.out").read_text()
        assert f"{script.name}: it cannot read {folder}/load-lock-line.sh" in said
        assert not (tmp_path / "order").exists()
    finally:
        os.close(held)
        finish(tmp_path, run)


def lock_with_no_room_for_a_line(tmp_path, closed):
    """A writable lock where no line can be made: `closed` is "folder", its
    folder taking no new file, or "line", its line's folder there already
    and taking no new file. (the folder to open again, the lock)."""
    folder = tmp_path / "locks"
    folder.mkdir()
    lock = folder / "load.lock"
    lock.write_text("")
    line = Path(f"{os.path.realpath(lock)}.line")
    shut = {"folder": folder, "line": line}[closed]
    shut.mkdir(exist_ok=True)
    shut.chmod(0o555)
    return shut, lock


NO_ROOM = pytest.mark.parametrize("closed", ["folder", "line"])


@NO_ROOM
def test_a_lock_that_takes_no_line_is_refused_by_name(tmp_path, closed):
    shut, lock = lock_with_no_room_for_a_line(tmp_path, closed)
    try:
        result = run(
            f"'{SCRIPT}' --loops 0 tests/test_pytest_under_load.py >/dev/null",
            out=str(tmp_path / "run"),
            lock=str(lock),
        )
    finally:
        shut.chmod(0o755)
    assert result.returncode == 2
    assert (
        f"pytest-under-load.sh: it cannot make the line {os.path.realpath(lock)}.line"
        in result.stderr
    )
    assert "Permission denied" not in result.stderr


@NO_ROOM
def test_a_hand_off_on_a_lock_that_takes_no_line_is_refused_by_name(tmp_path, closed):
    shut, lock = lock_with_no_room_for_a_line(tmp_path, closed)
    try:
        code, err = hold_with(tmp_path, ["true"], {"DERBYNET_LOAD_LOCK": str(lock)})
    finally:
        shut.chmod(0o755)
    assert code == 2
    assert (
        f"hold-load-lock.sh: it cannot make the line {os.path.realpath(lock)}.line"
        in err
    )
    assert "Permission denied" not in err


def test_a_run_from_before_the_line_never_runs_beside_a_holder(tmp_path):
    """A run from a branch cut before the line takes the load lock with a
    bare `flock -x` and no entry, so it may go ahead of the first waiter,
    but whichever takes the lock, the other starts only after it ends."""
    lock, held = held_lock(tmp_path)
    first = hold(tmp_path, lock, name="first")
    old = None
    try:
        wait_until(
            lambda: first_in_line(tmp_path / "first.out"), "the first waiting", 30
        )
        (tmp_path / "old").mkdir()
        old = subprocess.Popen(
            ["flock", "-x", str(lock), "bash", "-c", HAND_OFF],
            cwd=tmp_path / "old",
            start_new_session=True,
        )
        os.close(held)
        held = None
        started = {name: tmp_path / name / "started" for name in ("first", "old")}
        wait_until(
            lambda: any(path.exists() for path in started.values()), "a start", 30
        )
        winner = next(name for name, path in started.items() if path.exists())
        other = "old" if winner == "first" else "first"
        # One read, after a second in which the other could start beside it.
        time.sleep(1)
        assert not started[other].exists()
        (tmp_path / winner / "go").write_text("")
        wait_until(started[other].exists, f"{other} after {winner}", 30)
        (tmp_path / other / "go").write_text("")
        assert first.wait(timeout=30) == 0
        assert old.wait(timeout=30) == 0
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, first, *([old] if old else []))


# An flock that adds a line to PAUSE_DIR/reads at each read of the line,
# its `flock -x 7`, read through BASH_ENV.
COUNTED_READS = """flock() {
  if [ "$*" = "-x 7" ]; then echo >> "$PAUSE_DIR/reads"; fi
  command flock "$@"
}
"""


def counted_reads(tmp_path):
    """The environment that gives a script COUNTED_READS, counting into a
    folder of its own: (the environment, a function that reads the count
    once the script has read the line)."""
    folder = tmp_path / "counted"
    folder.mkdir()
    (folder / "hooks.sh").write_text(COUNTED_READS)
    reads = folder / "reads"

    def count():
        return len(reads.read_text().splitlines())

    return {"BASH_ENV": str(folder / "hooks.sh"), "PAUSE_DIR": str(folder)}, count


def behind(out):
    """The pids of the runs a waiting run last printed itself behind."""
    block = out.read_text().rsplit("in line, behind:\n", 1)[1]
    pids = []
    for line in block.splitlines():
        if not line.startswith("  "):
            break
        pids.append(int(re.match(r"  pid (\d+), since ", line).group(1)))
    return pids


def test_a_hand_off_takes_the_lock_before_a_load_run_waiting_ahead_of_it(tmp_path):
    """#406's 3-minute hand-off waited 7585 s, behind a 25-count load run
    that had queued before it (#419). A load run waits first in the line,
    then a hand-off joins behind it, held by SLOW_HEAD before its first
    read of the line, and the test lets the lock go. The load run reads the
    line twice with the lock free and takes nothing, since the hand-off is
    ahead of it; once the test lets the hand-off go on, it runs first, and
    the load run prints its place behind it, then first again."""
    (tmp_path / "slow_head.sh").write_text(SLOW_HEAD)
    slow = {"BASH_ENV": str(tmp_path / "slow_head.sh"), "PAUSE_DIR": str(tmp_path)}
    counted, reads = counted_reads(tmp_path)
    lock, held = held_lock(tmp_path)
    runs = []
    try:
        runs.append(join(tmp_path, SCRIPT, "load", lock, counted))
        load_out = tmp_path / "load.out"
        wait_until(lambda: first_in_line(load_out), "the load run first in line", 30)
        runs.append(join(tmp_path, HOLD_LOCK, "handoff", lock, slow))
        wait_until((tmp_path / "at-turn").exists, "the hand-off in the line", 30)
        os.close(held)
        held = None
        # Its first read from here may have begun before the release; the
        # second begins once the first, and its try, have ended.
        since = reads()
        wait_until(
            lambda: (tmp_path / "load" / "started").exists() or reads() >= since + 2,
            "two reads by the load run",
            30,
        )
        assert not (tmp_path / "load" / "started").exists()
        assert free(lock)
        (tmp_path / "go-turn").write_text("")
        assert [run.wait(timeout=90) for run in runs] == [0, 0]
        assert order(tmp_path) == ["handoff", "load"]
        assert places(load_out) == [1, 2, 1]
        assert behind(load_out) == [runs[1].pid]
        assert places(tmp_path / "handoff.out") == [1]
        assert entries(lock) == []
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, *runs)


def test_hand_offs_go_first_and_each_kind_keeps_the_order_it_came_in(tmp_path):
    """Two load runs join, then two hand-offs: each hand-off goes ahead of
    both load runs and behind the hand-off that came before it, and each
    load run prints its place again as each hand-off passes it. Once the
    test lets the lock go, they run hand-offs first, each kind in the order
    it came."""
    lock, held = held_lock(tmp_path)
    runs = {}
    try:
        runs["load1"] = join_at(tmp_path, SCRIPT, "load1", lock, 1)
        runs["load2"] = join_at(tmp_path, SCRIPT, "load2", lock, 2)
        out = {name: tmp_path / f"{name}.out" for name in ("load1", "load2")}
        for name, place in (("hand1", 1), ("hand2", 2)):
            runs[name] = join_at(tmp_path, HOLD_LOCK, name, lock, place)
            # Each load run reads the line once a second: each move shows
            # once it has.
            for load, moved in (("load1", place + 1), ("load2", place + 2)):
                wait_until(
                    lambda o=out[load], m=moved: places(o)[-1] == m,
                    f"{load} at place {moved}",
                    30,
                )
        out.update({name: tmp_path / f"{name}.out" for name in ("hand1", "hand2")})
        assert behind(out["load2"]) == [
            runs[name].pid for name in ("hand1", "hand2", "load1")
        ]
        assert behind(out["hand2"]) == [runs["hand1"].pid]
        os.close(held)
        held = None
        assert [run.wait(timeout=90) for run in runs.values()] == [0, 0, 0, 0]
        assert order(tmp_path) == ["hand1", "hand2", "load1", "load2"]
        assert places(out["hand1"]) == [1]
        assert places(out["hand2"]) == [2, 1]
        # Each load run moved down once for each hand-off, then up to 1.
        for name, start in (("load1", [1, 2, 3]), ("load2", [2, 3, 4])):
            moved = places(out[name])
            assert moved[:3] == start, (name, moved)
            rest = moved[2:]
            assert rest == sorted(set(rest), reverse=True), (name, moved)
            assert rest[-1] == 1, (name, moved)
        assert entries(lock) == []
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, *runs.values())


def test_an_entry_it_cannot_read_is_no_run_from_before_419(tmp_path):
    """An entry its owner removes between the scan's flock and its read of
    the kind, which an unreadable entry the test holds stands for, is no
    entry: it neither turns the line back to the order runs came in nor
    takes a place, so a hand-off that joins after it still goes ahead of
    the load run waiting first."""
    lock, held = held_lock(tmp_path)
    line = Path(f"{os.path.realpath(lock)}.line")
    runs = []
    seed = None
    try:
        runs.append(join_at(tmp_path, SCRIPT, "load", lock, 1))
        entry = line / f"{2:010d}.{os.getpid()}"
        entry.write_text("kind hand-off\n")
        seed = os.open(entry, os.O_RDONLY)
        fcntl.flock(seed, fcntl.LOCK_EX)
        entry.chmod(0)
        runs.append(join_at(tmp_path, HOLD_LOCK, "handoff", lock, 1))
        wait_until(
            lambda: places(tmp_path / "load.out")[-1] == 2, "the load run at 2", 30
        )
        os.close(held)
        held = None
        assert [run.wait(timeout=90) for run in runs] == [0, 0]
        assert order(tmp_path) == ["handoff", "load"]
    finally:
        if seed is not None:
            os.close(seed)
        if held is not None:
            os.close(held)
        finish(tmp_path, *runs)


# The copies of the three scripts from before #419, which a worktree cut
# before it still runs on the machine's lock.
BEFORE_419 = Path(__file__).resolve().parent / "load_lock_before_419"


def before_419(tmp_path, script):
    """A copy of `script` from before #419, beside its own line file, in
    `tmp_path/old/tools`."""
    folder = tmp_path / "old" / "tools"
    folder.mkdir(parents=True, exist_ok=True)
    for source in BEFORE_419.iterdir():
        shutil.copy(source, folder / source.name)
    copy = folder / script.name
    assert copy.stat().st_mode & 0o111, copy
    return copy


# (name, script, from before #419) for each run of a mixed line.
OLD_LOAD = ("oldload", SCRIPT, True)
OLD_HAND_OFF = ("oldhand", HOLD_LOCK, True)
NEW_LOAD = ("newload", SCRIPT, False)
NEW_HAND_OFF = ("newhand", HOLD_LOCK, False)


@pytest.mark.parametrize(
    "line",
    [
        pytest.param([OLD_LOAD, NEW_HAND_OFF], id="old-load-then-hand-off"),
        pytest.param([NEW_HAND_OFF, OLD_LOAD], id="hand-off-then-old-load"),
        pytest.param([OLD_HAND_OFF, NEW_LOAD], id="old-hand-off-then-load"),
        pytest.param([NEW_LOAD, OLD_HAND_OFF], id="load-then-old-hand-off"),
        pytest.param([OLD_LOAD, NEW_LOAD], id="old-load-then-load"),
        pytest.param([NEW_LOAD, OLD_LOAD], id="load-then-old-load"),
        pytest.param([OLD_HAND_OFF, NEW_HAND_OFF], id="old-hand-off-then-hand-off"),
        pytest.param([NEW_HAND_OFF, OLD_HAND_OFF], id="hand-off-then-old-hand-off"),
        # A rule that put hand-offs first beside an old run, which serves
        # tickets in order, made a cycle here: the new load run waited for
        # the hand-off, the hand-off for the old run, the old run for the
        # load run.
        pytest.param([NEW_LOAD, OLD_HAND_OFF, NEW_HAND_OFF], id="three"),
    ],
)
def test_a_line_with_a_run_from_before_419_keeps_the_order_they_came(tmp_path, line):
    """A worktree cut before #419 runs the old copies on the machine's lock
    (#419's Risks), and they serve the line in the order runs came: with
    one in the line, every run keeps that order, so none waits for ever,
    and each prints its place in it. Each run joins once the one before it
    has printed its place."""
    lock, held = held_lock(tmp_path)
    runs = []
    try:
        for place, (name, script, old) in enumerate(line, 1):
            copy = before_419(tmp_path, script) if old else None
            # An old load run's pytest starts in tmp_path/old, which holds
            # no Django project for pytest-django to find; its stub needs
            # none.
            env = {"PYTEST_ADDOPTS": "-p no:django"} if old else None
            runs.append(join(tmp_path, script, name, lock, env, copy))
            out = tmp_path / f"{name}.out"
            wait_until(
                lambda o=out, n=place: f"Place {n} in line" in o.read_text(),
                f"{name} at place {place}",
                30,
            )
        os.close(held)
        held = None
        assert [run.wait(timeout=90) for run in runs] == [0] * len(line)
        assert order(tmp_path) == [name for name, _, _ in line]
        assert entries(lock) == []
    finally:
        if held is not None:
            os.close(held)
        finish(tmp_path, *runs)


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
    out = tmp_path / "out"
    out.write_text("")
    run = subprocess.Popen(
        ["bash", "-c", command + " >out"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
            "DERBYNET_LOAD_LOCK": str(tmp_path / "load.lock"),
            "DERBYNET_BROWSER_FONT": "DejaVu Sans",
        },
        start_new_session=True,
    )
    try:
        code = wait_ended(run, out, load_run_steps(out, tmp_path / "run"))
    finally:
        finish(tmp_path, run)
    return code, out.read_text(), bin_dir / "journalctl.args"


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
    assert_reran(tmp_path)
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
        "  failure test_other::test_other: assert False\n"
    ) in out
    assert not (tmp_path / "run" / "rerun.log").exists()
    assert "Rerunning" not in out
    with pytest.raises(AssertionError) as raised:
        assert_reran(tmp_path)
    assert str(raised.value).startswith(
        "the script ended without the `Rerunning` line; it printed:\n"
    )


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
    assert_reran(tmp_path)
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
# until the test writes `go` beside it, for at most 60 s.  It writes
# `rerunning` by a rename, as HOLD writes `started`: start_held_rerun returns
# once the file exists and its callers read it at once (#407).
HELD_RERUN = (
    "import os\nimport time\nfrom pathlib import Path\n\n\n"
    "def test_loads():\n"
    "    here = Path(__file__).parent\n"
    "    if (here / 'failed-once').exists():\n"
    "        (here / 'rerunning.tmp').write_text(str(os.getpid()))\n"
    "        os.replace(here / 'rerunning.tmp', here / 'rerunning')\n"
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
        # No RESET_SIGNALS_ARGV: its callers send TERM, which a load run and a
        # hand-off leave at its default, and INT only after a TERM, when the
        # script's trap has set every signal it traps to ignored.
        run = subprocess.Popen(
            [str(SCRIPT), "--loops", "2", "-p", "no:cacheprovider", str(folder)],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=tmp_path,
            env=env,
            start_new_session=True,
        )
    try:
        wait_through(
            run,
            tmp_path / "held.out",
            "start of the rerun's stub",
            (folder / "rerunning").exists,
            load_run_steps(tmp_path / "held.out", tmp_path / "held-run")[:3],
        )
    except BaseException:
        # Its callers' finish() never gets the run: a rerun that hangs left
        # the script running after the test (#463's mutants).
        finish(tmp_path, run)
        raise
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
        out = tmp_path / "held.out"
        assert wait_ended(run, out, load_run_steps(out, tmp_path / "held-run")) == 0
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
        out = tmp_path / "second.out"
        steps = load_run_steps(out, tmp_path / "second-run")[:2]
        assert wait_ended(second, out, steps) == 0
        assert "Waiting" not in out.read_text()
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
    assert err == usage_line(HOLD_LOCK) + "\n"
    assert not (tmp_path / "load.lock").exists()


def usage_line(script):
    """The `# Usage:` line at the top of `script`, which the script prints
    for its usage."""
    (line,) = [
        x[2:] for x in script.read_text().splitlines() if x.startswith("# Usage: ")
    ]
    return line


def run_for_help(tmp_path, script, args):
    """`script` run with `args`, its lock in tmp_path: (exit code, stdout,
    stderr). Its output goes to pipes, which pytest-under-load.sh refuses
    only after it reads its options for -h and --help."""
    folder = tmp_path / "work"
    folder.mkdir(exist_ok=True)
    result = subprocess.run(
        [str(script), *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=60,
        cwd=folder,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "DERBYNET_LOAD_LOCK": str(tmp_path / "load.lock"),
            "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
        },
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize(
    "script, args",
    [
        (HOLD_LOCK, ["--help"]),
        (HOLD_LOCK, ["-h", "true"]),
        (SCRIPT, ["--help"]),
        (SCRIPT, ["-h"]),
        (
            SCRIPT,
            ["--postgres", "--loops", "4", "--flake-ticket", "--help", "-m", "browser"],
        ),
    ],
)
def test_help_prints_the_usage_and_takes_no_lock(tmp_path, script, args):
    """#434's `hold-load-lock.sh --help` waited in the lock's line to run a
    command named --help (#434 retro)."""
    code, out, err = run_for_help(tmp_path, script, args)
    assert (code, err) == (0, ""), out + err
    assert "[-h | --help]" in usage_line(script)
    assert out == (
        f"{usage_line(script)}\nThe comment at the top of {script} says more.\n"
    ), out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["work"]


def test_help_after_the_first_pytest_argument_reaches_pytest(tmp_path):
    """The Usage line says -h and --help are read among the script's own
    options, which end at the first pytest argument: `-p no:cacheprovider`
    does, so --help goes to pytest, under the lock. A stand-in `uv` records
    what it is run with, and the script's output goes to a file, since the
    script refuses a pipe once it has read its options."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\necho "$@" > "$UV_ARGS"\n')
    uv.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", f"'{SCRIPT}' --loops 0 -p no:cacheprovider --help >out"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "UV_ARGS": str(tmp_path / "uv-args"),
            "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run"),
            "DERBYNET_LOAD_LOCK": str(tmp_path / "load.lock"),
        },
    )
    summary = (tmp_path / "out").read_text()
    assert result.returncode == 0, summary + result.stderr
    assert usage_line(SCRIPT) not in summary, summary
    argv = (tmp_path / "uv-args").read_text().split()
    assert argv[:2] == ["run", "pytest"] and argv[-3:] == [
        "-p",
        "no:cacheprovider",
        "--help",
    ]
    assert (tmp_path / "load.lock").exists()


@pytest.mark.parametrize("option", ["-x", "--", "--help-me", "-hv"])
def test_a_hand_off_whose_command_starts_with_a_dash_is_refused(tmp_path, option):
    code, out, err = run_for_help(tmp_path, HOLD_LOCK, [option, "true"])
    assert (code, out) == (2, ""), err
    assert err == (
        "hold-load-lock.sh: it takes no option, and no COMMAND starts with -: "
        f"{option}\n{usage_line(HOLD_LOCK)}\n"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["work"]


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
