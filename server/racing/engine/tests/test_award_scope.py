"""D34: a partition such as Parents can be set not to count for
all-partition awards. Its racers then take no place in the pack's ladders
and win none of the pack's speed awards, but keep their places, and their
awards, in their own group and subgroup."""

from dataclasses import replace

import pytest

from racing.engine.standings import finalists
from racing.engine.standings.eligibility import AwardSettings, NotEligible
from racing.engine.standings.oracle import FULL_FIELD, Standings, round_key
from racing.engine.standings.speed_awards import TrophySettings, speed_awards
from racing.engine.tests.standings_events import (
    BOTH,
    ROUND_BOTH,
    random_event,
    with_awards,
    without,
)

PARENTS = AwardSettings(partition_counts_for_all_partition_awards=False)


def parents_lead(seed, full_field_calc):
    event = random_event(seed, full_field_calc=full_field_calc)
    standings = Standings(event)
    pack_ladder = (
        standings.award_ladder_full_field()
        if full_field_calc == 0
        else standings.award_ladder_class(BOTH)
    )
    fastest = standings.racers[pack_ladder[0][0]]
    parents = {r.id for r in event.racers if r.subgroup_id == fastest.subgroup_id}
    return event, parents


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("full_field_calc", [0, BOTH])
def test_the_pack_awards_skip_parents(seed, full_field_calc):
    event, parents = parents_lead(seed, full_field_calc)
    marked = Standings(with_awards(event, dict.fromkeys(parents, PARENTS)))
    removed = Standings(without(event, parents))
    counting = Standings(event)

    pack = FULL_FIELD if full_field_calc == 0 else round_key(ROUND_BOTH)
    assert marked.ladders[pack] == removed.ladders[pack]
    pack_ladders = {FULL_FIELD}
    if full_field_calc == BOTH:
        pack_ladders |= {
            k
            for k in counting.ladders
            if k.startswith(("st-q3", "st-r103", "st-rr103-"))
        }
    for key, ladder in counting.ladders.items():
        if key not in pack_ladders:
            assert marked.ladders[key] == ladder, key

    trophies = TrophySettings(n_subgroup=1)
    awards = speed_awards(marked, trophies).awards
    pack_group = None if full_field_calc == 0 else BOTH
    pack_awards = [a for a in awards if a.group_id == pack_group]
    assert pack_awards and not {a.racer_id for a in pack_awards} & parents
    assert pack_awards[0].racer_id in removed.ladders[pack][0]

    own = [
        a
        for a in speed_awards(counting, trophies).awards
        if a.racer_id in parents and a.group_id not in (None, BOTH)
    ]
    assert own, "a Parents racer wins in their own group or subgroup"
    assert set(own) <= set(awards)

    for row in marked.rows:
        if row.racer.id in parents and pack in row.not_eligible:
            assert set(row.not_eligible) <= pack_ladders
            assert set(row.not_eligible.values()) == {NotEligible.ALL_PARTITION_AWARDS}
            assert pack not in row.outcome
            if row.round.group_id != full_field_calc:
                assert row.outcome, "places in every other ladder stay"
    assert any(
        pack in row.not_eligible for row in marked.rows if row.racer.id in parents
    )


@pytest.mark.parametrize("seed", range(30))
def test_a_final_of_the_pack_aggregate_skips_parents(seed):
    event, parents = parents_lead(seed, BOTH)
    marked = Standings(with_awards(event, dict.fromkeys(parents, PARENTS)))
    cut = finalists.for_aggregate(marked, BOTH, bucketed=False, limit=4)
    assert not {f.racer_id for f in cut.finalists} & parents

    group = next(iter(parents)) // 100
    same = Standings(event)
    assert finalists.follow_on(
        marked, group, bucketed=False, limit=4
    ) == finalists.follow_on(same, group, bucketed=False, limit=4)

    elsewhere = Standings(
        with_awards(
            replace(event, settings=replace(event.settings, full_field_calc=0)),
            dict.fromkeys(parents, PARENTS),
        )
    )
    assert finalists.for_aggregate(
        elsewhere, BOTH, bucketed=False, limit=4
    ) == finalists.for_aggregate(
        Standings(replace(event, settings=replace(event.settings, full_field_calc=0))),
        BOTH,
        bucketed=False,
        limit=4,
    ), "an aggregate that is not the pack's keeps PHP's scope"
