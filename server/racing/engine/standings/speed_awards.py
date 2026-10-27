"""Speed awards, the awards placed by time: `add_speed_awards` and
`add_speed_group` in `website/inc/awards.inc`, and the speed branch of
`website/ajax/query.award.current.inc`.

The pack's awards come first, from the full field or from the
`full-field-calc` aggregate group. Then each other aggregate group's, in
topological order, then each group's and its subgroups', in group and
subgroup order. With `one-trophy-per`, a racer who already holds a speed
award is passed over and the award goes to the next racer. Each unit's
awards go only to racers its partition list holds (D35).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from racing.engine.groups.aggregates import Aggregate, topological_order
from racing.engine.phpcompat import php_equals, php_intval, php_numeric
from racing.engine.standings.oracle import Standings
from racing.engine.standings.ordinals import nth_fastest
from racing.engine.standings.tracker import Ladder


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
    ) -> None:
        """`add_speed_group()`. A missing rung moves the place on, an empty
        rung after a tie does not, and a rung whose racers all hold an
        award passes the place to the next rung. The ladder holds only the
        racers who can win the unit's awards: the standings decide that for
        each award ladder (D33, D35).

        PHP indexes the remaining racers of a rung by their old positions
        (Q7, C9), so when the first racer of a tie already holds an award
        it awards nobody and skips the next racer. Here the remaining
        racers get the place in rung order.
        """
        place = 0
        rung_index = 0
        while place < n:
            if rung_index >= len(ladder):
                place += 1
                rung_index += 1
                continue
            rung = ladder[rung_index]
            rung_index += 1
            recipients = [r for r in rung if r not in self.holders]
            if not recipients:
                continue
            name = nth_fastest(1 + place, label)
            if len(recipients) > 1:
                name += " (Tie)"
            for i, racer_id in enumerate(recipients):
                letter = tie_letters(i) if len(rung) > 1 else ""
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


def tie_letters(i: int) -> str:
    """The letters of the `i`-th award of a tied place: `a` to `z`, then
    `aa`, `ab`, and on. PHP writes `chr(ord('a') + $i)`, which runs past
    `z` into punctuation and, from the 32nd racer, bytes that are not
    UTF-8 (Q22)."""
    letters = ""
    i += 1
    while i:
        i, rest = divmod(i - 1, 26)
        letters = chr(ord("a") + rest) + letters
    return letters


def tie_index(letters: str) -> int:
    """The inverse of `tie_letters`."""
    i = 0
    for letter in letters:
        i = i * 26 + ord(letter) - ord("a") + 1
    return i - 1


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
        )
    elif pack_id is not None:
        awarding.award(
            trophies.n_pack,
            0,
            pack_id,
            None,
            label,
            standings.award_ladder_class(pack_id),
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
    not a speed key or names no award.

    The key is read as PHP reads it. A place without a tie letter stays
    text and is compared with PHP 8's `==`, so `speed-02` and `speed-2.0`
    find second place, and an id compares the same way, so `speed-2- 1`
    finds group 1's award. A name looks its group or subgroup up by the
    id's number, as SQLite compares text with an integer column. A place
    PHP cannot do arithmetic on, `speed-x1`, makes PHP answer 500; here it
    names no award."""
    parts = key.split("-")
    if len(parts) < 2 or parts[0] != "speed":
        return None
    place: int | str = parts[1]
    index = 0
    letters = re.search(r"[a-z]+\Z", place)
    if letters:
        # PHP reads one letter; two or more name a tie past "z" (Q22).
        tie = letters[0] if len(letters[0]) > 1 else letters[0][-1]
        index = tie_index(tie)
        place = php_intval(place[: len(place) - len(tie)])
    label = standings.settings.supergroup_label

    def named(table, id_text: str) -> str:
        number = php_numeric(id_text)
        found = table.get(number) if number is not None else None
        return nth_fastest(place, found.name if found else "")

    def is_place(award: SpeedAward) -> bool:
        if isinstance(place, int):
            return award.place == place
        return php_equals(award.place, place)

    try:
        if len(parts) == 4:
            name = named(standings.subgroups, parts[3])
        elif len(parts) == 3:
            if php_numeric(parts[2]) != standings.settings.full_field_calc:
                name = named(standings.groups, parts[2])
            else:
                key = f"speed-{place}"
                name = nth_fastest(place, label)
        else:
            name = nth_fastest(place, label)
    except TypeError:
        return None

    for award in awards:
        if not is_place(award):
            continue
        if len(parts) == 4:
            match = php_equals(award.subgroup_id, parts[3])
        elif len(parts) == 3:
            match = php_equals(award.group_id, parts[2])
        else:
            match = True
        if match:
            if index == 0:
                return CurrentAward(key, name, award.racer_id)
            index -= 1
    return None
