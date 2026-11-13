"""The parity workflow's golden replay selects no browser test.

The Django leg of `.github/workflows/parity.yml` replays every test the root
conftest marks `golden`, and installs no Chromium. An explicit `-m` replaces
pyproject.toml's default `not browser`, so a browser file that imports a
golden-reading module joins the replay and errors at setup. History, not
asserted here: PR #292 made `ui/tests/browser/test_results.py` import
`transfer.tests.import_golden`, and the nightly parity run of 2026-11-12
(run 43254181268) failed on it with "Executable doesn't exist" (#206 retro).
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
WORKFLOW = ROOT / ".github" / "workflows" / "parity.yml"
# The whole command, so a path, `-k` or other flag added to it fails here
# instead of passing a check that collects with `-m` alone.
REPLAY = re.compile(r"^\s*run: uv run pytest -rs -m '([^']+)'\s*$", re.MULTILINE)


def replay_expression() -> str:
    found = REPLAY.findall(WORKFLOW.read_text())
    assert len(found) == 1, (
        "expected one golden replay step reading exactly"
        " `run: uv run pytest -rs -m '<expression>'` in parity.yml;"
        f" found {found!r}. Update REPLAY with the step."
    )
    return found[0]


def collected(expression: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", expression],
        cwd=SERVER,
        capture_output=True,
        text=True,
    )
    # 5: no tests collected.
    assert done.returncode in (0, 5), done.stdout[-2000:] + done.stderr[-2000:]
    return [line for line in done.stdout.splitlines() if "::" in line]


def test_the_golden_replay_selects_golden_tests():
    assert collected(replay_expression())


def test_the_golden_replay_selects_no_test_marked_browser():
    assert collected(f"({replay_expression()}) and browser") == []


def test_without_not_browser_the_replay_would_select_browser_tests():
    """The twin: the check above bites while a golden browser test exists."""
    expression = replay_expression().replace(" and not browser", "")
    assert expression != replay_expression()
    if not collected(f"({expression}) and browser"):
        pytest.skip("no browser test reads golden fixtures today")
