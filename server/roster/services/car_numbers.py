"""Car numbering by partition, from `website/inc/car-numbering.inc` (#29).

The `car-numbering` setting is `<multiplier>+<smallest>`, `100+101` by
default. With a multiplier, partition `n` in sort order numbers its cars from
`smallest + (n - 1) * multiplier`, so the first den gets 101, 102, ... and the
second 201, 202, .... With multiplier 0 every partition shares one sequence
from `smallest`.

These read only. The callers that assign a number hold the `RaceState` lock.
"""

from dataclasses import dataclass

from core.models import RaceSettings
from racing.engine.phpcompat import php_intval
from roster.models import Partition, Racer


@dataclass(frozen=True)
class Numbering:
    multiplier: int
    smallest: int

    def lower_bound(self, partition: Partition) -> int:
        return self.smallest + (partition.sort_order - 1) * self.multiplier

    def upper_bound(self, partition: Partition) -> int:
        return self.smallest + partition.sort_order * self.multiplier


def numbering(event_id) -> Numbering:
    """`read_car_numbering_values()`, each part as `intval()` reads it. A
    setting with no `+` has no smallest number, which PHP reads as 0."""
    setting = RaceSettings.objects.get(event_id=event_id).car_numbering
    parts = setting.split("+")
    multiplier = php_intval(parts[0])
    smallest = php_intval(parts[1]) if len(parts) > 1 else 0
    return Numbering(multiplier, smallest)


def _taken(event_id) -> set[int]:
    return set(
        Racer.objects.filter(event_id=event_id).values_list("car_number", flat=True)
    )


def _ends_of_runs(taken: set[int]) -> list[int]:
    """The taken numbers whose successor is free, in order."""
    return sorted(n for n in taken if n + 1 not in taken)


def next_car_numbers(event_id) -> list[dict]:
    """`next_car_numbers()`: `{partitionid, next_carnumber}` for each
    partition in sort order, as the check-in page offers them.

    With a multiplier the next number is one past the first run of taken
    numbers that starts at or just below the partition's lower bound and
    ends below its upper bound, else the lower bound. Like PHP, a gap inside
    the range is skipped once a later run ends in it.
    """
    rule = numbering(event_id)
    ends = _ends_of_runs(_taken(event_id))
    rows = []
    for partition in Partition.objects.filter(event_id=event_id).order_by(
        "sort_order", "id"
    ):
        if rule.multiplier == 0:
            low, candidates = rule.smallest, [n for n in ends if n >= rule.smallest]
        else:
            low = rule.lower_bound(partition)
            high = rule.upper_bound(partition)
            candidates = [n for n in ends if low - 1 <= n < high]
        rows.append(
            {
                "partitionid": partition.pk,
                "next_carnumber": 1 + candidates[0] if candidates else low,
            }
        )
    return rows


def next_car_number_for_partition(partition: Partition) -> int:
    """`next_car_number_for_partition()`: the partition's lower bound if it
    is free, else one past the end of the first run of taken numbers from
    there. PHP returns null when every number up to the upper bound is
    taken; this returns one past the end of the run instead, past the
    bound, so a racer always gets a number."""
    rule = numbering(partition.event_id)
    taken = _taken(partition.event_id)
    low = rule.lower_bound(partition)
    if low not in taken:
        return low
    ends = [n for n in _ends_of_runs(taken) if n >= low]
    if rule.multiplier != 0:
        inside = [n for n in ends if n < rule.upper_bound(partition)]
        ends = inside or ends
    return 1 + ends[0]


def base_car_number_by_partition(event_id) -> int | dict[int, int]:
    """`base_car_number_by_partition()`: with multiplier 0 the first number
    for everyone, otherwise each partition's lower bound by partition id."""
    rule = numbering(event_id)
    if rule.multiplier == 0:
        return rule.smallest
    return {
        p.pk: rule.lower_bound(p) for p in Partition.objects.filter(event_id=event_id)
    }
