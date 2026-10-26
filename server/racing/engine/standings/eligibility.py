"""Who can win a speed award: the one rule the standings ladders, the speed
awards and a final's cut share (owner decisions D33 and D34).

PHP has one setting, `exclude`, and leaves an excluded racer out of the
standings query, so the racer takes no place, wins no speed award, and is
never a finalist. Django keeps that racer's rows, marked, and the rule
below decides which ladders they take no place in.
"""

from dataclasses import dataclass
from enum import StrEnum


class NotEligible(StrEnum):
    """Why a racer's row takes no place in a ladder."""

    ANY_AWARD = "not eligible for any award"
    SPEED_AWARDS = "not eligible for speed awards"
    ALL_PARTITION_AWARDS = "partition does not count for all-partition awards"


@dataclass(frozen=True)
class AwardSettings:
    """A racer's two award settings, and whether the racer's partition
    counts for all-partition awards. `not_eligible_for_any_award` is the
    legacy `exclude`."""

    not_eligible_for_any_award: bool = False
    not_eligible_for_speed_awards: bool = False
    partition_counts_for_all_partition_awards: bool = True

    def why_not(self, *, all_partitions: bool) -> NotEligible | None:
        """Why the racer cannot win a speed award of this scope, or None if
        the racer can. An all-partition award is one of the pack's: from
        the full field, or from the `full-field-calc` aggregate group."""
        if self.not_eligible_for_any_award:
            return NotEligible.ANY_AWARD
        if self.not_eligible_for_speed_awards:
            return NotEligible.SPEED_AWARDS
        if all_partitions and not self.partition_counts_for_all_partition_awards:
            return NotEligible.ALL_PARTITION_AWARDS
        return None


ELIGIBLE = AwardSettings()
