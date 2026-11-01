"""The `golden` marker picks the tests that replay the PHP golden fixtures
(#7), which the parity gate (#37) runs against Django."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from racing.engine.tests.golden import reads_golden_fixtures

SERVER_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module",
    [
        "legacy_api.tests.test_golden",
        "legacy_api.tests.test_timer_parity",
        "racing.services.tests.test_timer_transcripts",
        "racing.engine.tests.test_schedule_parity",
        "racing.engine.tests.test_standings_parity",
        "racing.services.tests.test_reschedule_replay",
        "racing.tests.test_award_current_parity",
    ],
)
def test_a_module_that_replays_a_fixture_is_golden(module):
    assert reads_golden_fixtures(importlib.import_module(module))


@pytest.mark.parametrize(
    "module", ["tests.test_passwords", "legacy_api.tests.test_awards_api"]
)
def test_a_module_that_reads_no_fixture_is_not(module):
    assert not reads_golden_fixtures(importlib.import_module(module))


def test_m_golden_collects_the_replays_and_nothing_else():
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "golden",
            "legacy_api/tests/test_golden.py",
            "tests/test_passwords.py",
        ],
        cwd=SERVER_DIR,
        capture_output=True,
        text=True,
    ).stdout

    assert "legacy_api/tests/test_golden.py::" in collected
    assert "tests/test_passwords.py::" not in collected
