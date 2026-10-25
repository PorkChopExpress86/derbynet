"""The rough schedule: heats from the generator tables, before reordering.

Ported from `website/inc/schedule_rough.inc`. A heat is a list of car
indices, one per lane, each `0 <= car < ncars`. `ncars` already counts the
byes that `round.py` adds.
"""

from typing import Any

from .generators import scheduler_data

Heat = list[int]


class CantFulfill(Exception):
    """The tables cannot give this many runs per lane for these counts.

    PHP reports `cant-fulfill` and returns an empty schedule, which
    `schedule.generate` then writes as an empty round (Q1 in #20).
    """

    def __init__(self, nlanes: int, ncars: int, n_times_per_lane: int):
        self.nlanes = nlanes
        self.ncars = ncars
        self.n_times_per_lane = n_times_per_lane
        super().__init__(
            f"no schedule gives {n_times_per_lane} runs per lane"
            f" for {ncars} cars on {nlanes} lanes"
        )


def scheduler_data_for(
    nlanes: int, ncars: int, max_runs_per_car: int
) -> dict[str, Any] | None:
    """PHP's `get_scheduler_data`, with None where PHP finds no table."""
    if max_runs_per_car != 0:
        # One rotation generator; make_ordered_schedule cuts it short.
        return {"generators": [[1] * (nlanes - 1)], "permutation_sets": []}
    if nlanes == 1:
        return {"generators": [[]] * 6, "permutation_sets": []}
    return scheduler_data(nlanes, ncars)


def apply_generator(gen: list[int], nlanes: int, ncars: int, heats: list[Heat]):
    for h in range(ncars):
        heat = [h]
        for lane in range(1, nlanes):
            heat.append((heat[lane - 1] + gen[lane - 1]) % ncars)
        heats.append(heat)


def apply_permutation(
    permutation: list[int], i_begin: int, i_end: int, heats: list[Heat]
):
    for i in range(i_begin, i_end):
        heats.append([permutation[car] for car in heats[i]])


def runs_supported(data: dict[str, Any] | None) -> int:
    """How many runs per lane `make_rough_schedule` can draw from `data`."""
    if data is None:
        return 0
    generators = data["generators"]
    psets = data["permutation_sets"]
    paired = min(len(generators), len(psets))
    return len(generators) + sum(len(psets[gi]) for gi in range(paired))


def max_runs_per_lane(nlanes: int, ncars: int) -> int:
    """How many runs per lane normal scheduling supports, 0 for none.

    `ncars` is the racer count; byes pad it up to `nlanes` as `round.py`
    does. With `max-runs-per-car` set, a round always gets exactly one run.
    """
    return runs_supported(scheduler_data_for(nlanes, max(ncars, nlanes), 0))


def make_rough_schedule(
    nlanes: int, ncars: int, n_times_per_lane: int, max_runs_per_car: int
) -> list[Heat]:
    data = scheduler_data_for(nlanes, ncars, max_runs_per_car)
    if data is None:
        # PHP throws a TypeError here (#64 C2).
        raise CantFulfill(nlanes, ncars, n_times_per_lane)
    generators = data["generators"]
    heats: list[Heat] = []
    remaining = n_times_per_lane

    for gen in generators:
        if remaining <= 0:
            break
        apply_generator(gen, nlanes, ncars, heats)
        remaining -= 1

    psets = data["permutation_sets"]
    k = 0
    making_progress = True
    while remaining > 0 and making_progress:
        making_progress = False
        gi = 0
        while remaining > 0 and gi < len(generators) and gi < len(psets):
            if k < len(psets[gi]):
                apply_permutation(psets[gi][k], gi * ncars, (gi + 1) * ncars, heats)
                remaining -= 1
                making_progress = True
            gi += 1
        k += 1

    if remaining > 0:
        raise CantFulfill(nlanes, ncars, n_times_per_lane)
    return heats
