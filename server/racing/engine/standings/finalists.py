"""Choosing the racers of a new round from the standings:
`calculate_roster_from_keys` and the three builders that reach it, in
`website/inc/roster.inc`.

A cut walks the standings rows best first and puts each row in the bucket
of the first ladder it has a place in among the bucket keys. A bucket takes
a row while it holds fewer than `limit` racers, or while the row's score
equals the last score it took, so a tie at the cut joins the round and a
top 4 can hold 5 racers.

A cut skips every racer who cannot win the speed awards of the group the
round is for, and takes the next one (D34): a racer with an award setting,
or whose partition is not in that group's list (D35). The pack's list
never changes a cut. A racer left out of a group's list has no place in
that group's award ladder, so a cut walks every ladder a row bears on, not
only those it has a place in, and the rule alone decides.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from racing.engine.standings.eligibility import AwardUnit
from racing.engine.standings.oracle import Standings, round_key, round_subgroup_key


@dataclass(frozen=True)
class Finalist:
    racer_id: int
    score: float


@dataclass(frozen=True)
class Bucket:
    name: str
    finalists: tuple[Finalist, ...]


@dataclass(frozen=True)
class Cut:
    """The finalists, bucket by bucket and best first within a bucket, and a
    concern for each bucket that holds fewer than `limit` racers."""

    buckets: tuple[Bucket, ...]
    limit: int

    @property
    def finalists(self) -> tuple[Finalist, ...]:
        return tuple(f for bucket in self.buckets for f in bucket.finalists)

    @property
    def concerns(self) -> tuple[str, ...]:
        concerns = []
        for bucket in self.buckets:
            n = len(bucket.finalists)
            if n >= self.limit:
                continue
            if n == 0:
                concerns.append(f"There are no eligible {bucket.name} racer(s).")
            else:
                concerns.append(f"There are only {n} eligible {bucket.name} racer(s).")
        return tuple(concerns)

    def unique(self) -> Cut:
        """Each racer in the first bucket that took them only. A racer can
        qualify from two of the groups an aggregate draws from, and PHP
        would enter the racer twice and count the racer in both buckets."""
        seen: set[int] = set()
        buckets = []
        for bucket in self.buckets:
            kept = tuple(f for f in bucket.finalists if f.racer_id not in seen)
            seen |= {f.racer_id for f in kept}
            buckets.append(Bucket(bucket.name, kept))
        return Cut(tuple(buckets), self.limit)


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
    unit = AwardUnit(group_id=for_group)
    buckets: list[list[Finalist]] = [[] for _ in keys]
    for row in standings.rows:
        if standings.why_not(row.racer, unit) is not None:
            continue
        for key in row.ladders:
            if key not in keys:
                continue
            bucket = buckets[keys.index(key) if bucketed else 0]
            score = row.summary.score
            # PHP reads an empty bucket's last score as null, and null >= 0.0
            # holds, so even a top 0 takes a racer who scores 0.0.
            tied = bucket[-1].score >= score if bucket else score == 0
            if len(bucket) < limit or tied:
                bucket.append(Finalist(row.racer.id, score))
            break

    return Cut(
        tuple(
            Bucket(name, tuple(bucket))
            for bucket, name in zip(buckets, names, strict=True)
        ),
        limit,
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


def from_round(
    standings: Standings,
    round_id: int,
    group_id: int,
    *,
    bucketed: bool,
    limit: int,
) -> Cut:
    """The cut `follow_on` takes for the round after `round_id`, read from
    that round's ladders even once a later round has finished, so the
    finalist suggestions (D32) compare the final with the standings of the
    round that fed it, never with the final's own times."""
    if not bucketed:
        return from_keys(
            standings,
            [round_key(round_id)],
            [standings.groups[group_id].name],
            bucketed=False,
            limit=limit,
            for_group=group_id,
        )
    subgroup_ids = sorted(
        s.id for s in standings.event.subgroups if s.group_id == group_id
    )
    return from_keys(
        standings,
        [round_subgroup_key(round_id, s) for s in subgroup_ids],
        [standings.subgroups[s].name for s in subgroup_ids],
        bucketed=True,
        limit=limit,
        for_group=group_id,
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
