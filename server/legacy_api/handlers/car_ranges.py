"""The car number ranges of a partition (#123, D48). Django only: PHP has no
such actions.

The race coordinator sets them (`SET_UP`). Check-in then suggests numbers from
them, and `range-full` (racers.py) asks for another.
"""

from accounts.permissions import Perm
from legacy_api.registry import LegacyContext, action, query
from roster.models import CarRange, Partition
from roster.services import car_numbers
from roster.services.car_numbers import RangeFailure

ADDED = ("D48: a Django-only endpoint; PHP has no car number ranges.",)
MISSING_ARGS = ("missing-args", "Unrecognized argument signature")
NO_PARTITION = ("no-such-partition", "No such partition")


def _whole_number(ctx: LegacyContext, key: str) -> int | None:
    value = ctx.args.get(key)
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def _put_ranges(ctx: LegacyContext, partition_id: int) -> None:
    ctx.put("partitionid", partition_id)
    ctx.put(
        "ranges",
        car_numbers.range_rows(
            partition_id, car_numbers.ranges_by_partition(ctx.event.id)
        ),
    )


@query("partition.range-list", permission=Perm.SET_UP, divergences=ADDED)
def range_list(ctx: LegacyContext) -> None:
    ctx.put("partition-ranges", car_numbers.list_ranges(ctx.event.id))


@action("partition.range-add", permission=Perm.SET_UP, divergences=ADDED)
def range_add(ctx: LegacyContext) -> None:
    first, last = _whole_number(ctx, "first"), _whole_number(ctx, "last")
    if first is None or last is None or "partitionid" not in ctx.args:
        ctx.failure(*MISSING_ARGS)
        return
    partition = ctx.resolve(Partition, ctx.args["partitionid"])
    if partition is None:
        ctx.failure(*NO_PARTITION)
        return
    try:
        car_numbers.add_range(partition, first, last)
    except RangeFailure as failure:
        ctx.failure(str(failure.code), failure.description)
        return
    _put_ranges(ctx, partition.pk)
    ctx.success()


@action("partition.range-remove", permission=Perm.SET_UP, divergences=ADDED)
def range_remove(ctx: LegacyContext) -> None:
    car_range = ctx.resolve(CarRange, ctx.args.get("rangeid"))
    if car_range is None:
        ctx.failure("no-such-range", "No such car number range")
        return
    partition_id = car_range.partition_id
    car_numbers.remove_range(car_range)
    _put_ranges(ctx, partition_id)
    ctx.success()
