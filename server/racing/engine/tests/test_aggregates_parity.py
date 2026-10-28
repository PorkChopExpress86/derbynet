"""Aggregate order and closure against `engine/aggregates.json`.

`capture_aggregates()` in `capture-engine.php` runs the real
`topological_sort()` and `compute_aggregate_constituents_tc()` on hand-made
and seeded random aggregate graphs. Django's closure matches PHP's on every
case. Its order is a real topological order. On these cases it differs from
PHP's exactly where PHP's comparator, which is not transitive, gives an
invalid order (Q8), and equals it wherever PHP's order is valid. The cases
that differ are listed under Q8 in `tests/golden/deviations.json`, written by

    uv run python -m racing.engine.tests.test_aggregates_parity
"""

import json

import pytest

from racing.engine.groups.aggregates import (
    Aggregate,
    constituents_closure,
    topological_order,
)

from .golden import DEVIATIONS_FILE, GOLDEN_DIR, engine_deviations, read_cases

FIXTURE = "engine/aggregates.json"
EXPECTED = "deviations/Q8/aggregates.json"
CASES = read_cases(GOLDEN_DIR / FIXTURE)
REASON = (
    "PHP sorts with a comparator that is not transitive, so its order can put an"
    " aggregate after a group it draws from; Django's is a topological order that"
    " keeps the input order between unrelated groups."
)

pytestmark = pytest.mark.pr


def django_record(case):
    aggregates = [Aggregate(g, tuple(c)) for g, c in case["classes"]]
    return {
        "classes": case["classes"],
        "order": [a.group_id for a in topological_order(aggregates)],
        "closure": {str(g): c for g, c in constituents_closure(aggregates).items()},
    }


def is_topological(case, order):
    """Every aggregate comes before each aggregate it draws from."""
    position = {g: i for i, g in enumerate(order)}
    return sorted(order) == sorted(g for g, _ in case["classes"]) and all(
        position[g] < position[c]
        for g, constituents in case["classes"]
        for c in constituents
        if c in position
    )


@pytest.mark.parametrize("case_id", list(CASES))
def test_order_and_closure_follow_php_or_the_q8_deviation(case_id):
    php = CASES[case_id]
    django = django_record(php)
    deviated = engine_deviations(FIXTURE)

    assert django["closure"] == php["closure"]
    assert is_topological(php, django["order"])
    if case_id in deviated:
        assert django == deviated[case_id]
        assert django["order"] != php["order"], f"{case_id} no longer differs"
    else:
        assert django["order"] == php["order"]


def test_the_fixture_holds_the_cases_the_ticket_names():
    """The #20 case, chains of 3 or more, and random graphs where PHP's
    order is invalid."""
    q8 = CASES["two-unrelated-and-an-aggregate-of-the-first"]
    assert q8["order"] == [1, 2, 3]
    assert django_record(q8)["order"] == [2, 3, 1]
    assert {"chain-of-3-in-order", "chain-of-3-reversed", "chain-of-4-shuffled"} <= set(
        CASES
    )
    invalid = [
        case_id
        for case_id, case in CASES.items()
        if case_id.startswith("seed=") and not is_topological(case, case["order"])
    ]
    assert len(invalid) >= 100
    invalid_anywhere = {
        c for c, case in CASES.items() if not is_topological(case, case["order"])
    }
    assert set(engine_deviations(FIXTURE)) == invalid_anywhere
    for case in CASES.values():
        assert "order_error" not in case and "closure_error" not in case


def write_q8_deviation():
    differ = {
        case_id: django_record(case)
        for case_id, case in CASES.items()
        if django_record(case)["order"] != case["order"]
    }
    lines = [
        f"{json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}"
        for k, v in differ.items()
    ]
    (GOLDEN_DIR / EXPECTED).parent.mkdir(parents=True, exist_ok=True)
    (GOLDEN_DIR / EXPECTED).write_text("{\n" + ",\n".join(lines) + "\n}\n")
    listing = json.loads(DEVIATIONS_FILE.read_text())
    entries = [e for e in listing["deviations"] if e["fixture"] != FIXTURE]
    entries.append(
        {
            "quirk": "Q8",
            "fixture": FIXTURE,
            "cases": list(differ),
            "reason": REASON,
            "expected": EXPECTED,
        }
    )
    listing["deviations"] = sorted(entries, key=lambda e: int(e["quirk"][1:]))
    DEVIATIONS_FILE.write_text(json.dumps(listing, indent=2, ensure_ascii=False) + "\n")
    print(f"Q8: {len(differ)} of {len(CASES)} cases written to {EXPECTED}")


if __name__ == "__main__":
    write_q8_deviation()
