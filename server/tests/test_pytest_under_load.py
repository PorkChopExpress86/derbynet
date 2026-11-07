"""`tools/pytest-under-load.sh` refuses a pipe and a `timeout` before it
starts anything: #47 and #48 each lost a 25-repeat run to
`timeout 3000 ... | grep` (#48 retro). It also refuses an output folder
that already holds files: #220 reused one, and a wait for its `junit.xml`
fired on an earlier session's results (#220 retro)."""

import os
import re
import shutil
import subprocess
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
        env={**os.environ, "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run")},
    )
    summary = (tmp_path / "out").read_text()
    assert result.returncode == 1, summary
    assert re.search(r"^ERROR \S*test_breaks.py::test_errors_at_setup", summary, re.M)
    assert re.search(r"^FAILED \S*test_breaks.py::test_fails", summary, re.M)
