"""`tools/network_changed.py`'s decisions, in process: which failures of a
load run count as a changed network, the rerun's arguments, and the
script's status after the rerun (D122, #373). `test_pytest_under_load.py`
runs the script on them."""

import subprocess
from pathlib import Path

import pytest

from tools import network_changed as nc

# The two forms #348's load run of test_kiosk_results_by_racer.py failed with.
PAGE_LOAD = (
    "playwright._impl._errors.Error: Page.goto: net::ERR_NETWORK_CHANGED at"
    " http://localhost:52751/kiosk.php?address=kiosk-test52"
)
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


@pytest.mark.parametrize(
    "element",
    [
        failure(message=PAGE_LOAD),
        failure(text=f"    page.goto(url)\nE           {PAGE_LOAD}\n"),
        failure(message=TEARDOWN, tag="error"),
        failure(text=f"E         {REQUEST_FAILED}\n", tag="error"),
        failure(message=f"first line&#10;{PAGE_LOAD}"),
        failure(message=PAGE_LOAD.replace("http://", "https://")),
    ],
    ids=[
        "page-load-message",
        "page-load-raised-line",
        "teardown-message",
        "request-failed-raised-line",
        "page-load-second-message-line",
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


@pytest.mark.parametrize(
    "element",
    [
        # This file's own asserts, `assert code == 1, out`, print the bare text.
        failure(message="AssertionError: net::ERR_NETWORK_CHANGED in the log;"),
        failure(text="E   AssertionError: net::ERR_NETWORK_CHANGED at app.css\n"),
        # A source line the traceback quotes, not a raised one.
        failure(
            text=f"    allowed = [{REQUEST_FAILED.split(': ', 1)[1]}]\n"
            "E   AssertionError: other\n"
        ),
        failure(message=f"AssertionError: {PAGE_LOAD}"),
        failure(message=PAGE_LOAD.replace(" at http://", " at app.css")),
        failure(message=PAGE_LOAD.replace("Page.goto", "Page goto")),
        failure(message=PAGE_LOAD.replace("_errors.Error", "_errors.")),
        failure(message="request failed: http://x/a.png net::ERR_NETWORK_CHANGED"),
        failure(message="'request failed: http://x/a.png net::ERR_ABORTED'"),
        failure(message="'request failed: x/a.png net::ERR_NETWORK_CHANGED'"),
        failure(message="'request failed: http://x/a.png net::ERR_NETWORK_CHANGEDX'"),
        failure(),
    ],
    ids=[
        "bare-text-message",
        "bare-text-raised-line",
        "source-line",
        "page-load-inside-another-error",
        "no-url",
        "no-playwright-call",
        "no-error-class",
        "request-failed-unquoted",
        "request-failed-other-error",
        "request-failed-no-scheme",
        "request-failed-longer-error",
        "empty",
    ],
)
def test_another_failure_is_not_rerun(element):
    args, reason = nc.rerun_args(junit(element), SUMMARY, [])
    assert args is None
    assert reason == (
        "1 of 1 failures and errors are not a changed network"
        " (net::ERR_NETWORK_CHANGED as a page load or a failed request):"
        " no rerun, and the status stays pytest's."
    )


def test_one_other_failure_among_changed_networks_is_not_rerun():
    found = junit(
        failure(message=PAGE_LOAD),
        failure(message="TimeoutError: Page.wait_for_function: Timeout"),
        failure(message=TEARDOWN, tag="error"),
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


def test_the_command_line(tmp_path, capsys):
    (tmp_path / "junit.xml").write_text(junit(failure(message=PAGE_LOAD)))
    (tmp_path / "pytest.log").write_text(SUMMARY)
    files = [str(tmp_path / "junit.xml"), str(tmp_path / "pytest.log")]
    assert nc.main(["rerun-args", *files, "--", "-m", "browser", "x.py"]) == 0
    assert capsys.readouterr().out == (
        "-m\nbrowser\n-n\n0\nui/tests/browser/test_a.py\nui/tests/browser/test_b.py\n"
    )
    assert nc.main(["rerun-args", *files]) == 0
    assert capsys.readouterr().out.startswith("-n\n0\n")
    assert nc.main(["rerun-args", str(tmp_path / "none.xml"), files[1]]) == 1
    assert capsys.readouterr().out == "no junit.xml to read: no rerun.\n"
    assert nc.main(["outcome", "1", "3", "0"]) == 0
    assert "counts as passed" in capsys.readouterr().out
    assert nc.main(["outcome", "1", "3", "0", "--flake-ticket"]) == 1
    assert "--flake-ticket keeps" in capsys.readouterr().out
    assert nc.main(["outcome", "1", "", "0"]) == 1
    assert "no veth count" in capsys.readouterr().out
    assert nc.main(["outcome", "1", "3", "0", "--flake"]) == 2
    assert "unknown option --flake" in capsys.readouterr().err
    assert nc.main(["outcome", "1", "3"]) == 2
    assert "network_changed.py rerun-args JUNIT LOG" in capsys.readouterr().err
    assert nc.main(["outcome", "1", "3", "0", "--flake-ticket", "x"]) == 2
    assert nc.main(["rerun-args", files[0]]) == 2
    assert nc.main(["status", "1", "3", "0"]) == 2
    capsys.readouterr()
    # The run's arguments may follow with no `--`.
    assert nc.main(["rerun-args", *files, "-k", "phone"]) == 0
    assert capsys.readouterr().out.startswith("-k\nphone\n-n\n0\n")
    assert nc.main(["rerun-args", files[0], str(tmp_path / "none.log")]) == 1
    assert capsys.readouterr().out.startswith("No failed file is named")


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
    assert "counts as passed" in result.stdout
