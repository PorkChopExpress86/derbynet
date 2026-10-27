"""Heat results as the timer or the race coordinator posts them.

Ported from `parse_heat_results`, `is_all_zeroes` and the merge and place
rules of `write_heat_results` in `website/inc/write-heat-results.inc`, and
the `reverse-lanes` mirrors in `website/ajax/action.timer-message.inc`.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from racing.engine.phpcompat import php_empty, php_intval, php_numeric


@dataclass(frozen=True)
class Posted:
    """One lane's posted `lane<N>` and `place<N>` text, None when absent."""

    time: str | None = None
    place: str | None = None


@dataclass(frozen=True)
class LaneResult:
    lane: int
    time: float | None
    place: int | None


def parse_heat_results(fields: Iterable[tuple[str, str]], nlanes: int) -> list[Posted]:
    """`parse_heat_results($nlanes)`: lane 1 at index 0. A key's number is
    read as `intval` reads it, and a later field for a lane wins."""
    times: dict[int, str] = {}
    places: dict[int, str] = {}
    for key, value in fields:
        for prefix, into in (("lane", times), ("place", places)):
            if key.startswith(prefix):
                lane = php_intval(key.removeprefix(prefix))
                if 0 < lane <= nlanes:
                    into[lane] = value
                break
    return [Posted(times.get(n), places.get(n)) for n in range(1, nlanes + 1)]


def mirror_results(results: list[Posted], nlanes: int) -> list[Posted]:
    """`reverse-lanes`: the timer's lane 1 is the track's last lane."""
    return [results[nlanes - i - 1] for i in range(nlanes)]


def mirror_mask(mask: int, nlanes: int) -> int:
    """`reverse-lanes` for the `heat-ready` lane mask."""
    return sum(1 << (nlanes - lane - 1) for lane in range(nlanes) if mask >> lane & 1)


_NOT_ZERO = re.compile(r"[^0.]")


def is_all_zeroes(results: Iterable[Posted]) -> bool:
    """Every lane up to the lane count has a time made only of `0` and `.`,
    bye lanes included (Q17). An empty time counts as zero."""
    return all(r.time is not None and not _NOT_ZERO.search(r.time) for r in results)


def _number(text: str | None) -> int | float | None:
    """A posted value, or None when it is not posted: absent, `""` or `"0"`
    as PHP's `empty()` reads them, or not a number (Q16)."""
    if text is None or php_empty(text):
        return None
    return php_numeric(text)


def merge_results(stored: list[LaneResult], posted: list[Posted]) -> list[LaneResult]:
    """The scheduled lanes, in lane order, after a post: a posted time or
    place replaces the stored one. When every lane got a time and some lane
    no place, every place comes from the times, ties sharing a place and the
    next place skipping (1, 1, 3)."""
    merged = []
    all_times = all_places = True
    for row in stored:
        sent = posted[row.lane - 1] if 0 < row.lane <= len(posted) else Posted()
        time, place = _number(sent.time), _number(sent.place)
        all_times &= time is not None
        all_places &= place is not None
        merged.append(
            LaneResult(
                row.lane,
                row.time if time is None else float(time),
                row.place if place is None else int(place),
            )
        )
    if all_times and not all_places:
        by_time = sorted(merged, key=lambda r: r.time)
        places = {}
        place = 1
        for i, row in enumerate(by_time):
            places[row.lane] = place
            if i + 1 == len(by_time) or by_time[i + 1].time != row.time:
                place = i + 2
        merged = [LaneResult(r.lane, r.time, places[r.lane]) for r in merged]
    return merged


DID_NOT_FINISH = 9.999
"""The time of a car the timer reports no time for (D42), as the standings
already read 9.9 seconds or more as a car that did not finish."""


def _finished(sent: Posted) -> bool:
    """A posted time a car can finish in: a number above zero."""
    number = _number(sent.time)
    return number is not None and number > 0


def is_false_start(lanes: Iterable[int], posted: list[Posted]) -> bool:
    """The timer reported times, but no scheduled lane has one above zero, so
    no car finished. It stops racing as `is_all_zeroes` does, rather than
    giving every car `DID_NOT_FINISH` (D42). A timer that reports places
    only reports no times."""
    if all(sent.time is None for sent in posted):
        return False
    return not any(
        _finished(posted[lane - 1]) for lane in lanes if 0 < lane <= len(posted)
    )


def merge_timer_results(
    stored: list[LaneResult], posted: list[Posted]
) -> list[LaneResult]:
    """`merge_results` for the timer (D42). When the timer reports times, a
    scheduled lane it gives no time, a zero, or a time that is not a number,
    did not finish and gets `DID_NOT_FINISH`, so every car has a time and the
    places follow from them. A timer that reports places only finishes no
    car."""
    if all(sent.time is None for sent in posted):
        return merge_results(stored, posted)
    filled = list(posted)
    for row in stored:
        if 0 < row.lane <= len(filled) and not _finished(filled[row.lane - 1]):
            filled[row.lane - 1] = replace(
                filled[row.lane - 1], time=str(DID_NOT_FINISH)
            )
    return merge_results(stored, filled)


def non_numeric_times(posted: Iterable[Posted]) -> list[int]:
    """The lanes whose posted time is text that is not a number (Q16)."""
    return [
        lane
        for lane, sent in enumerate(posted, start=1)
        if sent.time is not None
        and not php_empty(sent.time)
        and php_numeric(sent.time) is None
    ]
