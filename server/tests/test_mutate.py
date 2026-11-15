"""`tools/mutate.py` runs a mutant only where it shows something (#319
retro): its text occurs once, and the tests it names run."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.mutate import counts

TOOL = Path(__file__).resolve().parents[1] / "tools" / "mutate.py"
MODULE = "def bound(a, b):\n    return a < b\n\n\ndef twice():\n    return 1 + 1\n"
TESTS = """\
import os
import time

import pytest
from mod import bound


def test_the_bound():
    assert bound(1, 2)
    assert not bound(2, 2)


def test_other():
    assert True


@pytest.mark.skip
def test_skipped():
    assert bound(1, 2)


def test_waits():
    open("started", "w").close()
    time.sleep(60)
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "mod.py").write_text(MODULE)
    (tmp_path / "test_mod.py").write_text(TESTS)
    return tmp_path


def mutate(project, old, new, *pytest_args, expect="fail", wait=False):
    command = [sys.executable, str(TOOL), "--file", "mod.py", "--old", old]
    command += ["--new", new, "--expect", expect, "--", "-p", "no:cacheprovider"]
    command += [*pytest_args]
    # The project has no ini file, so no `addopts` and no Django settings.
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    if wait:
        return subprocess.Popen(command, cwd=project, env=env, text=True)
    return subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)


def test_a_killed_mutant_exits_0_and_the_file_is_restored(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1].startswith("KILLED  mod.py")
    assert (project / "mod.py").read_text() == MODULE


def test_a_surviving_mutant_exits_1(project):
    run = mutate(project, "1 + 1", "2", "test_mod.py::test_the_bound")
    assert run.returncode == 1
    assert run.stdout.splitlines()[-1].startswith("SURVIVED")
    assert (project / "mod.py").read_text() == MODULE


def test_a_should_pass_mutant_that_passes_exits_0(project):
    run = mutate(project, "1 + 1", "2", "test_mod.py::test_the_bound", expect="pass")
    assert run.returncode == 0
    assert run.stdout.splitlines()[-1].startswith("PASSED")


def test_a_should_pass_mutant_that_fails_exits_1(project):
    run = mutate(
        project, "a < b", "a <= b", "test_mod.py::test_the_bound", expect="pass"
    )
    assert run.returncode == 1
    assert run.stdout.splitlines()[-1].startswith("BROKE")


@pytest.mark.parametrize(("old", "times"), [("a > b", 0), ("return", 2)])
def test_a_text_that_does_not_occur_once_is_refused(project, old, times):
    run = mutate(project, old, "x", "test_mod.py::test_the_bound")
    assert run.returncode == 2
    assert f"occurs {times} times" in run.stderr
    assert "passed" not in run.stdout, "no test runs"
    assert (project / "mod.py").read_text() == MODULE


def test_a_k_that_deselects_a_test_is_refused(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py", "-k", "the_bound")
    assert run.returncode == 2
    assert run.stdout.splitlines()[-1].startswith("REFUSED")
    assert "deselected" in run.stdout.splitlines()[-1]


def test_a_k_that_matches_nothing_is_refused(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py", "-k", "no_such_test")
    assert run.returncode == 2
    assert run.stdout.splitlines()[-1].startswith("REFUSED")


def test_a_run_whose_tests_all_skip_is_refused(project):
    run = mutate(project, "1 + 1", "2", "test_mod.py::test_skipped")
    assert run.returncode == 2
    assert "every one skipped" in run.stdout.splitlines()[-1]


def test_a_node_id_that_does_not_exist_is_an_error(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_no_such")
    assert run.returncode == 2
    assert run.stdout.splitlines()[-1].startswith("ERROR")
    assert (project / "mod.py").read_text() == MODULE


def test_a_sigterm_restores_the_file(project):
    tool = mutate(project, "1 + 1", "2", "test_mod.py::test_waits", wait=True)
    deadline = time.monotonic() + 30
    while not (project / "started").exists():
        assert time.monotonic() < deadline, "the test never started"
        assert tool.poll() is None
        time.sleep(0.05)
    assert "return 2" in (project / "mod.py").read_text(), "mutated while it runs"
    tool.send_signal(signal.SIGTERM)
    assert tool.wait(timeout=30) == 128 + signal.SIGTERM
    assert (project / "mod.py").read_text() == MODULE


def test_the_counts_come_from_the_last_summary_line():
    """The tool merges stderr into the output, so a line can follow pytest's
    summary."""
    output = "1 failed, 2 passed in 0.5s\n1 passed, 3 deselected in 0.1s\nwarning\n"
    assert counts(output) == {"passed": 1, "deselected": 3}
