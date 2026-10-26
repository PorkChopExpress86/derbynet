"""`current_speed_award` reads a `current_award_key` exactly as
`query.award.current` does, oddly written numbers included. The expected
answers were recorded from PHP 8.4 by running `tools/standings_differential`'s
`harness.php` on this event: one group, one subgroup, two racers, one heat,
and `n-rank-trophies` 2, with `full-field-calc` 0 and then 1. A key PHP
answers with a TypeError (`speed-x1`) names no award."""

from dataclasses import replace

import pytest

from racing.engine.standings import oracle
from racing.engine.standings.speed_awards import (
    TrophySettings,
    current_speed_award,
    speed_awards,
)

EVENT = oracle.Event(
    groups=(oracle.Group(1, "Lions", 1),),
    subgroups=(oracle.Subgroup(2, 1, "Den 1", 1),),
    rounds=(oracle.Round(4, 1, 1, finished=True),),
    racers=(
        oracle.Racer(3, "Al", "Ames", 101, "", group_id=1, subgroup_id=2),
        oracle.Racer(5, "Bo", "Bell", 102, "", group_id=1, subgroup_id=2),
    ),
    results=(oracle.Result(4, 3, 3.0, 1), oracle.Result(4, 5, 3.1, 2)),
    settings=oracle.Settings(lane_count=4, use_subgroups=True),
)


@pytest.mark.parametrize(
    ("full_field_calc", "key", "expected"),
    [
        (0, "speed-02", ("speed-02", " Fastest in Pack", 5)),
        (0, "speed-2.0", ("speed-2.0", " Fastest in Pack", 5)),
        (0, "speed-2.", ("speed-2.", " Fastest in Pack", 5)),
        (0, "speed-+2", ("speed-+2", " Fastest in Pack", 5)),
        (0, "speed- 2", ("speed- 2", " Fastest in Pack", 5)),
        (0, "speed-2 ", ("speed-2 ", " Fastest in Pack", 5)),
        (0, "speed-2e0", ("speed-2e0", " Fastest in Pack", 5)),
        (0, "speed-01", ("speed-01", "Fastest in Pack", 3)),
        (0, "speed-1.0", ("speed-1.0", "Fastest in Pack", 3)),
        (0, "speed-0", None),
        (0, "speed--1", None),
        (0, "speed-T1", None),
        (0, "speed-1A", None),
        (0, "speed-x1", None),
        (0, "speed-\u0661", None),
        (0, "speed-25.0", None),
        (0, "speed-0x1", None),
        (0, "speed-01a", ("speed-01a", "Fastest in Pack", 3)),
        (0, "speed-1.0a", ("speed-1.0a", "Fastest in Pack", 3)),
        (0, "speed-2-1", ("speed-2-1", "2nd Fastest in Lions", 5)),
        (0, "speed-2- 1", ("speed-2- 1", "2nd Fastest in Lions", 5)),
        (0, "speed-2-1.0", ("speed-2-1.0", "2nd Fastest in Lions", 5)),
        (0, "speed-2-01", ("speed-2-01", "2nd Fastest in Lions", 5)),
        (0, "speed-2-1 ", ("speed-2-1 ", "2nd Fastest in Lions", 5)),
        (0, "speed-2-x", None),
        (0, "speed-2-", ("speed-2-", "2nd Fastest in ", 5)),
        (0, "speed-1-1-02", ("speed-1-1-02", "Fastest in Den 1", 3)),
        (0, "speed-1-1-2.0", ("speed-1-1-2.0", "Fastest in Den 1", 3)),
        (0, "speed-1-1- 2", ("speed-1-1- 2", "Fastest in Den 1", 3)),
        (0, "speed-1-1-", ("speed-1-1-", "Fastest in ", 3)),
        (0, "speed-1-1-x", None),
        (0, "speed-1-01-02", ("speed-1-01-02", "Fastest in Den 1", 3)),
        (0, "speed-02-0", None),
        (0, "speed-1-0", None),
        (1, "speed-1-1", ("speed-1", "Fastest in Pack", 3)),
        (1, "speed-1-01", ("speed-1", "Fastest in Pack", 3)),
        (1, "speed-1-1.0", ("speed-1", "Fastest in Pack", 3)),
        (1, "speed-1- 1", ("speed-1", "Fastest in Pack", 3)),
        (1, "speed-02-1", ("speed-02", " Fastest in Pack", 5)),
        (1, "speed-1a-1", ("speed-1", "Fastest in Pack", 3)),
        (1, "speed-2-1.0", ("speed-2", "2nd Fastest in Pack", 5)),
    ],
)
def test_an_oddly_written_key_resolves_as_php_resolves_it(
    full_field_calc, key, expected
):
    event = replace(
        EVENT, settings=replace(EVENT.settings, full_field_calc=full_field_calc)
    )
    standings = oracle.Standings(event)
    awards = speed_awards(standings, TrophySettings(n_subgroup=2)).awards
    current = current_speed_award(key, standings, awards)
    got = None if current is None else (current.key, current.name, current.racer_id)
    assert got == expected
