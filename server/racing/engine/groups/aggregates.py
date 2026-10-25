"""The aggregate-group graph from `website/inc/aggregate_round.inc` and the
ready-aggregate pass of `website/ajax/query.poll.coordinator.inc`."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Aggregate:
    """An aggregate group and the groups it draws from directly.

    A group that aggregates subgroups lists their groups here, as PHP's
    `constituents` column does.
    """

    group_id: int
    constituents: tuple[int, ...]


def topological_order(aggregates: Sequence[Aggregate]) -> list[Aggregate]:
    """Every aggregate before the aggregates it draws from.

    Of all such orders this is the one that stays closest to the input: at
    each step it takes the earliest aggregate that no remaining aggregate
    draws from. PHP's `topological_sort` sorts with a comparator that is not
    transitive (Q8, C10), so its order can depend on the input order.
    """
    pending = list(aggregates)
    order = []
    while pending:
        drawn_from = {c for a in pending for c in a.constituents}
        free = next((a for a in pending if a.group_id not in drawn_from), None)
        if free is None:
            raise ValueError("the aggregate groups draw from each other in a cycle")
        order.append(free)
        pending.remove(free)
    return order


def constituents_closure(aggregates: Sequence[Aggregate]) -> dict[int, list[int]]:
    """`{aggregate id: every group it draws from, directly or through
    another aggregate}`, in the order `compute_aggregate_constituents_tc`
    finds them."""
    direct = {a.group_id: a.constituents for a in aggregates}
    closure = {a.group_id: list(a.constituents) for a in aggregates}
    repeat = True
    while repeat:
        repeat = False
        for found in closure.values():
            for constituent in list(found):
                for second in direct.get(constituent, ()):
                    if second not in found:
                        found.append(second)
                        repeat = True
    return closure


def ready_aggregates(
    aggregates: Sequence[Aggregate],
    racing: Collection[int],
    unfinished: Collection[int],
) -> list[int]:
    """The aggregates with no round yet whose roster could be built now.

    `racing` holds the groups that have a round, and `unfinished` the groups
    with a scheduled heat lane that has no result. A constituent is ready
    when it is ready itself or has no such lane, so a constituent that was
    never scheduled counts as ready, as in PHP. `aggregates` comes in group
    sort order, and the result lists ids in the order PHP finds them.
    """
    ready: list[int] = []
    repeat = True
    while repeat:
        repeat = False
        for aggregate in aggregates:
            if aggregate.group_id in racing or aggregate.group_id in ready:
                continue
            if all(c in ready or c not in unfinished for c in aggregate.constituents):
                ready.append(aggregate.group_id)
                repeat = True
    return ready
