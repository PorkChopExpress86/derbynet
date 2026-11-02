"""The HTTP golden fixtures match the testing/ scripts that made them."""

import subprocess
import sys

from tools import golden_inputs


def test_the_http_fixtures_were_captured_from_these_scripts():
    assert golden_inputs.stale() == [], (
        "recapture with server/tests/golden/capture.sh --http-only;"
        " see the docstring of server/tools/golden_inputs.py"
    )


def test_the_inputs_include_every_script_the_capture_runs():
    names = {str(p.relative_to(golden_inputs.ROOT)) for p in golden_inputs.inputs()}

    assert {
        "testing/common.sh",
        "testing/test-aggregate-nonracing.sh",
        "testing/reset-database.sh",
        "testing/data/outlaw-class.csv",
    } <= names
    assert "server/tests/golden/capture/capture-engine.php" not in names


def test_the_check_runs_on_the_hosts_python_without_the_server():
    """capture.sh runs the tool with `python3 -I`, outside the venv."""
    result = subprocess.run(
        [sys.executable, "-I", golden_inputs.__file__, "--check"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
