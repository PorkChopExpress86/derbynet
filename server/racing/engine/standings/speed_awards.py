"""Speed awards, the awards placed by time: `add_speed_awards` and
`add_speed_group` in `website/inc/awards.inc`, and the speed branch of
`website/ajax/query.award.current.inc`.

The pack's awards come first, from the full field or from the
`full-field-calc` aggregate group. Then each other aggregate group's, in
topological order, then each group's and its subgroups', in group and
subgroup order. With `one-trophy-per`, a racer who already holds a speed
award is passed over and the award goes to the next racer.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from racing.engine.groups.aggregates import Aggregate, topological_order
from racing.engine.standings.oracle import Standings
from racing.engine.standings.ordinals import nth_fastest
from racing.engine.standings.tracker import Ladder

SPEED_TROPHY_TYPE_ID = 5
SPEED_TROPHY_TYPE = "Speed Trophy"


class Category(StrEnum):
    SUPERGROUP = "supergroup"
    GROUP = "group"
    SUBGROUP = "subgroup"


@dataclass(frozen=True)
class TrophySettings:
    """`n-pack-trophies`, `n-den-trophies`, `n-rank-trophies` and
    `one-trophy-per`. A group's or subgroup's own `n_trophies` overrides
    the count for it."""

    n_pack: int = 3
    n_group: int = 3
    n_subgroup: int = 0
    one_trophy_per: bool = False


@dataclass(frozen=True)
class SpeedAward:
    """One speed award. `group_id` is set for a group's or subgroup's
    award, and for the pack's awards when an aggregate group decides them;
    `subgroup_id` only for a subgroup's."""

    key: str
    name: str
    place: int
    sort: int
    racer_id: int
    group_id: int | None
    subgroup_id: int | None

    @property
    def category(self) -> Category:
        if self.subgroup_id is not None:
            return Category.SUBGROUP
        if self.group_id is not None:
            return Category.GROUP
        return Category.SUPERGROUP


@dataclass(frozen=True)
class SpeedAwards:
    """The awards in the order PHP makes them, and `reserved_overall`, the
    sort bias that places every other award after them."""

    awards: tuple[SpeedAward, ...]
    reserved_overall: int


class _Awarding:
    def __init__(self, standings: Standings, trophies: TrophySettings):
        self.standings = standings
        self.trophies = trophies
        self.awards: list[SpeedAward] = []
        self.holders: list[int] = []

    def fresh_group(self) -> None:
        if not self.trophies.one_trophy_per:
            self.holders = []

    def award(
        self,
        n: int,
        bias: int,
        group_id: int | None,
        subgroup_id: int | None,
        label: str,
        ladder: Ladder,
        all_partitions: bool = False,
    ) -> None:
        """`add_speed_group()`. A missing rung moves the place on, an empty
        rung after a tie does not, and a rung whose racers all hold an
        award passes the place to the next rung.

        PHP indexes the remaining racers of a rung by their old positions
        (Q7, C9), so when the first racer of a tie already holds an award
        it awards nobody and skips the next racer. Here the remaining
        racers get the place in rung order.
        """
        racers = self.standings.racers
        place = 0
        rung_index = 0
        while place < n:
            if rung_index >= len(ladder):
                place += 1
                rung_index += 1
                continue
            rung = ladder[rung_index]
            rung_index += 1
            recipients = [
                r
                for r in rung
                if r not in self.holders
                and racers[r].awards.why_not(all_partitions=all_partitions) is None
            ]
            if not recipients:
                continue
            name = nth_fastest(1 + place, label)
            if len(recipients) > 1:
                name += " (Tie)"
            for i, racer_id in enumerate(recipients):
                letter = chr(ord("a") + i) if len(rung) > 1 else ""
                key = f"speed-{1 + place}{letter}"
                if group_id is not None:
                    key += f"-{group_id}"
                if subgroup_id is not None:
                    key += f"-{subgroup_id}"
                self.awards.append(
                    SpeedAward(
                        key=key,
                        name=name,
                        place=1 + place,
                        sort=bias + place,
                        racer_id=racer_id,
                        group_id=group_id,
                        subgroup_id=subgroup_id,
                    )
                )
                self.holders.append(racer_id)
            place += 1


def speed_awards(standings: Standings, trophies: TrophySettings) -> SpeedAwards:
    """`add_speed_awards()`."""
    settings = standings.settings
    pack_id = settings.pack_group_id
    groups = standings.group_order
    subgroups = standings.subgroup_order

    def own(n_trophies: int | None, default: int) -> int:
        return default if n_trophies is None else n_trophies

    max_group = max([trophies.n_group, *(own(g.n_trophies, -1) for g in groups)])
    max_subgroup = max(
        [trophies.n_subgroup, *(own(s.n_trophies, -1) for s in subgroups)]
    )
    per_group_counts: dict[int, int] = {}
    for subgroup in standings.event.subgroups:
        per_group_counts[subgroup.group_id] = (
            per_group_counts.get(subgroup.group_id, 0) + 1
        )
    max_subgroups_per_group = max(per_group_counts.values(), default=0)
    reserved_per_subgroup = max_subgroup
    reserved_per_group = max_group + reserved_per_subgroup * max_subgroups_per_group
    reserved_overall = trophies.n_pack + reserved_per_group * len(groups)

    awarding = _Awarding(standings, trophies)
    label = settings.supergroup_label
    if standings.has_full_field_standings:
        awarding.award(
            trophies.n_pack,
            0,
            None,
            None,
            label,
            standings.award_ladder_full_field(),
            all_partitions=True,
        )
    elif pack_id is not None:
        awarding.award(
            trophies.n_pack,
            0,
            pack_id,
            None,
            label,
            standings.award_ladder_class(pack_id),
            all_partitions=True,
        )

    ordinal_group = 0
    for aggregate in _aggregate_order(standings):
        ordinal_group += 1
        if aggregate.id == pack_id:
            continue
        awarding.fresh_group()
        awarding.award(
            own(aggregate.n_trophies, trophies.n_group),
            ordinal_group * reserved_per_group,
            aggregate.id,
            None,
            aggregate.name,
            standings.award_ladder_class(aggregate.id),
        )

    group_id = None
    subgroup_within_group = 0
    for subgroup in subgroups:
        if subgroup.group_id != group_id:
            group_id = subgroup.group_id
            group = standings.groups[group_id]
            ordinal_group += 1
            bias_for_group = ordinal_group * reserved_per_group
            subgroup_within_group = 0
            awarding.fresh_group()
            awarding.award(
                own(group.n_trophies, trophies.n_group),
                bias_for_group,
                group_id,
                None,
                group.name,
                standings.award_ladder_class(group_id),
            )
        subgroup_within_group += 1
        # PHP biases a subgroup by `n-den-trophies`, not by the group's own
        # count. It changes only the sort order, so it stays.
        bias = (
            bias_for_group
            + trophies.n_group
            + subgroup_within_group * reserved_per_subgroup
        )
        awarding.fresh_group()
        awarding.award(
            own(subgroup.n_trophies, trophies.n_subgroup),
            bias,
            group_id,
            subgroup.id,
            subgroup.name,
            standings.award_ladder_subgroup(subgroup.id),
        )

    return SpeedAwards(tuple(awarding.awards), reserved_overall)


def _aggregate_order(standings: Standings):
    """`topological_sort(aggregate_classes())`: the aggregate groups by
    sort order, then every aggregate before the groups it draws from."""
    aggregates = sorted(
        (g for g in standings.event.groups if g.constituents),
        key=lambda g: (g.sort_order, g.id),
    )
    order = topological_order([Aggregate(g.id, g.constituents) for g in aggregates])
    return [standings.groups[a.group_id] for a in order]


@dataclass(frozen=True)
class CurrentAward:
    """The speed award a `current_award_key` names: its key as presented,
    its name, and the racer."""

    key: str
    name: str
    racer_id: int


def current_speed_award(
    key: str, standings: Standings, awards: Sequence[SpeedAward]
) -> CurrentAward | None:
    """The speed branch of `query.award.current`: `speed-<place>`,
    `speed-<place>-<group>` or `speed-<place>-<group>-<subgroup>`, where
    `<place>` may end in a tie letter, `1b` for the second award of first
    place. A group key for the `full-field-calc` aggregate is presented as
    the pack's award, under the key `speed-<place>`. None when the key is
    not a speed key or names no award."""
    parts = key.split("-")
    if len(parts) < 2 or parts[0] != "speed":
        return None
    place_text = parts[1]
    index = 0
    if place_text and "a" <= place_text[-1] <= "z":
        index = ord(place_text[-1]) - ord("a")
        place_text = place_text[:-1]
    if not place_text.isdigit():
        return None
    place = int(place_text)
    label = standings.settings.supergroup_label

    group_id = subgroup_id = None
    if len(parts) == 4:
        subgroup_id = _int(parts[3])
        subgroup = standings.subgroups.get(subgroup_id)
        name = nth_fastest(place, subgroup.name if subgroup else "")
    elif len(parts) == 3:
        group_id = _int(parts[2])
        if group_id != standings.settings.full_field_calc:
            group = standings.groups.get(group_id)
            name = nth_fastest(place, group.name if group else "")
        else:
            key = f"speed-{place}"
            name = nth_fastest(place, label)
    else:
        name = nth_fastest(place, label)

    for award in awards:
        if award.place != place:
            continue
        if subgroup_id is not None or len(parts) == 4:
            match = award.subgroup_id == subgroup_id
        elif group_id is not None or len(parts) == 3:
            match = award.group_id == group_id
        else:
            match = True
        if match:
            if index == 0:
                return CurrentAward(key, name, award.racer_id)
            index -= 1
    return None


def _int(text: str) -> int | str | None:
    """The id in a key, compared with an award's as PHP's `==` compares
    them: empty text equals a null id, and other text that is not a number
    equals no id, so it stays text."""
    return int(text) if text.isdigit() else (text or None)
