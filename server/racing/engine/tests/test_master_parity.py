"""Master-heat numbering against the PHP fixtures of #7.

`http/master-schedule/half-weights.http` races rounds of 2, 3, 5 and 9 heats
on two lanes, so every weight lands on a half. A poll taken right after a
chart change shows what `assign_master_heats()` wrote. The engine, given the
unraced heats and the start number read from that poll, must give every
`(roundid, heat)` the number PHP gave it.
"""

import json

import pytest

from racing.engine.scheduling.master import assign_master_heats

from .golden import GOLDEN_DIR, read_transcript

FIXTURE = GOLDEN_DIR / "http" / "master-schedule" / "half-weights.http"
EXCHANGES = read_transcript(FIXTURE)

# Polls taken right after a chart change, before any result moved.
AFTER_CHART_CHANGE = ["007 ondeck-in-master-order", "013 ondeck-after-regenerating"]


def body(exchange):
    return json.loads(EXCHANGES[exchange]["body"])


def php_numbering(exchange):
    """`{(roundid, heat): masterheat}` and the heats with a result."""
    numbers, raced = {}, set()
    for row in body(exchange)["ondeck"]["chart"]:
        numbers[(row["roundid"], row["heat"])] = row["masterheat"]
        if row["result"]:
            raced.add((row["roundid"], row["heat"]))
    return numbers, raced


@pytest.mark.parametrize("exchange", AFTER_CHART_CHANGE)
def test_numbering_matches_php(exchange):
    levels = {r["roundid"]: r["round"] for r in body("006 rounds")["rounds"]}
    php, raced = php_numbering(exchange)
    unraced = [(levels[r], r, h) for (r, h) in php if (r, h) not in raced]
    start = 1 + max((php[heat] for heat in raced), default=0)

    django = assign_master_heats(unraced, start)

    assert django | {heat: php[heat] for heat in raced} == php


def test_the_fixture_shows_both_half_weight_cases():
    """5 heats against 2 is weight 2.5: PHP takes 3 heats of round 3 per
    pass, where Python's round() would take 2. 3 against 2 is 1.5, where
    both give 2."""
    php, _ = php_numbering("007 ondeck-in-master-order")
    first_pass = sorted((n, heat) for heat, n in php.items() if n <= 11)
    assert [heat for _, heat in first_pass] == [
        (1, 1),
        (2, 1),
        (2, 2),
        (3, 1),
        (3, 2),
        (3, 3),
        (4, 1),
        (4, 2),
        (4, 3),
        (4, 4),
        (4, 5),
    ]
    _, raced = php_numbering("013 ondeck-after-regenerating")
    assert raced == {(1, 1)}
