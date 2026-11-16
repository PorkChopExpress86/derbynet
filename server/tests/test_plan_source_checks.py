"""plan-checks.yml runs, on a PR that changes only plan/, each source check
that reads plan/ (D128). server.yml skips such a PR, and #346's push ran
none of them; removing one decision row fails test_deviations.py (#346
retro). A source check reads plan/ when it names the folder as a string
literal or lists every tracked file with `git ls-files`: a new one fails
here until the workflow runs it. This file is a source check too, and
spells neither literal whole.
"""

import re

from tests.workflow_steps import ROOT, WORKFLOWS, steps

SERVER = ROOT / "server"
TEST_FILE = re.compile(r"[\w/]*tests/test_\w+\.py")
READS_PLAN = re.compile(r'"(?:plan|ls-files)"')


def source_checks() -> set[str]:
    text = (SERVER / "tools" / "source-checks.sh").read_text()
    return set(TEST_FILE.findall(text))


def plan_checks() -> set[str]:
    text = (WORKFLOWS / "plan-checks.yml").read_text()
    found = [s for s in steps(text) if "pytest" in s.script]
    assert len(found) == 1, found
    assert found[0].runs, "the step has if: false"
    return set(TEST_FILE.findall(found[0].script))


def reads_plan(name: str) -> bool:
    return READS_PLAN.search((SERVER / name).read_text()) is not None


def test_plan_checks_runs_each_source_check_that_reads_plan():
    readers = {name for name in source_checks() if reads_plan(name)}
    assert readers == plan_checks()


def test_the_list_is_the_five_found_on_2026_11_16():
    """A rule that found none would pass above with an empty step."""
    assert plan_checks() == {
        "legacy_api/tests/test_registry.py",
        "tests/test_deviations.py",
        "tests/test_docker_checks.py",
        "tests/test_listing_order.py",
        "tests/test_pipefail_pipes.py",
    }
