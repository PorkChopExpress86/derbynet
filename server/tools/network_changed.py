"""The decisions `tools/pytest-under-load.sh` makes after a load run fails
with Chromium's net::ERR_NETWORK_CHANGED, which marks the machine's network
changing as another project's container joins or leaves a Docker bridge
(D122, #373). The script runs pytest; this module reads its text.

- `rerun_args(junit, log, args)`: when every failure and error in the run's
  junit.xml carries the error as a page load or a failed request does, the
  pytest arguments that rerun each failed file alone, with no xdist: the
  run's own `-m`, `-k` and `-p` options, then `-n 0` and the files the
  summary names. Otherwise `None` and the reason: a count line, then one
  line for each failure that is not one, by its test.
- `outcome(status, veth, rerun_status, flake_ticket)`: the script's exit
  status and the line that says why. A clean rerun with veth lines in the
  run's window passes the run for D101's repeats only; a flake ticket's
  run, a veth count of 0 or one not read, and a failed rerun keep pytest's
  status.

Usage, from the script:

    network_changed.py rerun-args JUNIT LOG ARGS_FILE [-- RUN_ARG ...]
        writes one rerun argument per line to ARGS_FILE and exits 0, or
        prints the reason there is no rerun and exits 1
    network_changed.py outcome STATUS VETH RERUN_STATUS [--flake-ticket]
        prints the outcome line, then `exit N` with the script's status,
        and exits 0; VETH is a count, or empty when the kernel log was
        not read

Any other exit, such as 2 for a wrong command line, is the tool's own
error, and the script keeps pytest's status.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from typing import Literal

MARKER = "net::ERR_NETWORK_CHANGED"

# The two ways a browser test fails when the network changes, as #348's
# load runs of test_kiosk_results_by_racer.py and test_harness.py failed
# (#348 retro). Each is read from where pytest writes it alone, since the
# bare text is not enough: tests/test_pytest_under_load.py's asserts print
# the script's own lines naming it, an assert's diff can print any line,
# and a flake there must keep its failure (#373 review).
#
# A raised Playwright error, whose junit message starts with its class, or
# with pytest's `failed on setup with "` before it for a fixture's:
#   playwright._impl._errors.Error: Page.goto: net::ERR_NETWORK_CHANGED at
#   http://localhost:52751/kiosk.php?address=kiosk-test52
PAGE_LOAD = re.compile(
    r"playwright\._impl\._errors\.\w+: \w+\.\w+: " + MARKER + r" at https?://"
)
# The harness's own teardown assert, the `page` fixture's
# `assert errors == []` in ui/tests/browser/conftest.py, which pytest writes
# as an <error> with the message "failed on teardown with ...", a traceback
# whose last line names that file and the assert's line (`HARNESS_END`),
# and every item it lists as a changed network: a failed request
# ("request failed: {request.url} {request.failure}"), and the console
# error Chromium logs for each failed resource, which names no URL. Since
# #373 the assert prints every item, one per line, in raised lines, each
# request beside its console line, as a load run of #366 wrote them:
#   E       AssertionError: 4 page errors, one per line:
#   E         'request failed: http://localhost:45757/static/ui/kiosk/derbynet-300.png
#              net::ERR_NETWORK_CHANGED'
#   E         'console: Failed to load resource: net::ERR_NETWORK_CHANGED'
#   E         'request failed: http://localhost:45757/static/ui/kiosk-now-racing.js
#              net::ERR_NETWORK_CHANGED'
#   E         'console: Failed to load resource: net::ERR_NETWORK_CHANGED'
# A console line counts only beside as many failed requests: one with none
# names no request that failed.
# Before it, pytest's diff alone named one item in full only when it was
# the only one; "Left contains 8 more items, first extra item: ..." names
# the first of several, and starts no rerun.
#   E         Left contains one more item: 'request failed:
#   http://localhost:52751/kiosk.php?address=kiosk-test52 net::ERR_NETWORK_CHANGED'
TEARDOWN = "failed on teardown with "
# How pytest wraps the message of an error in a fixture, such as a page
# load in a login fixture, before the error's own class.
SETUP_OR_TEARDOWN = re.compile(r'^failed on (?:setup|teardown) with "')
HARNESS_END = re.compile(r"ui/tests/browser/conftest\.py:\d+: AssertionError")
REQUEST_FAILED = re.compile(r"'request failed: https?://\S+ " + MARKER + "'")
EVERY_ITEM = re.compile(r"AssertionError: (\d+) page errors, one per line:")
CONSOLE_FAILED = re.compile("'console: Failed to load resource: " + MARKER + "'")
ONE_ITEM = re.compile("Left contains one more item: " + REQUEST_FAILED.pattern)

# A failed test or error in the -rfE summary, by the file pytest names.
SUMMARY_FILE = re.compile(r"^(?:FAILED|ERROR) ([^: ]*\.py)", re.M)

# The run's options that choose its tests or plugins, carried into the
# rerun: `-m browser` selects the browser tests that `addopts` deselects.
CARRIED = ("-m", "-k", "-p")


def harness_raised(element: ET.Element) -> list[str] | None:
    """The raised lines of the harness's teardown <error>, or None for any
    other element."""
    if element.tag != "error" or not element.get("message", "").startswith(TEARDOWN):
        return None
    lines = [line for line in (element.text or "").splitlines() if line.strip()]
    if not lines or not HARNESS_END.fullmatch(lines[-1]):
        return None
    return [line[2:].strip() for line in lines if line.startswith("E ")]


def listed_items(raised: list[str]) -> list[str] | None:
    """The items the harness's assert lists one per line, or None when it
    lists none or fewer than it counts."""
    for n, line in enumerate(raised):
        if header := EVERY_ITEM.fullmatch(line):
            items = raised[n + 1 : n + 1 + int(header[1])]
            return items if len(items) == int(header[1]) > 0 else None
    return None


def item_kind(item: str) -> Literal["request", "console", "other"]:
    """How the harness's teardown item reads: a failed request, Chromium's
    console line for a failed resource, or anything else."""
    if REQUEST_FAILED.fullmatch(item):
        return "request"
    if CONSOLE_FAILED.fullmatch(item):
        return "console"
    return "other"


def items_are_changed_network(items: list[str]) -> bool:
    """Whether every item is a failed request or Chromium's console line
    for one, with no more console lines than requests, so at least one
    request: `listed_items` gives no empty list."""
    kinds = [item_kind(item) for item in items]
    return "other" not in kinds and kinds.count("request") >= kinds.count("console")


def changed_network(element: ET.Element) -> bool:
    """Whether a junit <failure> or <error> is a raised Playwright error
    naming a changed network, or the harness's teardown <error> whose
    every item is a changed network."""
    message = element.get("message", "")
    if PAGE_LOAD.match(SETUP_OR_TEARDOWN.sub("", message, count=1)):
        return True
    raised = harness_raised(element)
    if raised is None:
        return False
    if any(EVERY_ITEM.fullmatch(line) for line in raised):
        items = listed_items(raised)
        return items is not None and items_are_changed_network(items)
    return any(ONE_ITEM.fullmatch(line) for line in raised)


def failure_line(element: ET.Element, test: str | None, changed_tests: set[str]) -> str:
    """One line naming a failure that is not a changed network: its test
    (None when no <testcase> holds it), its first message line, the first
    item of a harness teardown that is not one, and whether its test is in
    `changed_tests`, those that also failed on a changed network."""
    first = element.get("message", "").split("\n", 1)[0]
    line = f"  {element.tag} {test or '(no test)'}: {first}"
    items = listed_items(harness_raised(element) or [])
    if items is not None:
        other = next((item for item in items if item_kind(item) == "other"), None)
        line += "; first item not a changed network: " + (
            other or "none, but more console lines than failed requests"
        )
    if test in changed_tests:
        line += "; its test also failed on a changed network"
    return line


def refusal(
    failures: list[ET.Element], changed: set[ET.Element], test_of: dict[ET.Element, str]
) -> str:
    """The reason for no rerun: how many of `failures` are not a changed
    network, then a line for each, by its test."""
    others = [element for element in failures if element not in changed]
    changed_tests = {test_of[element] for element in changed if element in test_of}
    return "\n".join(
        [
            f"{len(others)} of {len(failures)} failures and errors are not a"
            f" changed network (a Playwright {MARKER}, or the browser harness's"
            " teardown naming one): no rerun, and the status stays pytest's.",
            *(
                failure_line(element, test_of.get(element), changed_tests)
                for element in others
            ),
        ]
    )


def carried_options(args: list[str]) -> list[str]:
    """The run's `-m`, `-k` and `-p` options, each with its value, in the
    order given, whether written `-m browser` or `-mbrowser`."""
    carried = []
    words = iter(args)
    for word in words:
        if word in CARRIED:
            value = next(words, None)
            if value is not None:
                carried += [word, value]
        elif word[:2] in CARRIED:
            carried.append(word)
    return carried


NO_FILE = "No failed file is named in the summary: rerun the run's own command."


def rerun_args(
    junit: str | None, log: str, args: list[str]
) -> tuple[list[str] | None, str]:
    """(the rerun's pytest arguments, "") when every failure and error in
    `junit` carries the error and the summary in `log` names a file, else
    (None, the reason: several lines when a failure is not a changed
    network, see `refusal`)."""
    if junit is None:
        return None, "no junit.xml to read: no rerun."
    try:
        root = ET.fromstring(junit)
    except ET.ParseError as error:
        return None, f"junit.xml does not parse ({error}): no rerun."
    failures = [*root.iter("failure"), *root.iter("error")]
    if not failures:
        return None, NO_FILE
    # pytest writes a teardown's <error> in a <testcase> of its own, beside
    # the call's, under the same name.
    test_of = {
        child: f"{case.get('classname')}::{case.get('name')}"
        for case in root.iter("testcase")
        for child in case
    }
    changed = {element for element in failures if changed_network(element)}
    if len(changed) < len(failures):
        return None, refusal(failures, changed, test_of)
    summary = log.split("short test summary info", 1)
    files = sorted(set(SUMMARY_FILE.findall(summary[1]))) if len(summary) > 1 else []
    if not files:
        return None, NO_FILE
    carried = carried_options(args)
    # Beside `-p no:xdist`, `-n` is no option: pytest exits 4 (#373 review).
    no_xdist = "-pno:xdist" in carried or any(
        pair == ("-p", "no:xdist") for pair in zip(carried, carried[1:], strict=False)
    )
    return [*carried, *([] if no_xdist else ["-n", "0"]), *files], ""


def outcome(
    status: int, veth: int | None, rerun_status: int, flake_ticket: bool
) -> tuple[int, str]:
    """(the script's exit status, the line that says why)."""
    seen = "no veth count" if veth is None else f"{veth} veth lines"
    first = f"The first run exited {status}, with {seen} in its window;"
    if rerun_status != 0:
        return status, f"{first} the rerun failed too (exit {rerun_status})."
    rerun = f"{first} the rerun passed"
    if flake_ticket:
        return status, (
            f"{rerun}, and --flake-ticket keeps pytest's status {status}:"
            " a race keeps its 25 runs (D122)."
        )
    if veth is None or veth <= 0:
        return status, (
            f"{rerun}, but with no veth lines counted the status stays"
            f" pytest's {status} (D122)."
        )
    return 0, (
        f"{rerun}: the run counts as passed for D101's repeats only (D122),"
        " never for a flake ticket; its failures stay in pytest.log."
    )


def read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def main(argv: list[str]) -> int:
    if argv[:1] == ["rerun-args"] and len(argv) >= 4:
        # A `--` before the run's arguments is no option of theirs to carry.
        found, reason = rerun_args(read(argv[1]), read(argv[2]) or "", argv[4:])
        if found is None:
            print(reason)
            return 1
        with open(argv[3], "w", encoding="utf-8") as handle:
            handle.write("".join(f"{arg}\n" for arg in found))
        return 0
    if argv[:1] == ["outcome"] and len(argv) in (4, 5):
        flake = argv[4:] == ["--flake-ticket"]
        if len(argv) == 5 and not flake:
            print(f"network_changed.py: unknown option {argv[4]}", file=sys.stderr)
            return 2
        veth = int(argv[2]) if argv[2] else None
        status, line = outcome(int(argv[1]), veth, int(argv[3]), flake)
        print(f"{line}\nexit {status}")
        return 0
    print(__doc__.split("Usage, from the script:", 1)[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
