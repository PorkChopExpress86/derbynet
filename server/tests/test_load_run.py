"""tools/load-run.sh refuses minutes that are no whole number before it
starts anything.

Bash drops a command whose arithmetic fails and goes on with no failed
status: `load-run.sh 1m` dropped the load test, whose `--duration` is
`$((MINUTES * 60))`, and exited 0 under `Report:`, and so did `08`, which
bash reads as octal (#417 retro).  The run's
`uv` and `curl` are stand-ins here, so no server starts.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LOAD_RUN = Path(__file__).resolve().parents[1] / "tools" / "load-run.sh"

UV = """#!/bin/bash
echo "$*" >> "$CALLS"
case "$*" in
  *"tools.load_test"*) exit 0 ;;
  *"gunicorn --version"*) echo "gunicorn (version 23.0.0)" ;;
  *"derbynet.wsgi"*)
    for a; do [ "${prev:-}" = --pid ] && echo $$ > "$a"; prev=$a; done
    exec sleep 30 ;;
  *"journal_mode"*) echo wal ;;
esac
"""


def run(tmp_path, *args):
    server = tmp_path / "server"
    (server / "tools").mkdir(parents=True)
    shutil.copy(LOAD_RUN, server / "tools" / "load-run.sh")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "uv").write_text(UV)
    (stubs / "curl").write_text("#!/bin/sh\nexit 0\n")
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    calls = tmp_path / "calls"
    calls.touch()
    done = subprocess.run(
        ["bash", str(server / "tools" / "load-run.sh"), *args, str(tmp_path / "out")],
        env={
            **os.environ,
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "CALLS": str(calls),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done, calls.read_text()


@pytest.mark.parametrize("minutes", ["1m", "-1", "1.5", "08", "00"])
def test_minutes_that_are_no_whole_number_are_refused_before_anything_runs(
    tmp_path, minutes
):
    done, calls = run(tmp_path, minutes)
    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stderr == (
        "usage: load-run.sh [minutes] [report dir];"
        f" minutes is a whole number, not '{minutes}'\n"
    )
    assert calls == ""


def test_whole_minutes_run_the_load_test_for_that_many_seconds(tmp_path):
    done, calls = run(tmp_path, "2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--duration 120 " in calls
