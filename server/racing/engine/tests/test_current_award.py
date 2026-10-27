"""`current_speed_award` resolves a `current_award_key` as
`query.award.current` does. The expected answers were recorded from PHP by
`tools/standings_differential` on this event: one group with one subgroup,
one racer, one heat."""

import pytest

from racing.engine.standings import oracle
from racing.engine.standings.speed_awards import (
    TrophySettings,
    current_speed_award,
    speed_awards,
)

EVENT = oracle.Event(
    groups=(oracle.Group(1, "Lions", 1),),
    subgroups=(oracle.Subgroup(2, 1, "Den 1", 1, n_trophies=1),),
    rounds=(oracle.Round(4, 1, 1, finished=True),),
    racers=(oracle.Racer(3, "Al", "Ames", 101, "", group_id=1, subgroup_id=2),),
    results=(oracle.Result(4, 3, 3.0, 1),),
    settings=oracle.Settings(lane_count=4, use_subgroups=True),
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("speed-1", ("speed-1", "Fastest in Pack", 3)),
        ("speed-1-1", ("speed-1-1", "Fastest in Lions", 3)),
        ("speed-1-1-2", ("speed-1-1-2", "Fastest in Den 1", 3)),
        # PHP compares the id text with `==`: text that is not a number
        # equals no group, and empty text equals the pack's null group.
        ("speed-1-abc", None),
        ("speed-1-1-x", None),
        ("speed-1-", ("speed-1-", "Fastest in ", 3)),
        ("speed-1--", ("speed-1--", "Fastest in ", 3)),
    ],
)
def test_current_award_matches_php(key, expected):
    standings = oracle.Standings(EVENT)
    awards = speed_awards(standings, TrophySettings()).awards
    current = current_speed_award(key, standings, awards)
    got = None if current is None else (current.key, current.name, current.racer_id)
    assert got == expected
