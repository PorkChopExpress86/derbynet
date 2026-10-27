"""Who can win a speed award: the one rule the standings ladders, the speed
awards and a final's cut share (owner decisions D33 and D35).

PHP has one setting, `exclude`, and leaves an excluded racer out of the
standings query, so the racer takes no place, wins no speed award, and is
never a finalist. Django keeps that racer's rows, marked, and the rule
below decides which ladders they take no place in.

Every award unit, the pack, a group (an aggregate included) or a subgroup,
lists the partitions its speed awards can go to (D35). A unit with no list
in `partition_lists` goes to every partition.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class NotEligible(StrEnum):
    ANY_AWARD = "not eligible for any award"
    SPEED_AWARDS = "not eligible for speed awards"
    PARTITION_NOT_LISTED = "partition is not in the award's list"


@dataclass(frozen=True)
class AwardSettings:
    not_eligible_for_any_award: bool = False
    not_eligible_for_speed_awards: bool = False


ELIGIBLE = AwardSettings()


@dataclass(frozen=True)
class AwardUnit:
    """What carries a speed-award count and a partition list: the pack,
    with neither id, a group, or a subgroup."""

    group_id: int | None = None
    subgroup_id: int | None = None


PACK = AwardUnit()

PartitionLists = Mapping[AwardUnit, frozenset[int]]
"""The narrowed lists only, by unit."""


def why_not(
    awards: AwardSettings,
    partition_id: int | None,
    unit: AwardUnit | None,
    partition_lists: PartitionLists,
) -> NotEligible | None:
    """Why a racer cannot win the speed awards of `unit`, or None if the
    racer can. With no unit, only the racer's award settings count, as in a
    ladder that no award reads."""
    if awards.not_eligible_for_any_award:
        return NotEligible.ANY_AWARD
    if awards.not_eligible_for_speed_awards:
        return NotEligible.SPEED_AWARDS
    listed = partition_lists.get(unit) if unit is not None else None
    if listed is not None and partition_id not in listed:
        return NotEligible.PARTITION_NOT_LISTED
    return None
