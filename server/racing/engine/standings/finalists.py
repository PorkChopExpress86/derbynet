"""Choosing the racers of a new round from the standings:
`calculate_roster_from_keys` and the three builders that reach it, in
`website/inc/roster.inc`.

A cut walks the standings rows best first and puts each row in the bucket
of the first ladder it has a place in among the bucket keys. A bucket takes
a row while it holds fewer than `limit` racers, or while the row's score
equals the last score it took, so a tie at the cut joins the round and a
top 4 can hold 5 racers.

A cut skips every racer who cannot win the speed awards of the group the
round is for, and takes the next one (D34). Racers with an award setting
have no place in any ladder already; a cut for the `full-field-calc`
aggregate also skips racers whose partition does not count for
all-partition awards, because their places in the groups it draws from
still stand.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from racing.engine.standings.oracle import Standings


@dataclass(frozen=True)
class Finalist:
    racer_id: int
    score: float


@dataclass(frozen=True)
class Cut:
    """The finalists, bucket by bucket and best first within a bucket, and a
    concern for each bucket that holds fewer than `limit` racers."""

    finalists: tuple[Finalist, ...]
    concerns: tuple[str, ...]


def from_keys(
    standings: Standings,
    keys: Sequence[str | None],
    names: Sequence[str],
    *,
    bucketed: bool,
    limit: int,
    for_group: int,
) -> Cut:
    """`calculate_roster_from_keys()`. Without `bucketed`, every key fills
    one bucket. `for_group` is the group the new round belongs to."""
    all_partitions = for_group == standings.settings.pack_group_id
    buckets: list[list[Finalist]] = [[] for _ in keys]
    for row in standings.rows:
        if row.racer.awards.why_not(all_partitions=all_partitions) is not None:
            continue
        for key in row.outcome:
            if key not in keys:
                continue
            bucket = buckets[keys.index(key) if bucketed else 0]
            score = row.summary.score
            if len(bucket) < limit or (bucket and bucket[-1].score >= score):
                bucket.append(Finalist(row.racer.id, score))
            break

    concerns = []
    for bucket, name in zip(buckets, names, strict=True):
        if len(bucket) < limit:
            if not bucket:
                concerns.append(f"There are no eligible {name} racer(s).")
            else:
                concerns.append(
                    f"There are only {len(bucket)} eligible {name} racer(s)."
                )
    return Cut(
        finalists=tuple(f for bucket in buckets for f in bucket),
        concerns=tuple(concerns),
    )


def from_groups(
    standings: Standings,
    group_ids: Sequence[int],
    *,
    bucketed: bool,
    limit: int,
    for_group: int,
) -> Cut:
    """`calculate_roster_from_classes()`: one bucket per group's standings."""
    return from_keys(
        standings,
        [standings.class_key(g) for g in group_ids],
        [standings.groups[g].name for g in group_ids],
        bucketed=bucketed,
        limit=limit,
        for_group=for_group,
    )


def from_subgroups(
    standings: Standings,
    subgroup_ids: Sequence[int],
    *,
    bucketed: bool,
    limit: int,
    for_group: int,
) -> Cut:
    """`calculate_roster_from_subgroups()`: one bucket per subgroup's
    standings in its group's highest finished round."""
    return from_keys(
        standings,
        [standings.rank_key(s) for s in subgroup_ids],
        [standings.subgroups[s].name for s in subgroup_ids],
        bucketed=bucketed,
        limit=limit,
        for_group=for_group,
    )


def follow_on(
    standings: Standings, group_id: int, *, bucketed: bool, limit: int
) -> Cut:
    """`calculate_roster_follow_on_round()`: the next round of a group, from
    its standings, or with `bucketed` from each of its subgroups' in id
    order."""
    if not bucketed:
        return from_groups(
            standings, [group_id], bucketed=False, limit=limit, for_group=group_id
        )
    subgroup_ids = sorted(
        s.id for s in standings.event.subgroups if s.group_id == group_id
    )
    return from_subgroups(
        standings, subgroup_ids, bucketed=True, limit=limit, for_group=group_id
    )


def for_aggregate(
    standings: Standings, group_id: int, *, bucketed: bool, limit: int
) -> Cut:
    """Round 1 of an aggregate group, as `roster.new` and the playlist build
    it: from its subgroups when it draws from subgroups, else from its
    groups."""
    group = standings.groups[group_id]
    if group.subgroup_ids:
        return from_subgroups(
            standings,
            group.subgroup_ids,
            bucketed=bucketed,
            limit=limit,
            for_group=group_id,
        )
    return from_groups(
        standings,
        group.constituents,
        bucketed=bucketed,
        limit=limit,
        for_group=group_id,
    )
