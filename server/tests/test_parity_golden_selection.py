"""The parity workflow's golden replay selects no browser test.

The Django leg of `.github/workflows/parity.yml` replays every test the root
conftest marks `golden`, and installs no Chromium. An explicit `-m` replaces
pyproject.toml's default `not browser`, so a browser file that imports a
golden-reading module joins the replay and errors at setup. PR #292 made
`ui/tests/browser/test_results.py` import `transfer.tests.import_golden`, and
every nightly parity run failed from 2026-11-12 (run 43254181268, "Executable
doesn't exist"), while no pull request ran the workflow (#206 retro).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
WORKFLOW = ROOT / ".github" / "workflows" / "parity.yml"
REPLAY = re.compile(r"run: uv run pytest -rs -m '([^']+)'")


def replay_expression() -> str:
    (expression,) = REPLAY.findall(WORKFLOW.read_text())
    return expression


def collected(expression: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", expression],
        cwd=SERVER,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if "::" in line]


def browser(ids: list[str]) -> list[str]:
    return [i for i in ids if i.startswith("ui/tests/browser/")]


def test_the_golden_replay_selects_golden_tests_and_no_browser_test():
    ids = collected(replay_expression())

    assert ids, "the replay selects nothing"
    assert browser(ids) == []


def test_without_not_browser_the_replay_would_select_browser_tests():
    """The twin: the check above bites while a golden browser test exists."""
    expression = replay_expression().replace(" and not browser", "")
    assert expression != replay_expression()

    assert browser(collected(expression))
