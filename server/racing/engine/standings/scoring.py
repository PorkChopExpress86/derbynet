"""One racer's score in one round, from `sql_scoring_expression` and the
`avg`, `best` and `worst` columns of `StandingsOracle::make_result_summary`
in `website/inc/standings.inc`.

PHP computes these in SQLite. `SUM()` becomes `math.fsum` (#20, "Score
sums"), and SQL's NULL rules stay: a NULL value is left out of `SUM`, `MIN`
and `MAX` but counted by `COUNT(*)`, and `SUM` of no values is NULL.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum


class Scoring(IntEnum):
    """The `scoring` setting. Lower scores are better in every mode."""

    AVERAGE = 0
    DROP_SLOWEST = 1
    FASTEST = 2


@dataclass(frozen=True)
class Summary:
    """`base` is the number of heats with a result. `score` ranks the racer
    and is None when no heat has a value in this mode. `avg` is what the
    standings show: the score with times, a point total with points."""

    base: int
    score: float | None
    avg: float | int | None
    best: float | int | None
    worst: float | int | None


def summarize(
    times: Sequence[float | None],
    places: Sequence[int | None],
    *,
    use_points: bool,
    scoring: Scoring,
    lane_count: int,
) -> Summary:
    """The summary of one racer's heats in one round. `times[i]` and
    `places[i]` are heat i's result, at least one of them not None."""
    raw = list(places if use_points else times)
    finish = [float(v) for v in raw if v is not None]
    count = len(raw)
    score = _score(finish, count, scoring)
    if use_points:
        avg = _points([v for v in places if v is not None], count, scoring, lane_count)
    else:
        avg = score
    present = [v for v in raw if v is not None]
    return Summary(
        base=count,
        score=score,
        avg=avg,
        best=min(present, default=None),
        worst=max(present, default=None),
    )


def _score(finish: list[float], count: int, scoring: Scoring) -> float | None:
    if not finish:
        return None
    if scoring == Scoring.AVERAGE:
        return math.fsum(finish) / count
    if scoring == Scoring.DROP_SLOWEST:
        # The SQLite rule on every database, so one heat scores as itself
        # (Q12). PHP's other branch divides by zero there.
        if count == 1:
            return max(finish)
        return (math.fsum(finish) - max(finish)) / (count - 1)
    if scoring == Scoring.FASTEST:
        return min(finish)
    raise ValueError(f"unknown scoring {scoring}")


def _points(
    places: list[int], count: int, scoring: Scoring, lane_count: int
) -> int | None:
    if not places:
        return None
    if scoring == Scoring.AVERAGE:
        return (1 + lane_count) * count - sum(places)
    if scoring == Scoring.DROP_SLOWEST:
        return (1 + lane_count) * (count - 1) - (sum(places) - max(places))
    return (1 + lane_count) - min(places)
