"""A step the server workflow runs against Django is not in the parity gate's
WAITING or SKIPS (`server/tools/parity_gate.py`).

A suite that passes in `.github/workflows/server.yml` waits on no ticket, and
the Django leg can run it. #203 put test-basic-racing.sh in the workflow and
left its WAITING entry, and the nightly gate went red on it a day later (#204
retro). This finds a stale entry in the pull request that makes it stale, not
in the next nightly run.
"""

import re
from pathlib import Path

from tools.parity_gate import SKIPS, WAITING

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "server.yml"
# `bash testing/<script> <url> [argument ...]`, as a step label reads it:
# the script and its arguments (`testing/steps.sh`).
RUN = re.compile(r"^\s*bash testing/([\w-]+\.sh) \S+((?: [^\s|>]+)*)\s*$", re.MULTILINE)
# " (2)": the second run of the same words (`_step_label`, `testing/steps.sh`).
NTH_RUN = re.compile(r" \(\d+\)$")

# Stale entries the lead has yet to decide on, with why. Remove an entry here
# with its entry in parity_gate.py; the test fails while one is listed that
# isn't stale any more.
KNOWN_STALE: dict[str, str] = {}


def ran_against_django(workflow: str) -> set[str]:
    return {f"{script}{arguments}" for script, arguments in RUN.findall(workflow)}


def stale(workflow: str, waiting, skips) -> dict[str, str]:
    ran = ran_against_django(workflow)
    found = {}
    for label in waiting:
        if NTH_RUN.sub("", label) in ran:
            found[label] = "WAITING"
    for label in skips:
        if NTH_RUN.sub("", label) in ran:
            found[label] = "SKIPS"
    return found


def test_it_reads_a_step_label_from_a_workflow_line():
    workflow = (
        "        run: |\n"
        "          bash testing/test-a.sh http://127.0.0.1:8080\n"
        "          bash testing/test-b.sh http://127.0.0.1:8080 basic\n"
        "          echo x \\\n"
        "            | bash testing/test-c.sh http://127.0.0.1:8080 | grep y\n"
    )
    assert ran_against_django(workflow) == {"test-a.sh", "test-b.sh basic"}


def test_a_waiting_or_skipped_step_the_workflow_runs_is_stale():
    workflow = "  bash testing/test-a.sh http://h\n  bash testing/test-b.sh http://h\n"
    waiting = {"test-a.sh (2)": None, "test-c.sh": None}
    assert stale(workflow, waiting, {"test-b.sh": "x"}) == {
        "test-a.sh (2)": "WAITING",
        "test-b.sh": "SKIPS",
    }


def test_no_step_the_workflow_runs_against_django_waits_or_is_skipped():
    found = stale(WORKFLOW.read_text(), WAITING, SKIPS)
    new = {label: where for label, where in found.items() if label not in KNOWN_STALE}
    assert new == {}, (
        "server.yml runs these against Django, so they pass there: remove each"
        " from its list in server/tools/parity_gate.py in this pull request, or"
        f" say in the PR body why the Django leg still can't run it: {new}"
    )
    assert set(KNOWN_STALE) <= set(found), (
        "KNOWN_STALE lists a step that is no longer stale; remove it:"
        f" {sorted(set(KNOWN_STALE) - set(found))}"
    )
