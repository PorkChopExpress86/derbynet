"""D33: a racer not eligible for any award, or not eligible for speed
awards, races and keeps the standings rows, but every place, ladder, speed
award and finalist is what it would be with the racer removed."""

import pytest

from racing.engine.standings import finalists
from racing.engine.standings.eligibility import AwardSettings, NotEligible
from racing.engine.standings.oracle import FULL_FIELD, Standings
from racing.engine.standings.speed_awards import TrophySettings, speed_awards
from racing.engine.tests.standings_events import (
    BLUE,
    BOTH,
    RED,
    random_event,
    with_awards,
    without,
)

TROPHIES = [
    TrophySettings(),
    TrophySettings(one_trophy_per=True),
    TrophySettings(n_subgroup=2, one_trophy_per=True),
]


def outcomes(standings):
    return {
        (row.round.id, row.racer.id): row.outcome
        for row in standings.rows
        if row.outcome
    }


def cuts(standings):
    return [
        finalists.follow_on(standings, RED, bucketed=False, limit=3),
        finalists.follow_on(standings, BLUE, bucketed=True, limit=2),
        finalists.for_aggregate(standings, BOTH, bucketed=True, limit=3),
        finalists.for_aggregate(standings, BOTH, bucketed=False, limit=4),
    ]


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("full_field_calc", [0, -1, BOTH])
def test_marked_leaders_change_nothing_but_their_own_rows(seed, full_field_calc):
    event = random_event(seed, full_field_calc=full_field_calc)
    first, second = [r for rung in Standings(event).ladders[FULL_FIELD] for r in rung][
        :2
    ]
    marked = with_awards(
        event,
        {
            first: AwardSettings(not_eligible_for_any_award=True),
            second: AwardSettings(not_eligible_for_speed_awards=True),
        },
    )
    standings = Standings(marked)
    removed = Standings(without(event, {first, second}))

    assert standings.ladders == removed.ladders
    assert standings.catalog_counts == removed.catalog_counts
    assert outcomes(standings) == outcomes(removed)
    assert cuts(standings) == cuts(removed)
    for trophies in TROPHIES:
        assert speed_awards(standings, trophies) == speed_awards(removed, trophies)

    for racer_id, why in (
        (first, NotEligible.ANY_AWARD),
        (second, NotEligible.SPEED_AWARDS),
    ):
        rows = [row for row in standings.rows if row.racer.id == racer_id]
        assert rows, "the racer's rows stay"
        for row in rows:
            assert row.outcome == {}
            assert set(row.not_eligible.values()) == {why}
            assert FULL_FIELD in row.not_eligible
