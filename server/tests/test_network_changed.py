"""`tools/network_changed.py`'s decisions, in process: which failures of a
load run count as a changed network, the rerun's arguments, and the
script's status after the rerun (D122, #373). `test_pytest_under_load.py`
runs the script on them."""

import subprocess
from pathlib import Path

import pytest

from tools import network_changed as nc

# The two forms #348's load runs failed with
# (~/.cache/derbynet-drafts/348/load-noto-racer/junit.xml and
# load3-dejavu-scale2/junit.xml): a raised Playwright error, whose junit
# message starts with its class,
PAGE_LOAD = (
    "playwright._impl._errors.Error: Page.goto: net::ERR_NETWORK_CHANGED at"
    " http://localhost:52751/kiosk.php?address=kiosk-test52"
)
# and the harness's teardown assert in ui/tests/browser/conftest.py, which
# pytest writes as an <error> "failed on teardown with ...".
REQUEST_FAILED = (
    "Left contains one more item: 'request failed:"
    " http://localhost:52751/kiosk.php?address=kiosk-test52"
    " net::ERR_NETWORK_CHANGED'"
)
TEARDOWN = (
    "failed on teardown with &quot;AssertionError: assert"
    " ['request fai...WORK_CHANGED']"
    " == []&#10;  &#10;  " + REQUEST_FAILED.replace("'", "&apos;") + "&quot;"
)
HARNESS_TRACEBACK = (
    "        yield page\n"
    "        try:\n"
    "            no_sideways_scroll(page)\n"
    "        finally:\n"
    "            context.close()\n"
    "&gt;       assert page_errors.final() == []\n"
    "E       AssertionError: assert ['request fai...WORK_CHANGED'] == []\n"
    "E         \n"
    f"E         {REQUEST_FAILED}\n"
    "E         Use -v to get more diff\n"
    "\n"
    "ui/tests/browser/conftest.py:614: AssertionError"
)
SUMMARY = (
    "=== short test summary info ===\n"
    "FAILED ui/tests/browser/test_a.py::test_one[phone-chromium-7-25]\n"
    "ERROR ui/tests/browser/test_b.py::test_two[desktop-chromium]\n"
    "FAILED ui/tests/browser/test_a.py::test_three\n"
    "=== 2 failed, 1 error in 9.1s ===\n"
)


def junit(*elements):
    cases = "".join(
        f'<testcase classname="c" name="t{n}">{element}</testcase>'
        for n, element in enumerate(elements)
    )
    return f"<testsuites><testsuite>{cases}</testsuite></testsuites>"


def failure(message="", text="", tag="failure"):
    return f'<{tag} message="{message}">{text}</{tag}>'


HARNESS = failure(message=TEARDOWN, text=HARNESS_TRACEBACK, tag="error")


@pytest.mark.parametrize(
    "element",
    [
        failure(message=PAGE_LOAD),
        failure(message=f"{PAGE_LOAD}&#10;Call log:&#10;  - navigating to"),
        failure(message=PAGE_LOAD, tag="error"),
        failure(message=f"failed on setup with &quot;{PAGE_LOAD}&quot;", tag="error"),
        HARNESS,
        failure(message=PAGE_LOAD.replace("http://", "https://")),
    ],
    ids=[
        "page-load",
        "page-load-call-log",
        "page-load-error",
        "page-load-at-setup",
        "harness",
        "https",
    ],
)
def test_a_changed_network_is_rerun(element):
    args, reason = nc.rerun_args(junit(element), SUMMARY, [])
    assert reason == ""
    assert args == [
        "-n",
        "0",
        "ui/tests/browser/test_a.py",
        "ui/tests/browser/test_b.py",
    ]


def harness(**change):
    """HARNESS with one of its parts changed."""
    parts = {"message": TEARDOWN, "text": HARNESS_TRACEBACK, "tag": "error"}
    return failure(**{**parts, **change})


@pytest.mark.parametrize(
    "element",
    [
        # This file's own asserts, `assert code == 1, out`, print the bare text.
        failure(message="AssertionError: net::ERR_NETWORK_CHANGED in the log;"),
        failure(text="E   AssertionError: net::ERR_NETWORK_CHANGED at app.css\n"),
        # A raised line or a later message line: an assert's diff can print
        # the page-load text there (`assert out == expected`).
        failure(text=f"E           {PAGE_LOAD}\n"),
        failure(message=f"AssertionError: first line&#10;{PAGE_LOAD}"),
        failure(message=f"AssertionError: {PAGE_LOAD}"),
        failure(message=PAGE_LOAD.replace(" at http://", " at app.css")),
        failure(message=PAGE_LOAD.replace("Page.goto", "Page goto")),
        failure(message=PAGE_LOAD.replace("_errors.Error", "_errors.")),
        failure(message=f"failed on call with &quot;{PAGE_LOAD}&quot;"),
        failure(message=f"x failed on setup with &quot;{PAGE_LOAD}&quot;"),
        # #373 review: a test body's `assert "..." in OUT`, where OUT holds the
        # harness's line, is a <failure>, not the harness's teardown <error>.
        failure(
            message="AssertionError: assert 'x' in &quot;...&quot;",
            text="E       AssertionError: assert 'x' in OUT\n"
            f"E         {REQUEST_FAILED}"
            "\n\ntests/test_x.py:9: AssertionError",
        ),
        harness(tag="failure"),
        harness(message=TEARDOWN.replace("teardown", "setup")),
        harness(text=HARNESS_TRACEBACK.replace("ui/tests/browser/", "tests/")),
        harness(text=HARNESS_TRACEBACK.replace(": AssertionError", ": Error")),
        harness(text=HARNESS_TRACEBACK + "\n\nui/tests/browser/test_x.py:5: Error"),
        # A source line the traceback quotes, not a raised one.
        harness(
            text=HARNESS_TRACEBACK.replace(
                f"E         {REQUEST_FAILED}", f"    allowed = [{REQUEST_FAILED}]"
            )
        ),
        harness(text=HARNESS_TRACEBACK.replace("'request", "request")),
        harness(text=HARNESS_TRACEBACK.replace("CHANGED'", "CHANGEDX'")),
        harness(text=HARNESS_TRACEBACK.replace("CHANGED'", "ABORTED'")),
        harness(text=HARNESS_TRACEBACK.replace("failed: http://", "failed: ")),
        failure(),
    ],
    ids=[
        "bare-text-message",
        "bare-text-raised-line",
        "page-load-raised-line",
        "page-load-second-message-line",
        "page-load-inside-another-error",
        "no-url",
        "no-playwright-call",
        "no-error-class",
        "page-load-other-wrapper",
        "page-load-wrapper-not-first",
        "request-failed-in-a-test-body-assert",
        "harness-as-failure",
        "harness-at-setup",
        "harness-elsewhere",
        "harness-other-error",
        "harness-not-last",
        "request-failed-source-line",
        "request-failed-unquoted",
        "request-failed-longer-error",
        "request-failed-other-error",
        "request-failed-no-scheme",
        "empty",
    ],
)
def test_another_failure_is_not_rerun(element):
    args, reason = nc.rerun_args(junit(element), SUMMARY, [])
    assert args is None
    assert reason == (
        "1 of 1 failures and errors are not a changed network"
        " (a Playwright net::ERR_NETWORK_CHANGED, or the browser harness's"
        " teardown naming one): no rerun, and the status stays pytest's."
    )


def test_one_other_failure_among_changed_networks_is_not_rerun():
    found = junit(
        failure(message=PAGE_LOAD),
        failure(message="TimeoutError: Page.wait_for_function: Timeout"),
        HARNESS,
    )
    args, reason = nc.rerun_args(found, SUMMARY, [])
    assert args is None
    assert reason.startswith("1 of 3 failures and errors are not")


def test_no_junit_no_parse_no_failure_and_no_summary_are_not_rerun():
    assert nc.rerun_args(None, SUMMARY, []) == (
        None,
        "no junit.xml to read: no rerun.",
    )
    args, reason = nc.rerun_args("<testsuites>", SUMMARY, [])
    assert args is None
    assert reason.startswith("junit.xml does not parse (")
    no_file = "No failed file is named in the summary: rerun the run's own command."
    assert nc.rerun_args(junit("<skipped/>"), SUMMARY, []) == (None, no_file)
    page_load = junit(failure(message=PAGE_LOAD))
    assert nc.rerun_args(page_load, "FAILED test_a.py::t\n", []) == (None, no_file)
    assert nc.rerun_args(page_load, "short test summary info\n= 1 failed =\n", []) == (
        None,
        no_file,
    )


def test_the_rerun_keeps_the_run_s_marks_keywords_and_plugins_only():
    args, _ = nc.rerun_args(
        junit(failure(message=PAGE_LOAD)),
        SUMMARY,
        [
            "-m",
            "browser",
            "-n",
            "4",
            "--count",
            "25",
            "-k",
            "phone",
            "-pno:randomly",
            "-mnot slow",
            "--timeout",
            "60",
            "ui/tests/browser/test_a.py",
            "-p",
        ],
    )
    assert args == [
        "-m",
        "browser",
        "-k",
        "phone",
        "-pno:randomly",
        "-mnot slow",
        "-n",
        "0",
        "ui/tests/browser/test_a.py",
        "ui/tests/browser/test_b.py",
    ]


@pytest.mark.parametrize(
    "status, veth, rerun, flake, expected",
    [
        (1, 3, 0, False, 0),
        (1, 1, 0, False, 0),
        (1, 3, 0, True, 1),
        (1, 0, 0, False, 1),
        (1, None, 0, False, 1),
        (1, 3, 1, False, 1),
        (1, 3, 5, False, 1),
        (3, 3, 1, False, 3),
        (3, None, 0, True, 3),
    ],
)
def test_only_a_clean_rerun_with_veth_lines_passes_a_run(
    status, veth, rerun, flake, expected
):
    assert nc.outcome(status, veth, rerun, flake)[0] == expected


def test_the_outcome_names_the_first_run_the_veth_count_and_the_rerun():
    assert nc.outcome(1, 3, 0, False)[1] == (
        "The first run exited 1, with 3 veth lines in its window; the rerun"
        " passed: the run counts as passed for D101's repeats only (D122),"
        " never for a flake ticket; its failures stay in pytest.log."
    )
    assert nc.outcome(1, 3, 0, True)[1] == (
        "The first run exited 1, with 3 veth lines in its window; the rerun"
        " passed, and --flake-ticket keeps pytest's status 1: a race keeps its"
        " 25 runs (D122)."
    )
    assert nc.outcome(1, None, 0, False)[1] == (
        "The first run exited 1, with no veth count in its window; the rerun"
        " passed, but with no veth lines counted the status stays pytest's 1"
        " (D122)."
    )
    assert nc.outcome(1, 0, 5, False)[1] == (
        "The first run exited 1, with 0 veth lines in its window; the rerun"
        " failed too (exit 5)."
    )


@pytest.mark.parametrize(
    "run_args",
    [["-p", "no:xdist"], ["-pno:xdist"]],
    ids=["two-words", "one-word"],
)
def test_a_run_without_xdist_reruns_without_n(run_args):
    """`-n 0` beside `-p no:xdist` exits 4, "unrecognized arguments: -n"
    (#373 review); server/README.md names both ways to run without xdist."""
    args, _ = nc.rerun_args(junit(failure(message=PAGE_LOAD)), SUMMARY, run_args)
    assert args == [
        *run_args,
        "ui/tests/browser/test_a.py",
        "ui/tests/browser/test_b.py",
    ]


def test_the_command_line(tmp_path, capsys):
    """rerun-args writes the arguments to a file of their own, and prints
    only a reason; outcome prints its line, then `exit N` (#373 review)."""
    (tmp_path / "junit.xml").write_text(junit(failure(message=PAGE_LOAD)))
    (tmp_path / "pytest.log").write_text(SUMMARY)
    files = [str(tmp_path / "junit.xml"), str(tmp_path / "pytest.log")]
    args_file = tmp_path / "rerun-args"
    command = ["rerun-args", *files, str(args_file)]
    assert nc.main([*command, "--", "-m", "browser", "x.py"]) == 0
    assert capsys.readouterr().out == ""
    assert args_file.read_text() == (
        "-m\nbrowser\n-n\n0\nui/tests/browser/test_a.py\nui/tests/browser/test_b.py\n"
    )
    # The run's arguments may follow with no `--`.
    assert nc.main([*command, "-k", "phone"]) == 0
    assert args_file.read_text().startswith("-k\nphone\n-n\n0\n")
    args_file.unlink()
    none = str(tmp_path / "none")
    assert nc.main(["rerun-args", none, files[1], str(args_file)]) == 1
    assert capsys.readouterr().out == "no junit.xml to read: no rerun.\n"
    assert nc.main(["rerun-args", files[0], none, str(args_file)]) == 1
    assert capsys.readouterr().out.startswith("No failed file is named")
    assert not args_file.exists()

    assert nc.main(["outcome", "1", "3", "0"]) == 0
    out = capsys.readouterr().out
    assert "counts as passed" in out and out.endswith("\nexit 0\n")
    assert nc.main(["outcome", "1", "3", "0", "--flake-ticket"]) == 0
    out = capsys.readouterr().out
    assert "--flake-ticket keeps" in out and out.endswith("\nexit 1\n")
    assert nc.main(["outcome", "2", "", "0"]) == 0
    out = capsys.readouterr().out
    assert "no veth count" in out and out.endswith("\nexit 2\n")

    for wrong in (
        ["outcome", "1", "3", "0", "--flake"],
        ["outcome", "1", "3"],
        ["outcome", "1", "3", "0", "--flake-ticket", "x"],
        ["rerun-args", *files],
        ["status", "1", "3", "0"],
    ):
        assert nc.main(wrong) == 2, wrong
        assert capsys.readouterr().out == ""
    nc.main(["outcome", "1", "3", "0", "--flake"])
    assert "unknown option --flake" in capsys.readouterr().err
    nc.main(["outcome", "1", "3"])
    assert "network_changed.py rerun-args JUNIT LOG" in capsys.readouterr().err


def test_the_script_runs_it(tmp_path):
    """pytest-under-load.sh runs the file as a script, with python3 -I."""
    script = Path(nc.__file__)
    result = subprocess.run(
        ["python3", "-I", str(script), "outcome", "1", "3", "0"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("\nexit 0\n")
