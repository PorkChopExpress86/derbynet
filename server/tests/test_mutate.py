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

from tests import outside_process
from tools.mutate import counts, failed_in_helper, killed_by, main, stopped_on_error

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
# The project has no ini file, so no Django settings.
TOOL_UNSET = ("DJANGO_SETTINGS_MODULE",)


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


def mutate(
    project,
    old,
    new,
    *pytest_args,
    expect="fail",
    wait=False,
    tool=(),
    file="mod.py",
    unset=(),
    env=None,
    program=TOOL,
):
    """The tool's run of a mutant, with `env` added to its environment and
    the names in `unset` taken out."""
    command = [sys.executable, str(program), "--file", file, "--old", old]
    command += ["--new", new, "--expect", expect, *tool]
    command += ["--", "-p", "no:cacheprovider", *pytest_args]
    # The project has no ini file, so no `addopts` and no Django settings.
    env = {k: v for k, v in os.environ.items() if k not in (*TOOL_UNSET, *unset)} | (
        env or {}
    )
    if wait:
        return subprocess.Popen(command, cwd=project, env=env, text=True)
    return subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)


def test_a_killed_mutant_exits_0_and_the_file_is_restored(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1].startswith("KILLED  mod.py")
    assert run.stdout.count("test session starts") == 1, "pytest's output once"
    assert (project / "mod.py").read_text() == MODULE


def test_the_verdict_comes_before_pytest_s_output_too(project):
    """#463's round kept `grep ... | head -6` of the tool's output, which held
    pytest's `E` lines and cut the verdict after them (#463 retro)."""
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    lines = run.stdout.splitlines()
    assert lines[0] == lines[-1], run.stdout
    assert lines[0].startswith("KILLED  mod.py")


@pytest.mark.parametrize(
    "order, spared",
    [
        (
            ["test_other", "test_the_bound"],
            "1 passed with the mutant in: test_mod.py::test_other",
        ),
        (["test_the_bound", "test_other"], "1 not run after the first failure (-x)"),
    ],
)
def test_a_kill_names_the_tests_that_did_not_kill_it(project, order, spared):
    """#463's `true` mutant read KILLED on a run of `..F`, and the
    implementer and the reviewer nearly took each test it named for a kill
    (#463 retro)."""
    ids = [f"test_mod.py::{name}" for name in order]
    run = mutate(project, "a < b", "a <= b", *ids)
    assert run.returncode == 0, run.stdout + run.stderr
    last = run.stdout.splitlines()[-1]
    assert last == f"KILLED  mod.py: 'a < b' -> 'a <= b'  ({spared})"


def test_a_kill_under_q_names_no_unrun_count(project):
    """`-q` drops pytest's `collected N items` line, so no count of the tests
    `-x` left unrun can be read."""
    ids = ["test_mod.py::test_the_bound", "test_mod.py::test_other"]
    run = mutate(project, "a < b", "a <= b", "-q", *ids)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1] == "KILLED  mod.py: 'a < b' -> 'a <= b'"


def test_a_kill_by_its_only_test_names_no_other(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert run.stdout.splitlines()[-1] == "KILLED  mod.py: 'a < b' -> 'a <= b'"


HELPER_TESTS = """\
from mod import bound


def check(a, b):
    assert bound(a, b)


def test_through_a_helper():
    check(1, 2)


def test_in_its_body():
    assert bound(1, 2)
"""


@pytest.mark.parametrize(
    ("new", "test", "note"),
    [
        (
            "a > b",
            "test_through_a_helper",
            "  (failed in helper check at test_helper.py:5, not a test)",
        ),
        ("a > b", "test_in_its_body", ""),
        # Raised in the code under test, called from the test's body.
        ("[][1]", "test_in_its_body", ""),
        (
            "[][1]",
            "test_through_a_helper",
            "  (failed in helper check at test_helper.py:5, not a test)",
        ),
    ],
)
def test_a_mutant_killed_in_a_helper_names_it(project, new, test, note):
    """#417's M3, M10 and M11 read KILLED, killed by join_at's check that a
    run still waited, not by the order assert they aimed at (#417 retro)."""
    (project / "test_helper.py").write_text(HELPER_TESTS)
    run = mutate(project, "a < b", new, f"test_helper.py::{test}")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1] == f"KILLED  mod.py: 'a < b' -> {new!r}{note}"


HELPERS = """\
import pytest


def check(a, b):
    assert a < b


def test_outer():
    def inner():
        assert False

    inner()


class TestKind:
    def test_method(self):
        assert False


AT_MODULE_LEVEL = 1
"""


def failures(*places, after=""):
    """pytest's output for one failure whose traceback passes `places`."""
    lines = [
        "=" * 10 + " test session starts " + "=" * 10,
        "",
        "=" * 10 + " FAILURES " + "=" * 10,
    ]
    lines += ["_" * 5 + " test_x " + "_" * 5]
    for place in places:
        lines += ["    code", ">   raise", f"{place}"]
    return "\n".join(lines) + "\n" + after


@pytest.mark.parametrize(
    ("output", "found"),
    [
        (
            failures("test_h.py:12: ", "test_h.py:5: AssertionError"),
            "failed in helper check at test_h.py:5, not a test",
        ),
        # A library or the code under test below the helper.
        (
            failures("test_h.py:5: in check", "/usr/lib/python3/x.py:9: TimeoutError"),
            "failed in helper check at test_h.py:5, not a test",
        ),
        (
            failures("test_h.py:5: in check", "helpers.py:2: ValueError"),
            "failed in helper check at test_h.py:5, not a test",
        ),
        (
            failures("test_h.py:5: in check", "conftest.py:2: AssertionError"),
            "failed in helper fixture at conftest.py:2, not a test",
        ),
        (failures("test_h.py:12: AssertionError"), ""),
        (
            failures("test_h.py:12: in test_outer", "test_h.py:10: AssertionError"),
            "failed in helper inner at test_h.py:10, not a test",
        ),
        (failures("test_h.py:17: AssertionError"), ""),
        (failures("test_h.py:20: AssertionError"), ""),
        (failures("test_gone.py:5: AssertionError"), ""),
        (failures("test_broken.py:1: AssertionError"), ""),
        (failures("helpers.py:2: ValueError"), ""),
        # A place after the section, such as a warning's, is no frame.
        (
            failures(
                "test_h.py:12: AssertionError",
                after="=" * 5
                + " warnings summary "
                + "=" * 5
                + "\ntest_h.py:5: Warn\n",
            ),
            "",
        ),
        (failures("test_h.py:5: AssertionError").replace(" FAILURES ", " ERRORS "), ""),
        ("test_h.py:5: AssertionError\n", ""),
    ],
    # Ids by case, not by the output, which a mutant of failures() changes
    # (mutate.py's run then finds no such node id).
    ids=[
        "helper",
        "library-below",
        "code-below",
        "conftest",
        "test-body",
        "nested",
        "method",
        "module-level",
        "missing-file",
        "unparsable",
        "no-test-file",
        "after-section",
        "errors-section",
        "no-section",
    ],
)
def test_failed_in_helper_names_the_test_side_function_that_raised(
    tmp_path, monkeypatch, output, found
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_h.py").write_text(HELPERS)
    (tmp_path / "conftest.py").write_text("def fixture():\n    assert False\n")
    (tmp_path / "test_broken.py").write_text("def check(:\n")
    (tmp_path / "helpers.py").write_text("def run():\n    raise ValueError\n")
    assert failed_in_helper(output) == found


def test_an_absolute_place_is_no_frame(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_h.py").write_text(HELPERS)
    assert failed_in_helper(failures(f"{tmp_path}/test_h.py:5: AssertionError")) == ""


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


def test_after_a_sigkill_the_next_run_is_refused(project, tmp_path_factory):
    """A SIGKILL leaves the mutant in the file, and no run may take it as
    the original. The killed run's guard copy stays in its own TMPDIR, the
    only folder this test leaves it in."""
    folder = tmp_path_factory.mktemp("tmp")
    tmpdir = {"TMPDIR": str(folder)}
    tool = mutate(
        project, "1 + 1", "2", "test_mod.py::test_waits", wait=True, env=tmpdir
    )
    started(project, tool)
    tool.kill()
    tool.wait(timeout=30)
    assert "return 2" in (project / "mod.py").read_text()
    left = list(folder.iterdir())
    assert [p.name.startswith("mutate-guard-") for p in left] == [True]
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound", env=tmpdir)
    assert run.returncode == 2
    assert "refused" in run.stderr
    (project / "started").unlink()
    run = mutate(project, "1 + 1", "2", "test_mod.py::test_the_bound", env=tmpdir)
    assert run.returncode == 2, "the leftover lock or mutant, whichever it meets"
    assert list(folder.iterdir()) == left, "a refused run removes its own copy"


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


def test_a_killed_run_stops_at_its_first_failure(project):
    """#344's `--listed` rounds ran every test after the one that killed
    each mutant, and were stopped part-way (#344 retro)."""
    run = mutate(
        project,
        "a < b",
        "a <= b",
        "test_mod.py::test_the_bound",
        "test_mod.py::test_waits",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "stopping after 1 failures" in run.stdout, run.stdout
    assert not (project / "started").exists(), "test_waits ran"


def test_an_error_before_the_failure_still_kills(project):
    """`-x` stops at the error, which shows nothing about the mutant, so
    the run goes again without it and reaches the failure."""
    run = mutate(
        project,
        "a < b",
        "a <= b",
        "test_mod.py::test_broken",
        "test_mod.py::test_the_bound",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1].startswith("KILLED")


@pytest.mark.parametrize(
    ("found", "again"),
    [
        ({"error": 1}, True),
        ({"error": 1, "failed": 1}, False),
        ({"error": 1, "passed": 2}, True),
        ({"passed": 2}, False),
        ({"failed": 1}, False),
        ({}, False),
    ],
)
def test_only_a_run_stopped_on_an_error_with_no_failure_goes_again(found, again):
    """A KILLED or SURVIVED run, or a timed-out run with no summary line,
    takes no second run."""
    assert stopped_on_error(found) is again


def test_a_maxfail_given_wins_over_x(project):
    run = mutate(
        project,
        "a < b",
        "a <= b",
        "--maxfail=2",
        "test_mod.py::test_the_bound",
        "test_mod.py::test_other",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 failed, 1 passed" in run.stdout, run.stdout


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


# A test that loads the module only when it runs, as a test of a script it
# starts in a subprocess does: a module that no longer parses fails it, and
# no collection error says why.
LAZY_TESTS = """

def test_runs_lazily():
    import subprocess, sys
    load = "import mod; assert mod.bound(1, 2)"
    subprocess.run([sys.executable, "-c", load], check=True)
"""


def run_tool(project, name, old, new, *pytest_args):
    command = [sys.executable, str(TOOL), "--file", name, "--old", old, "--new", new]
    command += ["--", "-p", "no:cacheprovider", *pytest_args]
    env = {k: v for k, v in os.environ.items() if k not in TOOL_UNSET}
    return subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)


def test_a_python_mutant_that_does_not_parse_is_refused(project):
    """#345's m28 lost a newline to `$(...)` and glued two lines, and its
    test failed on a file that no longer parsed, which read as KILLED
    (#345 retro)."""
    (project / "test_mod.py").write_text(TESTS + LAZY_TESTS)
    subprocess.run([*GIT, "commit", "-qam", "lazy"], cwd=project, check=True)
    run = mutate(project, "a < b", "a < b)", "test_mod.py::test_runs_lazily")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "the mutant does not parse as Python (line 2" in run.stderr
    assert "build --old and --new from a file or a heredoc" in run.stderr
    assert "passed" not in run.stdout and "failed" not in run.stdout, "no test runs"
    assert (project / "mod.py").read_text() == MODULE


def test_a_python_script_mutant_that_does_not_parse_is_refused(project):
    """A script with no extension whose shebang names Python, such as
    `plan/bin/affected-tests`, is Python too."""
    (project / "tool").write_text("#!/usr/bin/env python3\nif 1 < 2:\n    pass\n")
    subprocess.run([*GIT, "add", "tool"], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "tool"], cwd=project, check=True)
    run = run_tool(project, "tool", "1 < 2", "1 <", "test_mod.py::test_other")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "does not parse as Python" in run.stderr


def test_a_mutant_of_a_file_that_is_no_python_or_never_parsed_runs(project):
    """The refusal is for a mutant that breaks Python in a Python file: a
    text file whose text parses as Python before the mutant and not after,
    and a `.py` file that did not parse before, still run."""
    for name, text in (("notes.txt", "a < b\n"), ("old.py", "print 'a < b'\n")):
        (project / name).write_text(text)
    subprocess.run([*GIT, "add", "notes.txt", "old.py"], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "files"], cwd=project, check=True)
    for name in ("notes.txt", "old.py"):
        run = run_tool(project, name, "a < b", "a <", "test_mod.py::test_other")
        assert run.returncode == 1, (name, run.stdout + run.stderr)
        assert run.stdout.splitlines()[-1].startswith("SURVIVED"), name


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
    env = {k: v for k, v in os.environ.items() if k not in TOOL_UNSET}
    if wait:
        return subprocess.Popen(
            command, cwd=project, env=env, text=True, stdout=subprocess.PIPE
        )
    return subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)


def rows(run):
    """The listed run's rows, without the pytest lines indented under them."""
    return [line for line in run.stdout.splitlines() if not line.startswith(" ")]


def commit(project, module, tests=""):
    (project / "mod.py").write_text(module)
    (project / "test_mod.py").write_text(TESTS + tests)
    subprocess.run([*GIT, "commit", "-qam", "change"], cwd=project, check=True)


def test_listed_runs_each_mutant_and_names_each_survivor(project):
    """`pick(1, 1)` and `pick(0, 1)` leave `b > 0` made true alive: no case
    has b at 0 or below."""
    commit(project, MODULE + PICK, PICK_TESTS)
    run = listed(project, "test_mod.py::test_pick")
    assert rows(run)[:6] == [
        "KILLED    mod.py:10  condition  a > 0 and b > 0  ->  True",
        "KILLED    mod.py:10  condition  a > 0 and b > 0  ->  False",
        "KILLED    mod.py:10  operand  a > 0  ->  True",
        "KILLED    mod.py:10  operand  a > 0  ->  False",
        "SURVIVED  mod.py:10  operand  b > 0  ->  True",
        "KILLED    mod.py:10  operand  b > 0  ->  False",
    ], run.stdout
    assert "6 listed: 5 KILLED, 1 SURVIVED, 0 REFUSED, 0 ERROR\n" in run.stdout
    assert (
        run.stdout.splitlines()[-1] == "  SURVIVED  mod.py:10  operand  b > 0  ->  True"
    )
    assert run.returncode == 1
    assert (project / "mod.py").read_text() == MODULE + PICK


def test_listed_names_the_test_and_error_under_each_killed_row(project):
    """#347's KILLED rows named no test: a kill by a bound or a flake read
    as one by the test's assert, and the row was rerun by hand (#347 retro).
    `pick(1, 1) == 1` fails each mutant that makes the condition false, and
    `pick(0, 1) == 0` each that makes it true."""
    commit(project, MODULE + PICK, PICK_TESTS)
    run = listed(project, "test_mod.py::test_pick")
    lines = run.stdout.splitlines()
    assert lines[:2] == [
        "KILLED    mod.py:10  condition  a > 0 and b > 0  ->  True",
        "    FAILED test_mod.py::test_pick - assert 1 == 0",
    ], run.stdout
    assert lines[2:4] == [
        "KILLED    mod.py:10  condition  a > 0 and b > 0  ->  False",
        "    FAILED test_mod.py::test_pick - assert 0 == 1",
    ], run.stdout
    survivor = lines.index("SURVIVED  mod.py:10  operand  b > 0  ->  True")
    assert not lines[survivor + 1].startswith(" "), run.stdout


def test_killed_by_reads_only_the_short_summary():
    """A captured log's `ERROR` line above the summary is no test."""
    output = (
        "ERROR    asyncio:base_events.py:1893 Exception in callback\n"
        "==================== short test summary info ====================\n"
        "FAILED t.py::test_a - assert 1 == 2\n"
        "ERROR t.py::test_b - RuntimeError: an environment problem\n"
        "=================== 1 failed, 1 error in 0.10s ===================\n"
    )
    assert killed_by(output) == [
        "FAILED t.py::test_a - assert 1 == 2",
        "ERROR t.py::test_b - RuntimeError: an environment problem",
    ]
    assert killed_by("1 failed in 0.1s\n") == []


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
    # The refusal names the next step (#367 retro).
    assert "Mutate each step the changed tests take by hand" in run.stderr
    # At the diff's own call site: #519's hand mutant of a shared helper
    # errored in other callers' fixtures (#519 retro).
    assert "at the diff's own call site rather than in a helper" in run.stderr
    assert run.stdout == (
        "SKIPPED   notes.md: 2 not run, in no source file\n"
        "SKIPPED   test_mod.py: 2 not run, in a test file"
        " (name it with --file to mutate its helpers)\n"
        # The round ends in its tally line, as the Implement step's hand-off
        # reads it: #513's and #519's refusals printed none (#519 retro).
        "0 listed: none for the diff, so the round is hand mutants\n"
    )


# A guard that lives in a test file, as #345's `root_execs` scanner does in
# tests/test_docker_checks.py, with the test that holds it. Its body reads
# the next line, so the loop made `while True` ends in an IndexError, not a
# hang that runs to --timeout.
GUARD_TESTS = """

def ends(lines, end):
    def test_from(end):
        while lines[end].endswith("\\\\"):
            end = lines.index(lines[end + 1], end + 1)
        return end

    return test_from(end)


def test_ends():
    if ends(["b"], 0) == 0:
        assert ends(["a \\\\", "b"], 0) == 1


class TestEnds:
    def test_in_a_class(self):
        if ends(["b"], 0) == 0:
            assert ends(["a \\\\", "b"], 0) == 1


@pytest.mark.parametrize(
    "x", [1 if len("b") == 1 else 2]
)
def test_decorated(x):
    assert x


async def test_async():
    if ends(["b"], 0) == 0:
        pass
"""


def test_listed_mutates_a_guard_in_a_test_file_named_by_file(project):
    """#345's scanner loop survived review's `break` mutant, and --listed
    would have skipped it, since every test file was skipped (#345 retro).
    Named by --file, a test file's helpers are mutated, a function nested
    in one too, whatever its name, and the conditions of its test functions
    and test methods still are not: an `async def` test's, and a decorator's
    (PR #364's review found both counted as helpers)."""
    commit(project, MODULE, GUARD_TESTS)
    run = listed(project, "test_mod.py::test_ends", tool=("--file", "test_mod.py"))
    assert rows(run)[1:3] == [
        'KILLED    test_mod.py:39  condition  lines[end].endswith("\\\\")  ->  True',
        'KILLED    test_mod.py:39  condition  lines[end].endswith("\\\\")  ->  False',
    ], run.stdout
    assert "SKIPPED   test_mod.py: 8 not run, in a test function" in rows(run)
    # The tally names them too: #494's report read the two counts as one.
    assert "; 8 skipped, not run" in run.stdout, run.stdout
    assert run.returncode == 0, run.stdout + run.stderr
    assert (project / "test_mod.py").read_text() == TESTS + GUARD_TESTS


def test_listed_skips_a_test_file_it_is_not_named(project):
    commit(project, MODULE + PICK, PICK_TESTS + GUARD_TESTS)
    run = listed(project, "test_mod.py::test_pick")
    assert (
        "SKIPPED   test_mod.py: 10 not run, in a test file"
        " (name it with --file to mutate its helpers)" in run.stdout
    ), run.stdout
    # No row names a mutant in it; a KILLED row's pytest line names its test.
    listed_rows = "\n".join(rows(run)).replace("SKIPPED   test_mod.py:", "")
    assert "test_mod.py:" not in listed_rows, run.stdout


def test_listed_takes_no_old_or_new(project):
    run = listed(project, "test_mod.py", tool=("--old", "a"))
    assert run.returncode == 2
    assert "--listed takes no --old" in run.stderr


@pytest.mark.parametrize(
    "signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP], ids=lambda s: s.name
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
    assert [line.split()[1] for line in rows(run)[:2]] == [
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


def test_listed_refuses_a_mutant_that_does_not_parse_and_names_its_line(project):
    """#417 retro's listing offered `key=lambda node: node.lineno` for a
    `max()`, which made `return key=...`: its tests failed at collection,
    and the row read ERROR with no line named (#449). The listing no
    longer offers it, so a stand-in listing does."""
    module = MODULE + "\n\ndef low(a, b):\n    return min(a, b)\n"
    commit(project, module)
    patch = (
        "import dataclasses\n"
        "listing = mutate._listing()\n"
        "real = listing.mutants\n"
        "listing.mutants = lambda diff, root: [\n"
        "    dataclasses.replace(m, new='key=lambda node: node.lineno')\n"
        "    for m in real(diff, root)[:1]\n"
        "]\n"
    )
    tool = wrapped(project, patch, "listed", "test_mod.py::test_the_bound")
    out, err = tool.communicate(timeout=60)
    assert tool.returncode == 2, out + err
    row = out.splitlines()[0]
    assert row.startswith(
        "REFUSED   mod.py:10  min  min(a, b)  ->  key=lambda node: node.lineno"
        "  (the mutant does not parse as Python (line 10: "
    ), out
    assert "heredoc" not in row, "a listed mutant's text comes from no shell"
    assert "1 listed: 0 KILLED, 0 SURVIVED, 1 REFUSED, 0 ERROR" in out
    assert len(out.splitlines()) == 3, f"no pytest output: {out}"
    assert (project / "mod.py").read_text() == module


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


# A stand-in run of the tool with one of its steps slowed or followed by an
# edit, for the #358 review's cases: a second signal while the file is put
# back or the lock is taken, and an edit by someone else during the run.
WRAPPER = """\
import os, pathlib, shutil, sys, tempfile, time
sys.path.insert(0, {tools!r})
import mutate
rmtree = shutil.rmtree
ORIGINAL = pathlib.Path("mod.py").read_bytes()
write_bytes, fdopen, result = pathlib.Path.write_bytes, os.fdopen, mutate._result
unlink, mkdtemp = pathlib.Path.unlink, tempfile.mkdtemp


def mark(name):
    open(name, "w").close()


def slow_restore(self, data):
    if self.name != "mod.py" or data != ORIGINAL:
        return write_bytes(self, data)
    with open(self, "wb") as f:
        f.write(data[: len(data) // 2])
        f.flush()
        mark("restoring")
        time.sleep(2)
        f.write(data[len(data) // 2 :])
    return len(data)


def slow_lock(fd, *args, **kwargs):
    os.fdopen = fdopen
    mark("locking")
    time.sleep(2)
    return fdopen(fd, *args, **kwargs)


def slow_unlock(self, *args):
    if self.name.startswith("mutate-"):
        mark("unlocking")
        time.sleep(2)
    return unlink(self, *args)


def held_mkdtemp(*args, **kwargs):
    path = mkdtemp(*args, **kwargs)
    pathlib.Path("making").write_text(path)
    deadline = time.monotonic() + 30
    while not os.path.exists("go"):
        assert time.monotonic() < deadline, "no go"
        time.sleep(0.02)
    return path


def failing_mkdtemp(*args, **kwargs):
    raise OSError("no room for the guard")


def slow_rmtree(path, *args, **kwargs):
    pathlib.Path("removing").write_text(str(path))
    time.sleep(2)
    return rmtree(path, *args, **kwargs)


def edit_after_first(*args):
    mutate._result = result
    with open("mod.py", "a") as f:
        f.write("# an edit\\n")
    return result(*args)


{patch}
sys.exit(mutate.main(sys.argv[1:]))
"""


def wrapped(project, patch, mode, *pytest_args, tool=(), env=None):
    """The tool, under `patch`, in `mode` (single: `1 + 1` -> `2`), with
    `env` added to its environment."""
    (project / "wrap.py").write_text(
        WRAPPER.format(tools=str(TOOL.parent), patch=patch)
    )
    first = ["--file", "mod.py", "--old", "1 + 1", "--new", "2"]
    command = [sys.executable, "wrap.py"] + (
        ["--listed", "--base", "HEAD~1"] if mode == "listed" else first
    )
    command += [*tool, "--", "-p", "no:cacheprovider", *pytest_args]
    env = {k: v for k, v in os.environ.items() if k not in TOOL_UNSET} | (env or {})
    return subprocess.Popen(
        command,
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_for(project, name, tool):
    deadline = time.monotonic() + 30
    while not (project / name).exists():
        assert time.monotonic() < deadline, f"no {name}"
        assert tool.poll() is None, tool.communicate()
        time.sleep(0.02)


def no_lock(project):
    return not list((project / ".git").glob("mutate-*.lock"))


@pytest.mark.parametrize("mode", ["single", "listed"])
def test_a_second_signal_while_the_file_is_put_back_waits_for_it(project, mode):
    """A SIGTERM comes each time a restore is half done: the restore
    finishes once, the lock goes, and the run ends on the signal. Before
    #358's fix the second left the file half written in a single run, and
    a third did in a listed run, which restored twice."""
    module = MODULE + "\n\ndef low(a, b):\n    return min(a, b)\n"
    commit(project, module)
    tool = wrapped(
        project,
        "pathlib.Path.write_bytes = slow_restore",
        mode,
        "test_mod.py::test_waits",
    )
    wait_for(project, "started", tool)
    tool.send_signal(signal.SIGTERM)
    restores, deadline = 0, time.monotonic() + 60
    while tool.poll() is None:
        assert time.monotonic() < deadline, "the run never ended"
        if (project / "restoring").exists():
            (project / "restoring").unlink()
            restores += 1
            tool.send_signal(signal.SIGTERM)
        time.sleep(0.02)
    _, err = tool.communicate(timeout=30)
    assert tool.returncode == 128 + signal.SIGTERM
    assert (project / "mod.py").read_bytes() == module.encode()
    assert no_lock(project)
    assert restores == 1
    assert "stopped:" not in err, "a signal is no edit by someone else"


@pytest.mark.parametrize("mode", ["single", "listed"])
@pytest.mark.parametrize(
    ("patch", "marker"),
    [
        ("os.fdopen = slow_lock", "locking"),
        ("pathlib.Path.unlink = slow_unlock", "unlocking"),
    ],
    ids=["taken", "removed"],
)
def test_a_signal_while_the_lock_is_taken_or_removed_leaves_no_lock(
    project, mode, patch, marker
):
    module = MODULE + "\n\ndef low(a, b):\n    return min(a, b)\n"
    commit(project, module)
    tool = wrapped(project, patch, mode, "test_mod.py::test_the_bound")
    wait_for(project, marker, tool)
    tool.send_signal(signal.SIGTERM)
    _, err = tool.communicate(timeout=30)
    assert tool.returncode == 128 + signal.SIGTERM
    assert no_lock(project)
    assert (project / "mod.py").read_text() == module
    assert "stopped:" not in err, "a signal is no edit by someone else"


EDIT_WAIT = 60
WAITS_FOR_THE_EDIT = f"""

def test_waits_for_the_edit():
    with open("started", "w") as started:
        started.write(str(os.getpid()))
    deadline = time.monotonic() + {EDIT_WAIT}
    while not os.path.exists("edited"):
        assert time.monotonic() < deadline, "no edit"
        time.sleep(0.02)
    open("ended", "w").close()
"""


@pytest.mark.parametrize("mode", ["single", "listed"])
def test_an_edit_during_the_run_is_left_as_it_is_and_named(project, mode):
    """Someone appends a line while the mutant's test runs: the tool puts
    nothing back over it, and names the file. The mutant's test ends on the
    edit, not on a `--timeout`: under load the bound passed before pytest
    reached the test, and the tool exited first (#367). The mutant's test
    waits `EDIT_WAIT` for the edit; the run's bound is `2 * EDIT_WAIT`, which
    leaves `EDIT_WAIT` for pytest's start and the tool's exit, so an edit it
    never sees ends on its own deadline, and `ended` is missing."""
    module = MODULE + "\n\ndef low(a, b):\n    return min(a, b)\n"
    commit(project, module, WAITS_FOR_THE_EDIT)
    tool = wrapped(project, "", mode, "test_mod.py::test_waits_for_the_edit")
    wait_for(project, "started", tool)
    mutant = (project / "mod.py").read_text()
    with open(project / "mod.py", "a") as f:
        f.write("# someone's edit\n")
    (project / "edited").touch()
    out, err = tool.communicate(timeout=2 * EDIT_WAIT)
    assert tool.returncode == 2, out + err
    assert (project / "ended").exists(), "the mutant's test never saw the edit"
    assert "mod.py changed while mutate.py held it" in err
    assert (project / "mod.py").read_text() == mutant + "# someone's edit\n"
    assert no_lock(project)
    if mode == "listed":
        assert "STOPPED  2 of 2 not run:" in out


def test_listed_stops_when_the_file_changes_between_mutants(project):
    """The edit lands after the first mutant's file is put back: the second
    mutant is never written over it."""
    module = MODULE + "\n\ndef low(a, b):\n    return min(a, b)\n"
    commit(project, module)
    tool = wrapped(
        project,
        "mutate._result = edit_after_first",
        "listed",
        "test_mod.py::test_the_bound",
    )
    out, err = tool.communicate(timeout=60)
    assert tool.returncode == 2, out + err
    assert "mod.py changed while mutate.py held it" in err
    assert "STOPPED  1 of 2 not run:" in out
    assert (project / "mod.py").read_text() == module + "# an edit\n"
    assert no_lock(project)


def test_after_a_sigkill_the_next_listed_run_is_refused(
    project, tmp_path_factory, monkeypatch
):
    folder = tmp_path_factory.mktemp("tmp")
    monkeypatch.setenv("TMPDIR", str(folder))
    commit(project, MODULE + PICK, PICK_TESTS)
    tool = listed(project, "test_mod.py::test_waits", wait=True)
    deadline = time.monotonic() + 30
    while not (project / "started").exists() or not (project / "started").read_text():
        assert time.monotonic() < deadline, "the test never started"
        time.sleep(0.05)
    tool.kill()
    tool.wait(timeout=30)
    assert "if True:" in (project / "mod.py").read_text(), "the mutant is left"
    left = list(folder.iterdir())
    assert [p.name.startswith("mutate-guard-") for p in left] == [True]
    run = listed(project, "test_mod.py::test_pick")
    assert run.returncode == 2
    assert "refused" in run.stderr
    assert "if True:" in (project / "mod.py").read_text(), "and left alone"
    assert list(folder.iterdir()) == left, "a refused run removes its own copy"


def test_listed_runs_a_crlf_files_mutants(project):
    """Each span of a CRLF file is its own, so its mutants run (#358 review:
    every one was refused)."""
    (project / "mod.py").write_bytes((MODULE + PICK).replace("\n", "\r\n").encode())
    (project / "test_mod.py").write_text(
        TESTS + PICK_TESTS + "    assert pick(1, 0) == 0\n"
    )
    subprocess.run([*GIT, "commit", "-qam", "crlf"], cwd=project, check=True)
    run = listed(project, "test_mod.py::test_pick")
    assert "6 listed: 6 KILLED" in run.stdout, run.stdout
    assert (project / "mod.py").read_bytes() == (MODULE + PICK).replace(
        "\n", "\r\n"
    ).encode()


def test_listed_reads_the_diff_from_the_merge_base(project):
    """#348's listing, run after master moved, held master's own lines: from
    BASE itself, a line master changed after the branch left it reads as
    added (#348 retro)."""
    master = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    gate = "\n\ndef gate(a):\n    return a > 0 and a < 9\n"
    commit(project, MODULE + gate)
    subprocess.run([*GIT, "checkout", "-qb", "branch"], cwd=project, check=True)
    commit(project, MODULE + gate + PICK, PICK_TESTS)
    subprocess.run([*GIT, "checkout", "-q", master], cwd=project, check=True)
    commit(project, MODULE + gate.replace("a < 9", "a < 10"))
    subprocess.run([*GIT, "checkout", "-q", "branch"], cwd=project, check=True)
    command = [sys.executable, str(TOOL), "--listed", "--base", master]
    command += ["--", "-p", "no:cacheprovider", "test_mod.py::test_pick"]
    env = {k: v for k, v in os.environ.items() if k not in TOOL_UNSET}
    run = subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)
    # PICK's six: the branch's `a < 9`, which master changed, is not.
    assert "6 listed: " in run.stdout, run.stdout + run.stderr
    assert "a < 9" not in run.stdout


def test_listed_runs_a_member_taken_away(project):
    """operand_mutants.py lists each member of an `in` tuple taken away
    (#356 retro), and the row runs with its replacement: the member a test
    reads is killed, the one none reads survives. A pair keeps its comma, so
    `("a",)` is still a tuple, not the string `"a"` (#370 review). The two
    mutants have one length, so written in one second the second ran the
    first one's .pyc and read as KILLED too, in about half the runs;
    mutate.py now removes the file's .pyc after each write, the restore's
    too, which the empty __pycache__ shows every run."""
    commit(
        project,
        MODULE + '\n\ndef kind(x):\n    return x in ("a", "b")\n',
        '\n\ndef test_kind():\n    from mod import kind\n    assert kind("a")\n',
    )
    run = listed(project, "test_mod.py::test_kind")
    rows = run.stdout.splitlines()
    assert 'KILLED    mod.py:10  member  "a", "b"  ->  "b",' in rows, (
        run.stdout + run.stderr
    )
    assert 'SURVIVED  mod.py:10  member  "a", "b"  ->  "a",' in rows, run.stdout
    assert (project / "mod.py").read_text().endswith('x in ("a", "b")\n')
    assert list(project.glob("__pycache__/mod.*")) == []


def test_listed_kills_each_member_a_test_reads(project):
    """#356's case after #370's review: with every member asserted, each row
    reads KILLED, `("values_list", "values")` less `"values"` included,
    which as the string `("values_list")` passed `"values" in` it."""
    commit(
        project,
        MODULE + '\n\ndef reads(attr):\n    return attr in ("values_list", "values")\n',
        "\n\ndef test_reads():\n    from mod import reads\n"
        '    assert reads("values_list")\n    assert reads("values")\n',
    )
    run = listed(project, "test_mod.py::test_reads")
    assert "2 listed: 2 KILLED, 0 SURVIVED" in run.stdout, run.stdout + run.stderr


def test_a_mutant_runs_past_a_stale_pyc_of_its_file(project):
    """A .pyc of the original that Python takes without checking the source
    stands for one another process wrote in the mutant's second, with the
    mutant's size: read, it runs the original, and the mutant survives
    (#370 review round, mutant T5). mutate.py removes it with the write."""
    import py_compile

    py_compile.compile(
        str(project / "mod.py"),
        cfile=str(project / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc"),
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1].startswith("KILLED  mod.py")


# kill_guard (#448): every pytest run mutate.py starts refuses a signal to a
# process outside its tree.
VICTIM = """\
from pathlib import Path


def victim():
    if Path("own").exists():
        return int(Path("own").read_text())
    return int(Path("outside.pid").read_text())
"""
SIGNAL_TESTS = """\
import os
import signal
import subprocess
from pathlib import Path

import pytest
from victim import victim


@pytest.fixture
def held():
    held = subprocess.Popen(["sleep", "120"])
    Path("own").write_text(str(held.pid))
    yield held
    held.kill()
    held.wait(timeout=10)


def test_stops_its_own_sleeper(held):
    os.kill(victim(), signal.SIGTERM)
    assert held.wait(timeout=10) == -signal.SIGTERM


def test_swallows_what_it_gets(held):
    try:
        os.kill(victim(), signal.SIGTERM)
    except BaseException:
        pass


@pytest.fixture
def stops_in_teardown(held):
    yield
    os.kill(victim(), signal.SIGTERM)


def test_stops_in_teardown(stops_in_teardown):
    pass
"""
# Where `victim()` names the sleeper outside the tree.
OUTSIDE_BRANCH = ('if Path("own").exists():', "if False:")


# The sleeper is started in `tmp_path`, which is the project.
outside = outside_process.outside


def with_victim(project):
    (project / "victim.py").write_text(VICTIM)
    (project / "test_guard.py").write_text(SIGNAL_TESTS)
    subprocess.run([*GIT, "add", "victim.py"], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "victim"], cwd=project, check=True)


@pytest.mark.parametrize(
    "test",
    [
        "test_stops_its_own_sleeper",
        "test_swallows_what_it_gets",
        "test_stops_in_teardown",
    ],
)
def test_a_mutant_that_signals_a_process_outside_the_tree_reads_killed_with_the_refusal(
    project, outside, test
):
    """The test's body, a refusal it swallowed, and a refusal in a fixture's
    teardown, which pytest counts as an error and not a failure."""
    with_victim(project)
    run = mutate(project, *OUTSIDE_BRANCH, f"test_guard.py::{test}", file="victim.py")
    assert run.returncode == 0, run.stdout + run.stderr
    last = run.stdout.splitlines()[-1]
    assert last.startswith("KILLED  victim.py: "), run.stdout
    assert "(kill_guard refused os.kill(" in last
    assert outside_process.alive(outside)


def test_listed_runs_each_mutant_under_the_guard(project, outside):
    with_victim(project)
    run = listed(project, "test_guard.py::test_stops_its_own_sleeper")
    listed_rows = rows(run)
    assert len(listed_rows) >= 3, run.stdout
    assert listed_rows[0].startswith(
        'SURVIVED  victim.py:5  condition  Path("own").exists()  ->  True'
    )
    assert listed_rows[1].startswith(
        'KILLED    victim.py:5  condition  Path("own").exists()  ->  False'
    )
    assert "(kill_guard refused os.kill(" in listed_rows[1]
    assert outside_process.alive(outside)
    assert run.returncode == 1, run.stdout + run.stderr


def test_a_run_under_the_guard_names_it_and_a_test_that_signals_its_own_child_passes(
    project,
):
    with_victim(project)
    run = mutate(
        project,
        'int(Path("own").read_text())',
        'int(Path("own").read_text()) + 0',
        "test_guard.py::test_stops_its_own_sleeper",
        "-v",
        expect="pass",
        file="victim.py",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "kill_guard: os.kill and os.killpg raises for a pid" in run.stdout
    assert run.stdout.splitlines()[-1].startswith("PASSED  victim.py")


def test_a_pythonpath_of_the_caller_is_kept_beside_the_guard(project, tmp_path_factory):
    folder = tmp_path_factory.mktemp("libs")
    (folder / "sentinel.py").write_text("VALUE = 1\n")
    (project / "test_path.py").write_text(
        "import sentinel\n\n\ndef test_it():\n    assert sentinel.VALUE == 1\n"
    )
    run = mutate(
        project,
        "1 + 1",
        "2",
        "test_path.py::test_it",
        expect="pass",
        env={"PYTHONPATH": str(folder)},
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_mutant_of_the_guard_itself_is_run_under_the_guard_as_it_was_before_it(
    project,
):
    """The run loads the copy of the plugin made before the first mutant, so
    a mutant of tools/kill_guard.py is judged by the tests and not by itself:
    the stand-in plugin's header names the text it had before the mutant."""
    (project / "plugin.py").write_text(
        'def pytest_report_header(config):\n    return "plugin says ORIGINAL"\n'
    )
    subprocess.run([*GIT, "add", "plugin.py"], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "plugin"], cwd=project, check=True)
    (project / "wrap.py").write_text(
        WRAPPER.format(
            tools=str(TOOL.parent), patch='mutate.GUARD = pathlib.Path("plugin.py")'
        )
    )
    run = mutate(
        project,
        "ORIGINAL",
        "MUTANT",
        "test_mod.py::test_other",
        expect="pass",
        file="plugin.py",
        program="wrap.py",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "plugin says ORIGINAL" in run.stdout
    assert "MUTANT" not in run.stdout.replace("-> 'MUTANT'", "")
    assert (project / "plugin.py").read_text().count("ORIGINAL") == 1


def test_a_run_with_no_pythonpath_loads_the_guard_too(project):
    run = mutate(
        project,
        "1 + 1",
        "2",
        "test_mod.py::test_other",
        expect="pass",
        unset=("PYTHONPATH",),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "kill_guard: os.kill and os.killpg raises for a pid" in run.stdout


def test_a_line_that_only_reads_like_a_refusal_does_not_kill_a_passing_run(project):
    """The tool reads the plugin's summary line from a failed run: a test
    that prints one and passes is no refusal."""
    (project / "test_says.py").write_text(
        "def test_says():\n"
        '    print("\\nkill_guard refused os.kill(1, SIGTERM) at x: y", flush=True)\n'
    )
    run = mutate(project, "1 + 1", "2", "-s", "test_says.py::test_says")
    assert run.returncode == 1, run.stdout + run.stderr
    assert "kill_guard refused os.kill(1" in run.stdout
    assert run.stdout.splitlines()[-1].startswith("SURVIVED  mod.py")


def test_a_should_pass_mutant_that_signals_a_process_outside_the_tree_breaks(
    project, outside
):
    with_victim(project)
    run = mutate(
        project,
        *OUTSIDE_BRANCH,
        "test_guard.py::test_stops_its_own_sleeper",
        expect="pass",
        file="victim.py",
    )
    assert run.returncode == 1, run.stdout + run.stderr
    last = run.stdout.splitlines()[-1]
    assert last.startswith("BROKE  victim.py: "), run.stdout
    assert "(kill_guard refused os.kill(" in last
    assert outside_process.alive(outside)


def test_the_guard_copy_is_removed_when_the_run_ends(project, tmp_path_factory):
    folder = tmp_path_factory.mktemp("tmp")
    run = mutate(
        project,
        "1 + 1",
        "2",
        "test_mod.py::test_other",
        expect="pass",
        env={"TMPDIR": str(folder)},
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_stop_while_the_guard_copy_is_made_removes_it(
    project, tmp_path_factory, signum
):
    """The stop comes after mkdtemp made the folder and before main() has its
    name: it is delivered once the name is kept, and the folder goes."""
    folder = tmp_path_factory.mktemp("tmp")
    tool = wrapped(
        project,
        "mutate.tempfile.mkdtemp = held_mkdtemp",
        "single",
        "test_mod.py::test_the_bound",
        env={"TMPDIR": str(folder)},
    )
    wait_for(project, "making", tool)
    assert Path((project / "making").read_text()).parent == folder
    tool.send_signal(signum)
    (project / "go").touch()
    _, err = tool.communicate(timeout=30)
    assert tool.returncode == 128 + signum, err
    assert list(folder.iterdir()) == []
    assert (project / "mod.py").read_text() == MODULE
    assert no_lock(project)


def test_a_guard_copy_that_cannot_be_made_ends_the_run_on_that_error(project):
    tool = wrapped(
        project,
        "mutate.tempfile.mkdtemp = failing_mkdtemp",
        "single",
        "test_mod.py::test_the_bound",
    )
    _, err = tool.communicate(timeout=30)
    assert tool.returncode == 1, err
    assert err.splitlines()[-1] == "OSError: no room for the guard"
    assert "During handling" not in err
    assert (project / "mod.py").read_text() == MODULE


def test_a_second_signal_while_the_guard_copy_is_removed_waits_for_it(
    project, tmp_path_factory
):
    folder = tmp_path_factory.mktemp("tmp")
    tool = wrapped(
        project,
        "mutate.shutil.rmtree = slow_rmtree",
        "single",
        "test_mod.py::test_waits",
        env={"TMPDIR": str(folder)},
    )
    wait_for(project, "started", tool)
    tool.send_signal(signal.SIGTERM)
    wait_for(project, "removing", tool)
    tool.send_signal(signal.SIGTERM)
    _, err = tool.communicate(timeout=30)
    assert tool.returncode == 128 + signal.SIGTERM, err
    assert Path((project / "removing").read_text()).parent == folder
    assert list(folder.iterdir()) == []
    assert (project / "mod.py").read_text() == MODULE
    assert no_lock(project)


def test_after_a_sigkill_the_guard_copy_is_left_and_the_next_run_goes_on(
    project, tmp_path_factory
):
    """As the mutant and the lock are left; the next run makes a copy of its
    own, and removes only that."""
    folder = tmp_path_factory.mktemp("tmp")
    tmpdir = {"TMPDIR": str(folder)}
    tool = mutate(
        project, "1 + 1", "2", "test_mod.py::test_waits", wait=True, env=tmpdir
    )
    started(project, tool)
    tool.kill()
    tool.wait(timeout=30)
    left = list(folder.iterdir())
    # Beside it a __pycache__, unless PYTHONDONTWRITEBYTECODE is set.
    copy = (left[0] / "kill_guard.py").read_bytes()
    assert copy == (TOOL.parent / "kill_guard.py").read_bytes()
    assert len(left) == 1
    subprocess.run([*GIT, "checkout", "mod.py"], cwd=project, check=True)
    for lock in (project / ".git").glob("mutate-*.lock"):
        lock.unlink()
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound", env=tmpdir)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1].startswith("KILLED  mod.py")
    assert list(folder.iterdir()) == left


STRAY_TEST = """\
import subprocess
from pathlib import Path

from mod import twice


def test_leaves_a_sleep():
    sleep = subprocess.Popen(["sleep", "1000"], start_new_session=True)
    Path("stray").write_text(str(sleep.pid))
    assert twice() == 2
"""


def test_a_process_the_run_left_running_is_named_and_killed(project):
    """#463's hang mutants left load scripts asleep, each in a session of
    its own, seen only with `pgrep -fa 'sleep 100000'` (#463 retro)."""
    (project / "test_stray.py").write_text(STRAY_TEST)
    subprocess.run([*GIT, "add", "."], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "stray"], cwd=project, check=True)
    run = mutate(project, "1 + 1", "1 + 2", "test_stray.py::test_leaves_a_sleep")
    pid = int((project / "stray").read_text())
    last = run.stdout.splitlines()[-1]
    assert last.startswith("KILLED  mod.py"), run.stdout + run.stderr
    assert last.endswith(f"(left 1 processes running, now killed: {pid} sleep)")
    assert (
        not Path(f"/proc/{pid}").exists()
        or "Z" in (Path(f"/proc/{pid}/stat").read_text().split()[2])
    )


def test_a_run_that_leaves_nothing_names_nothing(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert "left" not in run.stdout.splitlines()[-1]


SCAFFOLD_TEST = """\
from mod import twice


class Tab:
    pass


def test_reads_the_tab():
    tab = Tab()
    if twice() != 2:
        {raises}


def unbound():
    if False:
        late = 1
    return late
"""


@pytest.mark.parametrize(
    "raises, error",
    [
        ('tab.blocker.send("Network.enable")', "AttributeError"),
        ("blocker", "NameError"),
        ("unbound()", "UnboundLocalError"),
        ("tab()", "TypeError"),
    ],
)
def test_a_kill_on_a_scaffolding_error_says_it_was_no_assert(project, raises, error):
    """#409's m2 read KILLED on `tab.blocker`'s AttributeError in its own
    test, which the mutant reached, not on an assert (#409 retro)."""
    (project / "test_scaffold.py").write_text(SCAFFOLD_TEST.format(raises=raises))
    subprocess.run([*GIT, "add", "."], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "scaffold"], cwd=project, check=True)
    run = mutate(project, "1 + 1", "1 + 2", "test_scaffold.py::test_reads_the_tab")
    last = run.stdout.splitlines()[-1]
    assert last.startswith("KILLED  mod.py"), run.stdout
    assert last.endswith(f"failed on {error}, not an assert)"), run.stdout


@pytest.mark.parametrize("tb", ["--tb=no", "--tb=native"])
def test_a_kill_with_no_traceback_line_names_no_error(project, tb):
    """`--tb=no` prints no failures section, and `--tb=native` no
    `file.py:N: Error` line, so no error is named."""
    (project / "test_scaffold.py").write_text(
        SCAFFOLD_TEST.format(raises='tab.blocker.send("Network.enable")')
    )
    subprocess.run([*GIT, "add", "."], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "scaffold"], cwd=project, check=True)
    run = mutate(project, "1 + 1", "1 + 2", tb, "test_scaffold.py::test_reads_the_tab")
    assert run.stdout.splitlines()[-1] == "KILLED  mod.py: '1 + 1' -> '1 + 2'"


def test_a_kill_on_an_assert_names_no_error(project):
    run = mutate(project, "a < b", "a <= b", "test_mod.py::test_the_bound")
    assert "not an assert" not in run.stdout.splitlines()[-1]


SOMETIMES_TEST = """\
from pathlib import Path

from mod import twice


def test_sometimes():
    runs = Path("runs")
    n = int(runs.read_text()) if runs.exists() else 0
    runs.write_text(str(n + 1))
    assert twice() == 2 or n % 2
"""


@pytest.mark.parametrize("count", [["--count", "4"], ["--count=4"]])
def test_a_kill_under_count_gives_the_runs_it_failed_in(project, count):
    """#409's mutant that deleted stop_sending survived a run, then was
    killed in only some of its repeats: a kill by chance (#409 retro)."""
    (project / "test_sometimes.py").write_text(SOMETIMES_TEST)
    subprocess.run([*GIT, "add", "."], cwd=project, check=True)
    subprocess.run([*GIT, "commit", "-qm", "sometimes"], cwd=project, check=True)
    run = mutate(project, "1 + 1", "1 + 2", *count, "test_sometimes.py::test_sometimes")
    last = run.stdout.splitlines()[-1]
    assert last == "KILLED  mod.py: '1 + 1' -> '1 + 2'  (failed in 2 of 4 runs)", (
        run.stdout + run.stderr
    )
