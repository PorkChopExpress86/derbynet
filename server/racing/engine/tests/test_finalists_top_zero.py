"""A top 0 cut, as `calculate_roster_from_keys` makes it. The expected
answers were recorded from PHP by `tools/standings_differential` on this
event: drop-slowest scoring, and racer 3's second heat has a place but no
time, so racer 3 scores (3.1 - 3.1) / 1 = 0.0."""

import pytest

from racing.engine.standings import finalists, oracle
from racing.engine.standings.scoring import Scoring

EVENT = oracle.Event(
    groups=(oracle.Group(1, "Lions", 1),),
    subgroups=(oracle.Subgroup(2, 1, "Den 1", 1),),
    rounds=(oracle.Round(4, 1, 1, finished=True),),
    racers=(
        oracle.Racer(3, "Al", "Ames", 101, "", group_id=1, subgroup_id=2),
        oracle.Racer(5, "Bo", "Bell", 102, "", group_id=1, subgroup_id=2),
    ),
    results=(
        oracle.Result(4, 3, 3.1, 1),
        oracle.Result(4, 5, 3.2, 2),
        oracle.Result(4, 5, 3.3, 2),
        oracle.Result(4, 3, None, 1),
    ),
    settings=oracle.Settings(scoring=Scoring.DROP_SLOWEST, lane_count=4),
)


@pytest.mark.parametrize("limit", [0, 1])
def test_a_score_of_zero_makes_a_top_zero_cut(limit):
    cut = finalists.follow_on(oracle.Standings(EVENT), 1, bucketed=False, limit=limit)
    assert (cut.finalists, cut.concerns) == ((finalists.Finalist(3, 0.0),), ())
