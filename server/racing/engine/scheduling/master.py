"""The master schedule: one running order across the rounds of a round level.

Ported from `assign_master_heats()` in `website/inc/masterheat.inc` and the
master-schedule branch of `get_next_heat()` in `website/inc/autoadvance.inc`.
"""

from collections import deque
from collections.abc import Iterable
from typing import Literal

from racing.engine.phpcompat import php_round

type HeatRef = tuple[int, int]  # (roundid, heat)

type Requested = Literal["first", "next", "prev", "next-up"]


def assign_master_heats(
    unraced: Iterable[tuple[int, int, int]], start: int
) -> dict[HeatRef, int]:
    """Number the unraced heats from `start`, returning `{(roundid, heat): n}`.

    `unraced` holds `(round level, roundid, heat)` for each heat with a lane
    that has no result. `start` is 1 plus the highest master heat on a lane
    with a result, or 1. Levels go in ascending order. Within a level, each
    pass takes `weight` heats from each round in ascending roundid order,
    where `weight` is the round's heat count over the fewest heats of any
    round at that level, rounded as PHP rounds.
    """
    levels: dict[int, dict[int, list[int]]] = {}
    for level, roundid, heat in sorted(set(unraced)):
        levels.setdefault(level, {}).setdefault(roundid, []).append(heat)

    numbers: dict[HeatRef, int] = {}
    number = start
    for rounds in levels.values():
        fewest = min(len(heats) for heats in rounds.values())
        weights = {r: php_round(len(heats) / fewest) for r, heats in rounds.items()}
        queues = {r: deque(heats) for r, heats in rounds.items()}
        while any(queues.values()):
            for roundid, queue in queues.items():
                for _ in range(min(weights[roundid], len(queue))):
                    numbers[(roundid, queue.popleft())] = number
                    number += 1
    return numbers


def master_target(
    index: Iterable[tuple[int, int, int, bool]],
    current: HeatRef | None,
    requested: Requested,
) -> HeatRef | None:
    """The heat a master-schedule request lands on, or None if there is none.

    `index` holds `(masterheat, roundid, heat, has_result)` for each lane row,
    in the order PHP's query returns them, which on SQLite is row id order.
    When two heats share a number, the first one in `index` wins. `current` is
    None when no heat has ever been current.

    `next-up` is the lowest number on a lane without a result. Any other
    request with a current heat is that heat's number plus 1, or minus 1 for
    `prev`, so `first` acts as `next`. Raced heats are not skipped, and
    `prev` from 1 lands on a heat numbered 0 if one exists. With no current
    heat, the target is number 1.

    The caller decides whether master rules apply: `use-master-sched` on, the
    request not a heat number, and the current round not the timer test.
    """
    rows = list(index)
    if requested == "next-up":
        open_numbers = [n for n, _, _, has_result in rows if not has_result]
        if not open_numbers:
            return None
        wanted = min(open_numbers)
    elif current is None:
        wanted = 1
    else:
        mine = next((n for n, r, h, _ in rows if (r, h) == current), None)
        if mine is None:
            return None
        wanted = mine - 1 if requested == "prev" else mine + 1
    return next(((r, h) for n, r, h, _ in rows if n == wanted), None)
