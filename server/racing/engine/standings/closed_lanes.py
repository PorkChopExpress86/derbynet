"""Dropping the times of closed lanes from the standings (#108, D36, D37).
Not in PHP, which never closes a lane during a round.

A result row in a lane closed for its round stays stored and takes no part
in the standings. Under points racing the other cars of its heat move up,
so the heat scores as if it ran on the open lanes only.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class HeatResult:
    """One lane's result. `key` is the caller's, carried through."""

    round_id: int
    heat: int
    lane: int  # 1-based, as stored
    finish_time: float | None
    finish_place: int | None
    key: object = None


def _closed(result: HeatResult, closed: Mapping[int, int]) -> bool:
    return bool(closed.get(result.round_id, 0) & (1 << (result.lane - 1)))


def counted_results(
    results: Iterable[HeatResult], closed: Mapping[int, int]
) -> list[HeatResult]:
    """The results that count, in their order. `closed` maps a round id to
    the mask of its closed lanes, bit 0 for lane 1. Each place moves up by
    the number of cars in closed lanes of the same heat placed ahead of it;
    a car tied with a dropped car keeps its place."""
    results = list(results)
    if not any(closed.values()):
        return results
    ahead: dict[tuple[int, int], list[int]] = {}
    for result in results:
        if _closed(result, closed) and result.finish_place is not None:
            ahead.setdefault((result.round_id, result.heat), []).append(
                result.finish_place
            )
    counted = []
    for result in results:
        if _closed(result, closed):
            continue
        dropped = ahead.get((result.round_id, result.heat), ())
        place = result.finish_place
        if place is not None and dropped:
            place -= sum(1 for p in dropped if p < place)
            result = replace(result, finish_place=place)
        counted.append(result)
    return counted
