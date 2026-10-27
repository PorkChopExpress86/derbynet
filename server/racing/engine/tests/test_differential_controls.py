"""The seeded faults of `tools/standings_differential --controls` run, and
each one is caught. PHP's side here is the engine's own answer before the
fault, in the shape `harness.php` writes, so a fault shows as a difference
exactly where it would against PHP."""

import json

import pytest

from tools.standings_differential.compare import compare, django_side
from tools.standings_differential.controls import CONTROLS, fault
from tools.standings_differential.generate import BATCHES, random_event


def as_php(ev: dict) -> dict:
    keys = [a["key"] for a in django_side(ev, [])["awards"]]
    keys = list(dict.fromkeys(keys + ev["extra_award_keys"]))
    ours = django_side(ev, keys)
    cars = {r["racerid"]: r["carnumber"] for r in ev["RegistrationInfo"]}
    php = {
        k: ours[k]
        for k in (
            "rows",
            "ladders",
            "catalog_counts",
            "catalog",
            "class_keys",
            "rank_keys",
            "topological_order",
            "awards",
            "reserved_overall",
            "finalists",
        )
    }
    php["current"] = [
        {
            "key": key,
            "failure": None,
            "current": None
            if c is None
            else {
                "key": c["key"],
                "awardname": c["awardname"],
                "carnumber": cars[c["racerid"]],
            },
            "racerid": None if c is None else c["racerid"],
        }
        for key, c in zip(keys, ours["current"], strict=True)
    ]
    php["id"] = ev["id"]
    php["warnings"] = []
    return json.loads(json.dumps(php))


@pytest.fixture(scope="module")
def events():
    evs = [random_event(seed) for seed in range(20)]
    evs += [random_event(seed, batch) for batch in BATCHES for seed in range(3)]
    return [(ev, as_php(ev)) for ev in evs]


def test_the_engine_agrees_with_itself(events):
    assert [ev["id"] for ev, php in events if compare(ev, php).differing] == []


@pytest.mark.parametrize("name", list(CONTROLS))
def test_each_seeded_fault_is_caught(events, name):
    category = CONTROLS[name][0]
    with fault(name):
        caught = sum(1 for ev, php in events if compare(ev, php).differing[category])
    assert caught > 0
