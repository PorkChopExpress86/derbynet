"""The decisions `tools/pytest-under-load.sh` makes after a load run fails
with Chromium's net::ERR_NETWORK_CHANGED, which marks the machine's network
changing as another project's container joins or leaves a Docker bridge
(D122, #373). The script runs pytest; this module reads its text.

- `rerun_args(junit, log, args)`: when every failure and error in the run's
  junit.xml carries the error as a page load or a failed request does, the
  pytest arguments that rerun each failed file alone, with no xdist: the
  run's own `-m`, `-k` and `-p` options, then `-n 0` and the files the
  summary names. Otherwise `None` and the reason.
- `outcome(status, veth, rerun_status, flake_ticket)`: the script's exit
  status and the line that says why. A clean rerun with veth lines in the
  run's window passes the run for D101's repeats only; a flake ticket's
  run, a veth count of 0 or one not read, and a failed rerun keep pytest's
  status.

Usage, from the script:

    network_changed.py rerun-args JUNIT LOG [-- RUN_ARG ...]
        prints one rerun argument per line and exits 0, or prints the
        reason there is no rerun and exits 1
    network_changed.py outcome STATUS VETH RERUN_STATUS [--flake-ticket]
        prints the outcome line and exits with the script's status; VETH
        is a count, or empty when the kernel log was not read
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET

MARKER = "net::ERR_NETWORK_CHANGED"

# The two forms a browser test fails with when the network changes, as in
# #348's load run of test_kiosk_results_by_racer.py (#348 retro):
# Playwright's page load, a raised line of its own,
#   playwright._impl._errors.Error: Page.goto: net::ERR_NETWORK_CHANGED at
#   http://localhost:52751/kiosk.php?address=kiosk-test52
# and the harness's failed request in its teardown assert
# (`ui/tests/browser/conftest.py`, "request failed: {request.url} {request.failure}"),
#   Left contains one more item: 'request failed:
#   http://localhost:52751/kiosk.php?address=kiosk-test52 net::ERR_NETWORK_CHANGED'
# The bare text is not enough: tests/test_pytest_under_load.py's asserts print
# the script's own lines naming it, and a flake there must keep its failure.
# PAGE_LOAD.match reads it from the start of a line only.
PAGE_LOAD = re.compile(
    r"playwright\._impl\._errors\.\w+: \w+\.\w+: " + MARKER + r" at https?://"
)
REQUEST_FAILED = re.compile(r"'request failed: https?://\S+ " + MARKER + "'")

# A failed test or error in the -rfE summary, by the file pytest names.
SUMMARY_FILE = re.compile(r"^(?:FAILED|ERROR) ([^: ]*\.py)", re.M)

# The run's options that choose its tests or plugins, carried into the
# rerun: `-m browser` selects the browser tests that `addopts` deselects.
CARRIED = ("-m", "-k", "-p")


def network_line(line: str) -> bool:
    """Whether a line of an error, its `E` taken off, is one of the forms
    a changed network fails a browser test with."""
    return bool(PAGE_LOAD.match(line) or REQUEST_FAILED.search(line))


def carries_marker(element: ET.Element) -> bool:
    """Whether a junit <failure> or <error> carries a changed network in a
    line of its message or a raised line (`E   ...`) of its traceback. A
    source line the traceback quotes is no failure of the network: a test
    that defines the text would match."""
    lines = [line.strip() for line in element.get("message", "").splitlines()]
    lines += [
        line[1:].strip()
        for line in (element.text or "").splitlines()
        if line.startswith("E ")
    ]
    return any(network_line(line) for line in lines)


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


def rerun_args(
    junit: str | None, log: str, args: list[str]
) -> tuple[list[str] | None, str]:
    """(the rerun's pytest arguments, "") when every failure and error in
    `junit` carries the error and the summary in `log` names a file, else
    (None, the reason)."""
    if junit is None:
        return None, "no junit.xml to read: no rerun."
    try:
        root = ET.fromstring(junit)
    except ET.ParseError as error:
        return None, f"junit.xml does not parse ({error}): no rerun."
    failures = [*root.iter("failure"), *root.iter("error")]
    if not failures:
        return None, (
            "No failed file is named in the summary: rerun the run's own command."
        )
    others = sum(1 for element in failures if not carries_marker(element))
    if others:
        return None, (
            f"{others} of {len(failures)} failures and errors are not a"
            f" changed network ({MARKER} as a page load or a failed request):"
            " no rerun, and the status stays pytest's."
        )
    summary = log.split("short test summary info", 1)
    files = sorted(set(SUMMARY_FILE.findall(summary[1]))) if len(summary) > 1 else []
    if not files:
        return None, (
            "No failed file is named in the summary: rerun the run's own command."
        )
    return [*carried_options(args), "-n", "0", *files], ""


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
    if argv[:1] == ["rerun-args"] and len(argv) >= 3:
        # A `--` before the run's arguments is no option of theirs to carry.
        found, reason = rerun_args(read(argv[1]), read(argv[2]) or "", argv[3:])
        if found is None:
            print(reason)
            return 1
        print("\n".join(found))
        return 0
    if argv[:1] == ["outcome"] and len(argv) in (4, 5):
        flake = argv[4:] == ["--flake-ticket"]
        if len(argv) == 5 and not flake:
            print(f"network_changed.py: unknown option {argv[4]}", file=sys.stderr)
            return 2
        veth = int(argv[2]) if argv[2] else None
        status, line = outcome(int(argv[1]), veth, int(argv[3]), flake)
        print(line)
        return status
    print(__doc__.split("Usage, from the script:", 1)[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
