"""D35: each award unit, the pack, a group or a subgroup, lists the
partitions its speed awards can go to. A racer outside a unit's list takes
no place in the ladder that unit's awards read, and wins none of its
awards, but keeps every other place and award. A final's cut reads its own
group's list, never the pack's."""

from dataclasses import replace

import pytest

from racing.engine.standings import finalists
from racing.engine.standings.eligibility import PACK, AwardUnit, NotEligible
from racing.engine.standings.oracle import FULL_FIELD, Standings, round_key
from racing.engine.standings.speed_awards import TrophySettings, speed_awards
from racing.engine.tests.standings_events import (
    BLUE,
    BOTH,
    RED,
    ROUND_BOTH,
    ROUND_RED,
    random_event,
    without,
)

TROPHIES = TrophySettings(n_subgroup=1)


def partitioned(event):
    """Two partitions in every subgroup."""
    return replace(
        event,
        racers=tuple(
            replace(r, partition_id=r.subgroup_id * 10 + r.id // 2 % 2)
            for r in event.racers
        ),
    )


def all_partitions(event):
    return {r.partition_id for r in event.racers}


def listing(event, unit, parents):
    lists = {unit: frozenset(all_partitions(event) - {parents})}
    return replace(event, partition_lists=lists)


def leader(standings, ladder):
    return standings.racers[ladder[0][0]]


def awards_of(standings, unit_group, unit_subgroup=None):
    return [
        a
        for a in speed_awards(standings, TROPHIES).awards
        if (a.group_id, a.subgroup_id) == (unit_group, unit_subgroup)
    ]


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("full_field_calc", [0, BOTH])
def test_the_pack_list_leaves_parents_out_of_the_pack_only(seed, full_field_calc):
    event = partitioned(random_event(seed, full_field_calc=full_field_calc))
    counting = Standings(event)
    pack = FULL_FIELD if full_field_calc == 0 else round_key(ROUND_BOTH)
    parents = leader(counting, counting.ladders[pack]).partition_id
    parent_ids = {r.id for r in event.racers if r.partition_id == parents}
    marked = Standings(listing(event, PACK, parents))
    removed = Standings(without(event, parent_ids))

    assert marked.ladders[pack] == removed.ladders[pack]
    for key, ladder in counting.ladders.items():
        if key != pack:
            assert marked.ladders[key] == ladder, f"{key} keeps its places"

    pack_group = None if full_field_calc == 0 else BOTH
    pack_awards = awards_of(marked, pack_group)
    assert pack_awards and not {a.racer_id for a in pack_awards} & parent_ids
    assert pack_awards[0].racer_id in removed.ladders[pack][0]
    own = [
        a
        for a in speed_awards(counting, TROPHIES).awards
        if a.racer_id in parent_ids and a.group_id not in (None, BOTH)
    ]
    assert set(own) <= set(speed_awards(marked, TROPHIES).awards)

    for row in marked.rows:
        if row.racer.id in parent_ids and pack in row.ladders:
            assert dict(row.not_eligible) == {pack: NotEligible.PARTITION_NOT_LISTED}
            assert set(row.outcome) == set(row.ladders) - {pack}


def test_the_check_on_own_awards_sees_some():
    """The pack test checks that a Parents racer keeps every award they win
    outside the pack. Here some do win one."""
    won = 0
    for seed in range(30):
        event = partitioned(random_event(seed))
        counting = Standings(event)
        parents = leader(counting, counting.ladders[FULL_FIELD]).partition_id
        won += any(
            counting.racers[a.racer_id].partition_id == parents
            for a in speed_awards(counting, TROPHIES).awards
            if a.group_id in (RED, BLUE)
        )
    assert won


@pytest.mark.parametrize("seed", range(30))
def test_a_subgroup_list_places_its_awards_the_same_way(seed):
    event = partitioned(random_event(seed, use_subgroups=True))
    counting = Standings(event)
    subgroup = leader(counting, counting.award_ladder_class(RED)).subgroup_id
    first = leader(counting, counting.award_ladder_subgroup(subgroup))
    unit = AwardUnit(subgroup_id=subgroup)
    marked = Standings(listing(event, unit, first.partition_id))
    parent_ids = {r.id for r in event.racers if r.partition_id == first.partition_id}
    removed = Standings(without(event, parent_ids))

    assert marked.award_ladder_subgroup(subgroup) == removed.award_ladder_subgroup(
        subgroup
    )
    assert marked.award_ladder_class(RED) == counting.award_ladder_class(RED)
    subgroup_awards = awards_of(marked, RED, subgroup)
    assert subgroup_awards and not {a.racer_id for a in subgroup_awards} & parent_ids
    assert awards_of(marked, RED) == awards_of(counting, RED)


@pytest.mark.parametrize("seed", range(30))
def test_under_one_group_the_group_and_the_pack_agree_on_the_same_list(seed):
    """One group whose subgroups are the partitions, as the
    one-group rule makes it. Fastest in the group and fastest in the pack
    go to the same racer when both units list the same partitions."""
    event = partitioned(random_event(seed))
    event = replace(
        event,
        groups=tuple(g for g in event.groups if g.id == RED),
        subgroups=tuple(s for s in event.subgroups if s.group_id == RED),
        rounds=tuple(r for r in event.rounds if r.id == ROUND_RED),
        racers=tuple(r for r in event.racers if r.group_id == RED),
        results=tuple(r for r in event.results if r.round_id == ROUND_RED),
    )
    counting = Standings(event)
    parents = leader(counting, counting.ladders[FULL_FIELD]).partition_id
    kept = frozenset(all_partitions(event) - {parents})

    both = Standings(
        replace(event, partition_lists={PACK: kept, AwardUnit(group_id=RED): kept})
    )
    assert [a.racer_id for a in awards_of(both, None)] == [
        a.racer_id for a in awards_of(both, RED)
    ]
    pack_only = Standings(replace(event, partition_lists={PACK: kept}))
    assert (
        awards_of(pack_only, None)[0].racer_id != awards_of(pack_only, RED)[0].racer_id
    )


@pytest.mark.parametrize("seed", range(30))
def test_a_cut_reads_the_list_of_the_group_it_is_for(seed):
    """One rule decides an aggregate's awards and its final's
    cut, and the pack's list changes no cut."""
    event = partitioned(random_event(seed))
    counting = Standings(event)
    parents = leader(counting, counting.ladders[FULL_FIELD]).partition_id
    parent_ids = {r.id for r in event.racers if r.partition_id == parents}

    def cuts(standings):
        return [
            finalists.for_aggregate(standings, BOTH, bucketed=False, limit=4),
            finalists.follow_on(standings, RED, bucketed=False, limit=3),
            finalists.follow_on(standings, BLUE, bucketed=True, limit=2),
        ]

    aggregate = Standings(listing(event, AwardUnit(group_id=BOTH), parents))
    cut = finalists.for_aggregate(aggregate, BOTH, bucketed=False, limit=4)
    assert not {f.racer_id for f in cut.finalists} & parent_ids
    assert cut == finalists.for_aggregate(
        Standings(without(event, parent_ids)), BOTH, bucketed=False, limit=4
    )
    assert not {a.racer_id for a in awards_of(aggregate, BOTH)} & parent_ids
    assert cuts(aggregate)[1:] == cuts(counting)[1:]

    assert cuts(Standings(listing(event, PACK, parents))) == cuts(counting)
    group = Standings(listing(event, AwardUnit(group_id=RED), parents))
    assert cuts(group)[0] == cuts(counting)[0], "the aggregate's cut reads its own list"


def test_with_every_list_at_all_partitions_nothing_changes():
    event = partitioned(random_event(1))
    everyone = frozenset(all_partitions(event))
    listed = Standings(
        replace(
            event,
            partition_lists={PACK: everyone, AwardUnit(group_id=RED): everyone},
        )
    )
    counting = Standings(event)
    assert listed.ladders == counting.ladders
    assert speed_awards(listed, TROPHIES) == speed_awards(counting, TROPHIES)
