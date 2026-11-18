"""`tools/reschedule_deviations.py` names the quirk that explains each
reschedule reply that differs from PHP's, and refuses one none explains."""

import importlib.util
from pathlib import Path

import pytest

from racing.engine.tests.reschedule_replay import Reply

TOOL = Path(__file__).resolve().parents[1] / "tools" / "reschedule_deviations.py"


@pytest.fixture(scope="module")
def quirk_of():
    spec = importlib.util.spec_from_file_location("reschedule_deviations", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.quirk_of


PHP = ["- 4", "2 5", "3 -", "4 2", "5 3"]


@pytest.mark.parametrize(
    ("reply", "php", "quirk"),
    [
        (Reply(None, [], {}, "notonroster"), PHP, "Q25"),
        (Reply(["1 2"], [], {}), None, "Q21"),
        (Reply(["2 4", "1 5"], [], {1: "1 4"}), ["2 4", "1 5"], "Q20"),
        (Reply(["2 4", "3 5", "4 2", "5 3"], [], {}), PHP, "Q3"),
        (Reply(["5 4", "2 -", "3 -", "4 2", "- 3"], [], {}), PHP, None),
        (Reply(None, [], {}), PHP, None),
    ],
)
def test_each_difference_gets_the_quirk_that_explains_it(quirk_of, reply, php, quirk):
    """Q3 only for a chart with fewer heats than PHP's: one as long as PHP's,
    or none at all, is no Q3."""
    assert quirk_of(reply, php) == quirk
