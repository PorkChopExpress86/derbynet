"""pytest puts back the SIGINT and SIGQUIT a background job ignores.

A non-interactive bash starts a `&` job with SIGINT and SIGQUIT ignored, and
tools/pytest-under-load.sh, tools/hold-load-lock.sh and hand-off scripts
start pytest that way. Each command a test then starts inherits the ignore,
and bash cannot trap a signal ignored at its start: #311's Ctrl-C tests hung
inside pr-merge's run, and #59's hand-off failed both INT cases of
test_pytest_under_load.py while a terminal run passed them (#407 retro).
conftest.py resets both, so a test's commands get them as from a terminal.
"""

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
PROBE = "DERBYNET_SIGNAL_PROBE"


def ignored_in(status):
    mask = int(re.search(r"^SigIgn:\s*([0-9a-f]+)$", status, re.M).group(1), 16)
    return {s.name for s in signal.Signals if mask >> (s.value - 1) & 1}


@pytest.mark.skipif(not os.environ.get(PROBE), reason="run by the test below")
def test_probe_writes_what_a_command_it_starts_ignores():
    status = subprocess.run(
        ["cat", "/proc/self/status"], capture_output=True, text=True, check=True
    ).stdout
    Path(os.environ[PROBE]).write_text(status)


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["one-process", "xdist"])
def test_a_command_a_background_pytest_starts_gets_int_and_quit(tmp_path, workers):
    out = tmp_path / "status"
    pytest_argv = " ".join(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *workers,
            f"{Path(__file__).name}::test_probe_writes_what_a_command_it_starts_ignores",
        ]
    )
    # The job's own mask, then pytest as a job of the same shell.
    script = f"cat /proc/self/status >'{tmp_path}/job' & wait; {pytest_argv} & wait $!"
    env = {**os.environ, PROBE: str(out)}
    run = subprocess.run(
        ["bash", "-c", script],
        cwd=SERVER / "tests",
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    # The shell ignores both in its job, so the run starts with them ignored.
    assert {"SIGINT", "SIGQUIT"} <= ignored_in((tmp_path / "job").read_text())
    assert not {"SIGINT", "SIGQUIT"} & ignored_in(out.read_text())
