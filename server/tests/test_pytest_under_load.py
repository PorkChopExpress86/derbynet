"""`tools/pytest-under-load.sh` refuses a pipe and a `timeout` before it
starts anything: #47 and #48 each lost a 25-repeat run to
`timeout 3000 ... | grep` (#48 retro). It also refuses an output folder
that already holds files: #220 reused one, and a wait for its `junit.xml`
fired on an earlier session's results (#220 retro). A run holds a lock
for the machine, a second waits for it, and a SIGKILL frees it (#316
retro)."""

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "pytest-under-load.sh"


def run(command: str, out: str = "/nonexistent") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "DERBYNET_LOAD_RUN_DIR": out},
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


def start(tmp_path, name, lock, extra_env=None):
    """The script on a stub of its own in `tmp_path/name`, in a session of
    its own so the test can kill all it started, with its output in
    `tmp_path/name.out` (a file, as run_in_background gives it)."""
    folder = tmp_path / name
    folder.mkdir()
    (folder / f"test_{name}.py").write_text(HOLD)
    out = open(tmp_path / f"{name}.out", "w")
    env = {
        **os.environ,
        "DERBYNET_LOAD_RUN_DIR": str(tmp_path / f"{name}-run"),
        "DERBYNET_LOAD_LOCK": str(lock),
        **(extra_env or {}),
    }
    env.pop("DERBYNET_LOAD_LOCK_HELD", None)
    run = subprocess.Popen(
        [str(SCRIPT), "--loops", "1", "-p", "no:cacheprovider", str(folder)],
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


def children(pid):
    """The command names of the processes whose parent is `pid`."""
    names = []
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            name, rest = stat.read_text().rsplit(")", 1)
        except OSError:
            continue
        if int(rest.split()[1]) == pid:
            names.append(name.split("(", 1)[1])
    return names


def finish(tmp_path, *runs):
    for name in ("first", "second"):
        if (tmp_path / name).is_dir():
            (tmp_path / name / "go").write_text("")
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
        wait_until((tmp_path / "first" / "started").exists, "first run")
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
        wait_until((tmp_path / "second" / "started").exists, "second run")
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
        wait_until((tmp_path / "first" / "started").exists, "first run")
        stub = int((tmp_path / "first" / "started").read_text().split()[0])
        os.kill(first.pid, signal.SIGKILL)
        first.wait(timeout=10)
        second = start(tmp_path, "second", lock)
        wait_until((tmp_path / "second" / "started").exists, "second run", seconds=30)
        os.kill(stub, 0)  # the first run's pytest still runs
        assert "Waiting" not in (tmp_path / "second.out").read_text()
    finally:
        finish(tmp_path, first, *([second] if second else []))
