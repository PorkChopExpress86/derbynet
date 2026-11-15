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

from tools.mutate import counts, main

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
    assert run.stdout.count("test session starts") == 1, "pytest's output once"
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


def test_a_new_text_that_equals_the_old_is_refused(project):
    """A mutant whose edit changes nothing passes as a survivor: #324's m7
    sed was never shown to apply, and its pass stood as an equivalent
    mutant (#324 retro)."""
    run = mutate(project, "a < b", "a < b", "test_mod.py::test_the_bound")
    assert run.returncode == 2
    assert "--new is --old" in run.stderr
    assert "passed" not in run.stdout, "no test runs"


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
    assert "test_mod.py" in run.stdout, "what pytest printed before the bound"
    assert (project / "mod.py").read_text() == MODULE


def test_the_counts_come_from_the_last_summary_line():
    """The tool merges stderr into the output, so a line can follow pytest's
    summary."""
    output = "1 failed, 2 passed in 0.5s\n1 passed, 3 deselected in 0.1s\nwarning\n"
    assert counts(output) == {"passed": 1, "deselected": 3}


# --listed (#336 retro): every mutant tools/operand_mutants.py lists.
PICK = """

def pick(a, b):
    if a > 0 and b > 0:
        return 1
    return 0
"""
PICK_TESTS = """

def test_pick():
    from mod import pick
    assert pick(1, 1) == 1
    assert pick(0, 1) == 0
"""


def listed(project, *pytest_args, tool=(), wait=False):
    command = [sys.executable, str(TOOL), "--listed", "--base", "HEAD~1", *tool]
    command += ["--", "-p", "no:cacheprovider", *pytest_args]
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    if wait:
        return subprocess.Popen(
            command, cwd=project, env=env, text=True, stdout=subprocess.PIPE
        )
    return subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)


def commit(project, module, tests=""):
    (project / "mod.py").write_text(module)
    (project / "test_mod.py").write_text(TESTS + tests)
    subprocess.run([*GIT, "commit", "-qam", "change"], cwd=project, check=True)


def test_listed_runs_each_mutant_and_names_each_survivor(project):
    """`pick(1, 1)` and `pick(0, 1)` leave `b > 0` made true alive: no case
    has b at 0 or below."""
    commit(project, MODULE + PICK, PICK_TESTS)
    run = listed(project, "test_mod.py::test_pick")
    assert run.stdout.splitlines()[:6] == [
        "KILLED    mod.py:10  condition  a > 0 and b > 0  ->  True",
        "KILLED    mod.py:10  condition  a > 0 and b > 0  ->  False",
        "KILLED    mod.py:10  operand  a > 0  ->  True",
        "KILLED    mod.py:10  operand  a > 0  ->  False",
        "SURVIVED  mod.py:10  operand  b > 0  ->  True",
        "KILLED    mod.py:10  operand  b > 0  ->  False",
    ], run.stdout
    assert "6 listed: 5 KILLED, 1 SURVIVED, 0 REFUSED, 0 ERROR" in run.stdout
    assert (
        run.stdout.splitlines()[-1] == "  SURVIVED  mod.py:10  operand  b > 0  ->  True"
    )
    assert run.returncode == 1
    assert (project / "mod.py").read_text() == MODULE + PICK


def test_listed_exits_0_when_every_mutant_is_killed(project):
    commit(project, MODULE + PICK, PICK_TESTS + "    assert pick(1, 0) == 0\n")
    run = listed(project, "test_mod.py::test_pick")
    assert "6 listed: 6 KILLED" in run.stdout
    assert run.returncode == 0, run.stdout + run.stderr


def test_listed_refuses_a_text_twice_on_its_line(project):
    """A line mutant's text is placed by its line, and a line that holds it
    twice is left to a run by hand."""
    commit(project, MODULE + "\n\ndef low(a, b):\n    return min(a, b) + min(a, b)\n")
    run = listed(project, "test_mod.py::test_the_bound")
    assert run.stdout.splitlines()[0].startswith("REFUSED   mod.py:10  min"), run.stdout
    assert "not once on its line" in run.stdout.splitlines()[0]
    assert run.returncode == 2


def test_listed_refuses_a_diff_with_no_mutant(project):
    """A Markdown file's `min(a, b)` is prose, not a mutant, and a test
    file's own condition is no code under test."""
    (project / "notes.md").write_text("Take min(a, b).\n")
    subprocess.run([*GIT, "add", "notes.md"], cwd=project, check=True)
    test_if = "\n\ndef test_if():\n    if bound(1, 2):\n        pass\n"
    commit(project, MODULE + "\n\ndef three():\n    return 3\n", test_if)
    run = listed(project, "test_mod.py::test_the_bound")
    assert run.returncode == 2
    assert "no mutant listed" in run.stderr
    assert run.stdout == (
        "SKIPPED   notes.md: 2 listed, in no source file\n"
        "SKIPPED   test_mod.py: 2 listed, in a test file\n"
    )


def test_listed_takes_no_old_or_new(project):
    run = listed(project, "test_mod.py", tool=("--old", "a"))
    assert run.returncode == 2
    assert "--listed takes no --old" in run.stderr


@pytest.mark.parametrize(
    "signum", [signal.SIGTERM, signal.SIGINT], ids=lambda s: s.name
)
def test_a_stopped_listed_run_restores_the_file_and_names_what_it_left(project, signum):
    commit(project, MODULE + PICK, PICK_TESTS)
    tool = listed(project, "test_mod.py::test_waits", wait=True)
    deadline = time.monotonic() + 30
    while not (project / "started").exists() or not (project / "started").read_text():
        assert time.monotonic() < deadline, "the test never started"
        assert tool.poll() is None
        time.sleep(0.05)
    assert "if True:" in (project / "mod.py").read_text(), "mutated while it runs"
    tool.send_signal(signum)
    out, _ = tool.communicate(timeout=30)
    assert tool.returncode == 128 + signum
    assert (project / "mod.py").read_text() == MODULE + PICK
    assert "STOPPED  6 of 6 not run:" in out
    assert out.count("  NOT RUN  mod.py:10") == 6
    run = listed(project, "test_mod.py::test_pick")
    assert run.returncode == 1, "no lock is left behind: " + run.stderr


@pytest.mark.parametrize(
    ("argv", "why"),
    [
        (["--listed", "--old", "a"], "--listed takes no --old"),
        (["--listed", "--new", "a"], "--listed takes no --old, --new"),
        (["--listed", "--expect", "pass"], "or --expect"),
        (["--old", "a", "--new", "b"], "give one --file"),
        (["--file", "m.py", "--new", "b"], "give one --file"),
        (["--file", "m.py", "--old", "a"], "give one --file"),
        (["--file", "m.py", "--file", "n.py", "--old", "a", "--new", "b"], "give one"),
    ],
)
def test_a_mode_given_the_other_modes_arguments_is_a_usage_error(argv, why, capsys):
    with pytest.raises(SystemExit) as stopped:
        main([*argv, "--", "test_mod.py"])
    assert stopped.value.code == 2
    assert why in capsys.readouterr().err


def test_listed_narrows_to_each_file_given_and_lists_scripts_and_stylesheets(project):
    """A stylesheet's call and a Python script with no extension are
    mutated; a file left out of --file is not."""
    (project / "tool").write_text("#!/usr/bin/env python3\nif a:\n    pass\n")
    (project / "a.css").write_text(".a { width: min(1px, 2px); }\n")
    subprocess.run([*GIT, "add", "tool", "a.css"], cwd=project, check=True)
    commit(project, MODULE + PICK, PICK_TESTS)
    run = listed(project, "test_mod.py::test_the_bound", tool=("--file", "tool"))
    assert [line.split()[1] for line in run.stdout.splitlines()[:2]] == [
        "tool:2",
        "tool:2",
    ], run.stdout
    assert "2 listed" in run.stdout
    run = listed(project, "test_mod.py::test_the_bound", tool=("--file", "a.css"))
    # The row names the stylesheet and its line 1; split, so no cite check
    # takes it for a cite of a real file.
    where = ("a.css", "1")
    assert any(
        line.split()[1] == ":".join(where)
        and line.endswith("  min  min(1px, 2px)  ->  1px")
        for line in run.stdout.splitlines()
    ), run.stdout
    assert "2 listed" in run.stdout


def test_listed_names_an_error_with_pytests_last_lines_and_exits_2(project):
    commit(project, MODULE + "\n\ndef low(a, b):\n    return min(a, b)\n")
    run = listed(project, "test_mod.py::test_no_such")
    assert run.stdout.splitlines()[0].startswith("ERROR     mod.py:10  min"), run.stdout
    assert "\n    ERROR: not found: " in run.stdout
    assert "2 listed: 0 KILLED, 0 SURVIVED, 0 REFUSED, 2 ERROR" in run.stdout
    assert run.returncode == 2


@pytest.mark.parametrize(
    ("tool", "why"),
    [(("--base", "no-such-rev"), "git diff"), ((), "not in a git work tree")],
)
def test_listed_refuses_a_base_git_cannot_read_or_no_git_tree(project, tool, why):
    if not tool:
        subprocess.run(["rm", "-rf", str(project / ".git")], check=True)
    command = [sys.executable, str(TOOL), "--listed", *tool, "--", "test_mod.py"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(project.parent)
    run = subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)
    assert run.returncode == 2
    assert why in run.stderr


def test_a_listed_mutant_past_its_bound_is_an_error(project):
    commit(project, MODULE + "\n\ndef low(a, b):\n    return min(a, b)\n")
    run = listed(project, "test_mod.py::test_waits", tool=("--timeout", "3"))
    assert run.stdout.splitlines()[0].startswith("ERROR     mod.py:10  min"), run.stdout
    assert "(timed out after 3 s)" in run.stdout.splitlines()[0]
    assert "0 KILLED, 0 SURVIVED, 0 REFUSED, 2 ERROR" in run.stdout
    assert run.returncode == 2
    assert (project / "mod.py").read_text().endswith("return min(a, b)\n")


def test_a_run_past_its_bound_that_printed_nothing_is_an_error(project):
    """With -q pytest prints nothing before test_waits ends."""
    run = mutate(
        project, "1 + 1", "2", "-q", "test_mod.py::test_waits", tool=("--timeout", "3")
    )
    assert run.returncode == 2, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1].startswith("ERROR")
    assert (project / "mod.py").read_text() == MODULE
