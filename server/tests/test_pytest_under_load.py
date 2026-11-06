"""`tools/pytest-under-load.sh` refuses a pipe and a `timeout` before it
starts anything: #47 and #48 each lost a 25-repeat run to
`timeout 3000 ... | grep` (#48 retro)."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "pytest-under-load.sh"


def run(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "DERBYNET_LOAD_RUN_DIR": "/nonexistent"},
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
