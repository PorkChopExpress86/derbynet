"""`tools/mutate.py` runs a mutant only where it shows something (#319
retro): its text occurs once, the tests it names run and none is
deselected, and no other run holds or has changed the file."""

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


@pytest.fixture
def broken():
    raise RuntimeError("an environment problem")


def test_broken(broken):
    assert True


def test_waits():
    with open("started", "w") as started:
        started.write(str(os.getpid()))
    time.sleep(60)
"""
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


@pytest.fixture
def project(tmp_path):
    """A repository whose `mod.py` is committed, as the tool requires."""
    (tmp_path / "mod.py").write_text(MODULE)
    (tmp_path / "test_mod.py").write_text(TESTS)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([*GIT, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*GIT, "commit", "-qm", "base"], cwd=tmp_path, check=True)
    yield tmp_path
    # A test that kills the tool leaves its pytest asleep in test_waits.
    if (started := tmp_path / "started").exists():
        try:
            os.kill(int(started.read_text()), signal.SIGKILL)
        except ProcessLookupError, ValueError:
            pass


def mutate(project, old, new, *pytest_args, expect="fail", wait=False, tool=()):
    command = [sys.executable, str(TOOL), "--file", "mod.py", "--old", old]
    command += ["--new", new, "--expect", expect, *tool]
    command += ["--", "-p", "no:cacheprovider", *pytest_args]
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


def started(project, tool):
    """Waits until test_waits runs under the tool's mutant."""
    deadline = time.monotonic() + 30
    while not (project / "started").exists() or not (project / "started").read_text():
        assert time.monotonic() < deadline, "the test never started"
        assert tool.poll() is None
        time.sleep(0.05)
    assert "return 2" in (project / "mod.py").read_text(), "mutated while it runs"


@pytest.mark.parametrize(
    "signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP], ids=lambda s: s.name
)
def test_a_stopped_run_restores_the_file(project, signum):
    tool = mutate(project, "1 + 1", "2", "test_mod.py::test_waits", wait=True)
    started(project, tool)
    tool.send_signal(signum)
    assert tool.wait(timeout=30) == 128 + signum
    assert (project / "mod.py").read_text() == MODULE


def test_after_a_sigkill_the_next_run_is_refused(project):
    """A SIGKILL leaves the mutant in the file, and no run may take it as
    the original."""
    tool = mutate(project, "1 + 1", "2", "test_mod.py::test_waits", wait=True)
    started(project, tool)
    tool.kill()
    tool.wait(timeout=30)
    assert "return 2" in (project / "mod.py").read_text()
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert run.returncode == 2
    assert "refused" in run.stderr
    (project / "started").unlink()
    run = mutate(project, "1 + 1", "2", "test_mod.py::test_the_bound")
    assert run.returncode == 2, "the leftover lock or mutant, whichever it meets"


def test_a_file_that_differs_from_the_index_is_refused(project):
    """A mutant a killed run left reads as the original, and a different
    mutant then reads as KILLED against it."""
    (project / "mod.py").write_text(MODULE.replace("a < b", "a > b"))
    run = mutate(project, "1 + 1", "2", "test_mod.py::test_the_bound")
    assert run.returncode == 2
    assert "differs from the index" in run.stderr
    assert "a > b" in (project / "mod.py").read_text(), "left as it was"


def test_a_staged_change_is_the_original(project):
    (project / "mod.py").write_text(MODULE.replace("1 + 1", "1 + 1 + 0"))
    subprocess.run([*GIT, "add", "mod.py"], cwd=project, check=True)
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 + 1 + 0" in (project / "mod.py").read_text()


def test_an_untracked_file_is_refused(project):
    (project / "mod.py").rename(project / "other.py")
    subprocess.run([*GIT, "rm", "-q", "--cached", "mod.py"], cwd=project, check=True)
    (project / "other.py").rename(project / "mod.py")
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert run.returncode == 2
    assert "not tracked" in run.stderr


def test_a_second_run_on_the_file_is_refused(project):
    first = mutate(project, "1 + 1", "2", "test_mod.py::test_waits", wait=True)
    started(project, first)
    second = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert second.returncode == 2
    assert "another run" in second.stderr
    first.send_signal(signal.SIGTERM)
    assert first.wait(timeout=30) == 128 + signal.SIGTERM
    assert (project / "mod.py").read_text() == MODULE
    again = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert again.returncode == 0, "the lock goes with its run"


@pytest.mark.parametrize("workers", [["-n", "2"], ["-n2"], ["--numprocesses=2"]])
def test_xdist_is_refused(project, workers):
    """xdist's summary leaves out the deselected count."""
    run = mutate(project, "a < b", "a <= b", *workers, "test_mod.py", "-k", "bound")
    assert run.returncode == 2
    assert "xdist" in run.stderr
    assert (project / "mod.py").read_text() == MODULE


def test_an_ini_that_starts_xdist_gives_no_result(project):
    (project / "pytest.ini").write_text("[pytest]\naddopts = -n 2\n")
    run = mutate(project, "a < b", "a <= b", "test_mod.py", "-k", "the_bound")
    assert run.returncode == 2
    assert run.stdout.splitlines()[-1].split()[0] in {"REFUSED", "ERROR"}


def test_a_run_with_an_error_and_no_failure_is_refused(project):
    run = mutate(
        project, "1 + 1", "2", "test_mod.py::test_other", "test_mod.py::test_broken"
    )
    assert run.returncode == 2
    assert run.stdout.splitlines()[-1].startswith("REFUSED")
    assert "error" in run.stdout.splitlines()[-1]


def test_a_failure_beside_an_error_is_killed(project):
    run = mutate(
        project,
        "a < b",
        "a <= b",
        "test_mod.py::test_the_bound",
        "test_mod.py::test_broken",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1].startswith("KILLED")


def test_a_run_past_its_bound_is_stopped(project):
    begun = time.monotonic()
    run = mutate(
        project, "1 + 1", "2", "test_mod.py::test_waits", tool=("--timeout", "5")
    )
    assert time.monotonic() - begun < 40, "test_waits sleeps a minute"
    assert run.returncode == 2
    assert run.stdout.splitlines()[-1].startswith("ERROR")
    assert "timed out" in run.stdout.splitlines()[-1]
    assert (project / "mod.py").read_text() == MODULE


def test_the_counts_come_from_the_last_summary_line():
    """The tool merges stderr into the output, so a line can follow pytest's
    summary."""
    output = "1 failed, 2 passed in 0.5s\n1 passed, 3 deselected in 0.1s\nwarning\n"
    assert counts(output) == {"passed": 1, "deselected": 3}
