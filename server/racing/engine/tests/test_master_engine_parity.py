"""Master-heat numbering and heat navigation against `engine/master-heats.json`.

`capture_master_heats()` in `capture-engine.php` builds each chart state on the
real SQLite schema, records `get_next_heat()` under `use-master-sched` for
every request from every heat, runs `assign_master_heats()`, and records the
numbers it wrote and the navigation again. Each case is replayed here through
`assign_master_heats` and `master_target`, with the inputs
`racing.services.master` reads from the same rows.
"""

import pytest

from racing.engine.scheduling.master import assign_master_heats, master_target

from .golden import GOLDEN_DIR, read_cases

FIXTURE = GOLDEN_DIR / "engine" / "master-heats.json"
CASES = read_cases(FIXTURE)
REQUESTS = ("first", "next", "prev", "next-up")

pytestmark = pytest.mark.pr


def has_result(row):
    _roundid, _heat, _lane, time, place, _n = row
    return time is not None or place is not None


def renumber(case):
    """The master heat of each row after `reassign_master_heats` would run."""
    level = dict(map(tuple, case["rounds"]))
    rows = case["rows"]
    unraced = {(level[row[0]], row[0], row[1]) for row in rows if not has_result(row)}
    start = 1 + max((row[5] for row in rows if has_result(row)), default=0)
    numbers = assign_master_heats(unraced, start)
    return [numbers.get((row[0], row[1]), row[5]) for row in rows]


def navigation(rows, masterheats, currents):
    index = [
        (n, row[0], row[1], has_result(row))
        for row, n in zip(rows, masterheats, strict=True)
    ]
    answers = {}
    for key in currents:
        current = None if key == "none" else tuple(map(int, key.split("/")))
        answers[key] = [
            None if (t := master_target(index, current, r)) is None else list(t)
            for r in REQUESTS
        ]
    return answers


@pytest.mark.parametrize("case_id", list(CASES))
def test_numbering_and_navigation_match_php(case_id):
    case = CASES[case_id]
    before = [row[5] for row in case["rows"]]

    after = renumber(case)

    assert after == case["masterheats"]
    assert (
        navigation(case["rows"], before, case["navigation_before"])
        == (case["navigation_before"])
    )
    assert (
        navigation(case["rows"], after, case["navigation_after"])
        == (case["navigation_after"])
    )


def test_the_fixture_holds_the_cases_the_ticket_names():
    assert {
        "half-weights",
        "heat-number-gaps",
        "round-without-a-chart",
        "several-levels",
        "partly-raced-heat",
        "raced-out-of-order",
    } <= set(CASES)
    assert sum(case_id.startswith("seed=") for case_id in CASES) == 200
    for case in CASES.values():
        assert "warnings" not in case and "error" not in case
        assert all(
            answer is None or isinstance(answer, list)
            for answers in case["navigation_after"].values()
            for answer in answers
        )


def test_half_weights_take_php_rounding():
    """5 heats over 2 is 2.5, which PHP rounds to 3 and Python's round() to 2.
    Heats 1 to 3 of round 3 come before heat 1 of round 4."""
    case = CASES["half-weights"]
    numbers = {
        (row[0], row[1]): n
        for row, n in zip(case["rows"], case["masterheats"], strict=True)
    }
    assert [numbers[(3, h)] for h in (1, 2, 3)] == [4, 5, 6]
    assert numbers[(4, 1)] == 7
