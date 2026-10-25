"""One round's chart from its roster, ported from `schedule_one_round` and
`form_chart` in `website/inc/schedule_one_round.inc`."""

from collections.abc import Sequence

from .lanes import spread_across_unused_lanes
from .ordered import make_ordered_schedule
from .rotation import make_rotation_schedule


class TooFewRacers(Exception):
    """A round needs at least two racers to schedule."""


def make_chart[T](
    roster: Sequence[T],
    nlanes: int,
    n_times_per_lane: int,
    *,
    max_runs_per_car: int = 0,
    rotation: bool = False,
    unused_lane_mask: int = 0,
) -> list[list[T | None]]:
    """The chart as racers per heat and physical lane, None for an empty lane.

    `roster` is in PHP's order: roster entry order, or car number under
    `rotation-schedule`. `nlanes` counts the lanes in use. Byes pad the roster
    up to `nlanes`, then until `len * max_runs_per_car` divides by `nlanes`.
    Raises TooFewRacers, or CantFulfill from the engine.
    """
    if len(roster) < 2:
        raise TooFewRacers
    padded: list[T | None] = list(roster)
    padded += [None] * (nlanes - len(padded))
    while len(padded) * max_runs_per_car % nlanes != 0:
        padded.append(None)
    if rotation:
        schedule = make_rotation_schedule(nlanes, len(padded), n_times_per_lane)
    else:
        schedule = make_ordered_schedule(
            nlanes, len(padded), n_times_per_lane, max_runs_per_car
        )
    chart = [[padded[car] for car in heat] for heat in schedule]
    return spread_across_unused_lanes(chart, unused_lane_mask)
