"""Car numbering by partition, from `website/inc/car-numbering.inc` (#29).

The `car-numbering` setting is `<multiplier>+<smallest>`, `100+101` by
default. With a multiplier, partition `n` in sort order numbers its cars from
`smallest + (n - 1) * multiplier`, so the first den gets 101, 102, ... and the
second 201, 202, .... With multiplier 0 every partition shares one sequence
from `smallest`.

A partition can instead hold car number ranges that the race coordinator sets
(D48, Django only). Check-in then suggests the first free number of the first
range with room, and a partition with no range keeps the rule above.

The suggestions read only. The callers that assign a number hold the
`RaceState` lock, and so do the range writers.
"""

from dataclasses import dataclass
from enum import StrEnum

from django.db import transaction

from core.models import RaceSettings
from core.services import lock_race_state
from racing.engine.phpcompat import php_intval
from roster.models import CarRange, Partition, Racer

# `Racer.car_number` is a 32-bit column.
LARGEST_NUMBER = 2**31 - 1


class RangeFailure(Exception):
    class Code(StrEnum):
        BAD_RANGE = "bad-range"
        OVERLAP = "range-overlap"

    def __init__(self, code: Code, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


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


def ranges_by_partition(event_id) -> dict[int, list[CarRange]]:
    """Each partition's ranges in order, by partition id."""
    by_partition: dict[int, list[CarRange]] = {}
    for car_range in CarRange.objects.filter(event_id=event_id).order_by(
        "sort_order", "id"
    ):
        by_partition.setdefault(car_range.partition_id, []).append(car_range)
    return by_partition


def _first_free(ranges: list[CarRange], taken: set[int]) -> int | None:
    """The lowest number of the first range with room. None when every range
    is full."""
    for car_range in ranges:
        number = car_range.first
        while number <= car_range.last and number in taken:
            number += 1
        if number <= car_range.last:
            return number
    return None


def has_ranges(partition: Partition) -> bool:
    return CarRange.objects.filter(partition=partition).exists()


def outside_ranges(partition: Partition, number: int) -> bool:
    """True when the partition has ranges and none holds `number`. A
    partition with no range accepts any number, as PHP does."""
    ranges = CarRange.objects.filter(partition=partition)
    return (
        ranges.exists()
        and not ranges.filter(first__lte=number, last__gte=number).exists()
    )


def outside_warning(partition: Partition, number: int) -> str | None:
    """The warning for a number outside the partition's ranges, else None."""
    if outside_ranges(partition, number):
        return (
            f"Car number {number} is outside the car number ranges of {partition.name}"
        )
    return None


def range_rows(partition_id, ranges: dict[int, list[CarRange]]) -> list[dict]:
    return [
        {"rangeid": r.pk, "first": r.first, "last": r.last}
        for r in ranges.get(partition_id, [])
    ]


def list_ranges(event_id) -> list[dict]:
    """`{partitionid, ranges}` for each partition that has ranges, in
    partition order."""
    ranges = ranges_by_partition(event_id)
    return [
        {"partitionid": p.pk, "ranges": range_rows(p.pk, ranges)}
        for p in Partition.objects.filter(event_id=event_id).order_by(
            "sort_order", "id"
        )
        if p.pk in ranges
    ]


def add_range(partition: Partition, first: int, last: int) -> CarRange:
    """Append a range to the partition. A range whose last number is below
    its first, or that shares a number with a range of another partition, is
    refused and nothing changes."""
    event_id = partition.event_id
    with transaction.atomic():
        lock_race_state(event_id)
        if not 0 <= first <= last <= LARGEST_NUMBER:
            raise RangeFailure(
                RangeFailure.Code.BAD_RANGE,
                f"{first} to {last} is not a range of whole numbers, "
                "with the last number at least the first.",
            )
        clash = (
            CarRange.objects.filter(event_id=event_id, first__lte=last, last__gte=first)
            .exclude(partition=partition)
            .select_related("partition")
            .first()
        )
        if clash:
            raise RangeFailure(
                RangeFailure.Code.OVERLAP,
                f"{first} to {last} overlaps {clash.first} to {clash.last}, "
                f"which belongs to {clash.partition.name}.",
            )
        last_order = (
            CarRange.objects.filter(partition=partition)
            .order_by("-sort_order")
            .values_list("sort_order", flat=True)
            .first()
        )
        return CarRange.objects.create(
            event_id=event_id,
            partition=partition,
            first=first,
            last=last,
            sort_order=(last_order or 0) + 1,
        )


def remove_range(car_range: CarRange) -> None:
    with transaction.atomic():
        lock_race_state(car_range.event_id)
        car_range.delete()


def next_car_numbers(event_id) -> list[dict]:
    """`next_car_numbers()`: `{partitionid, next_carnumber}` for each
    partition in sort order, as the check-in page offers them.

    With a multiplier the next number is one past the first run of taken
    numbers that starts at or just below the partition's lower bound and
    ends below its upper bound, else the lower bound. Like PHP, a gap inside
    the range is skipped once a later run ends in it.
    """
    rule = numbering(event_id)
    taken = _taken(event_id)
    ends = _ends_of_runs(taken)
    ranges = ranges_by_partition(event_id)
    rows = []
    for partition in Partition.objects.filter(event_id=event_id).order_by(
        "sort_order", "id"
    ):
        if partition.pk in ranges:
            # None when every range is full: the page asks for another.
            rows.append(
                {
                    "partitionid": partition.pk,
                    "next_carnumber": _first_free(ranges[partition.pk], taken),
                }
            )
            continue
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


def next_car_number_for_partition(partition: Partition) -> int | None:
    """`next_car_number_for_partition()`: the partition's lower bound if it
    is free, else one past the end of the first run of taken numbers from
    there that ends below the upper bound. None when every number up to the
    upper bound is taken, as PHP's query finds none. A partition with ranges
    takes the first free number of the first range with room instead."""
    taken = _taken(partition.event_id)
    ranges = ranges_by_partition(partition.event_id).get(partition.pk)
    if ranges:
        return _first_free(ranges, taken)
    rule = numbering(partition.event_id)
    low = rule.lower_bound(partition)
    if low not in taken:
        return low
    ends = [n for n in _ends_of_runs(taken) if n >= low]
    if rule.multiplier != 0:
        ends = [n for n in ends if n < rule.upper_bound(partition)]
    return 1 + ends[0] if ends else None


def base_car_number_by_partition(event_id) -> int | dict[int, int]:
    """`base_car_number_by_partition()`: with multiplier 0 the first number
    for everyone, otherwise each partition's lower bound by partition id."""
    rule = numbering(event_id)
    if rule.multiplier == 0:
        return rule.smallest
    return {
        p.pk: rule.lower_bound(p) for p in Partition.objects.filter(event_id=event_id)
    }
