"""The ordered schedule: the rough heats, greedily reordered.

Ported from `website/inc/schedule_ordered.inc`. Each step picks the remaining
rough heat with the lowest score, where the score penalizes uneven race
counts and cars that race in consecutive heats.

`reorder_reference` is the literal port, float arithmetic and scan order
included (#20, "Numeric and comparison rules"). It costs about heats squared
times cars, about 13 s for 200 cars on 6 lanes with 6 runs. `reorder` makes
the same choices in integer arithmetic in a fraction of a second. The parity
tests hold both to the PHP fixtures, and a randomized test holds them to each
other.
"""

from .rough import Heat, make_rough_schedule

RACE_COUNT_WEIGHT = 10
CONSECUTIVE_WEIGHT = 1000
REPEAT_WEIGHT = 1000

INITIAL_BEST_SCORE = 10000
EPSILON = 0.00001


def rate_race_counts(next_heat: Heat, n_prev_heats: int, appearances: list[int]):
    nlanes = len(next_heat)
    ncars = len(appearances)
    new_appearances = list(appearances)
    for car in next_heat:
        new_appearances[car] += 1
    dev = 0
    target = ((n_prev_heats + 1) * nlanes) / ncars
    for app in new_appearances:
        dev += (app - target) * (app - target)
    return dev * RACE_COUNT_WEIGHT


def rate_consecutive(prev_heat: Heat, next_heat: Heat) -> int:
    result = 0
    for lane in range(len(next_heat)):
        for prev_lane in range(len(prev_heat)):
            if prev_heat[prev_lane] == next_heat[lane]:
                result += CONSECUTIVE_WEIGHT
                if lane == prev_lane:
                    result += REPEAT_WEIGHT
    return result


def rate_next_heat(prev_heat, n_prev_heats, next_heat, appearances):
    rating = rate_race_counts(next_heat, n_prev_heats, appearances)
    if n_prev_heats > 0:
        rating += rate_consecutive(prev_heat, next_heat)
    return rating


class ChoseAUsedHeat(RuntimeError):
    """No heat scored under 10000 and the last rough heat was already used.

    PHP then appends a null heat. On the last step it returns the schedule
    with that trailing null. On an earlier step the next rating fails on the
    null previous heat. No schedule the tables give reaches it.
    """


def reorder_reference(
    rough: list[Heat], ncars: int, max_runs_per_car: int
) -> list[Heat]:
    remaining: list[Heat | None] = list(rough)
    appearances = [0] * ncars
    ordered: list[Heat] = []
    prev_heat: Heat = []
    # PHP loops n_times_per_lane * ncars times, which is len(rough).
    for _ in range(len(rough)):
        best_heat_index = len(remaining) - 1
        best_heat_score = INITIAL_BEST_SCORE
        for rough_i, heat in enumerate(remaining):
            if heat:
                score = rate_next_heat(prev_heat, len(ordered), heat, appearances)
                if score < best_heat_score - EPSILON:
                    best_heat_score = score
                    best_heat_index = rough_i

        chosen = remaining[best_heat_index]
        if chosen is None:
            raise ChoseAUsedHeat(best_heat_index)
        prev_heat = chosen
        remaining[best_heat_index] = None
        for car in prev_heat:
            appearances[car] += 1
            if max_runs_per_car > 0 and appearances[car] > max_runs_per_car:
                return ordered
        ordered.append(prev_heat)
    return ordered


def reorder(rough: list[Heat], ncars: int, max_runs_per_car: int) -> list[Heat]:
    """`reorder_reference`, in time linear in heats times cars per heat.

    Each candidate adds `nlanes` appearances, so they all share the target and
    the sum of appearances. The race-count deviation is then
    `sum(a*a) - S*S/ncars` with `S = (n_prev + 1) * nlanes`, and candidates
    differ only by the integer `sum(a*a)`. Scores of two candidates are equal
    or at least 10 apart, so the 0.00001 epsilon never decides between them.
    Against the first `best = 10000`, the exact score is a fraction with
    denominator `ncars` and so is never within 1/ncars of 10000 unless equal.
    That rests on every heat holding `nlanes` distinct cars, which the tables
    and the rotation generator give. Any other rough schedule runs the
    reference.
    """
    nlanes = len(rough[0]) if rough else 0
    if any(len(set(heat)) != nlanes or len(heat) != nlanes for heat in rough):
        return reorder_reference(rough, ncars, max_runs_per_car)

    heats_of_car: list[list[int]] = [[] for _ in range(ncars)]
    for i, heat in enumerate(rough):
        for car in heat:
            heats_of_car[car].append(i)

    appearances = [0] * ncars
    # added[i]: how much rough[i] would raise sum(a*a), sum(2a + 1) over its cars.
    added = [len(heat) for heat in rough]
    unused = list(range(len(rough)))
    sum_squares = 0
    ordered: list[Heat] = []
    prev_heat: Heat = []
    for _ in range(len(rough)):
        penalty: dict[int, int] = {}
        for prev_lane, car in enumerate(prev_heat):
            for i in heats_of_car[car]:
                repeat = rough[i][prev_lane] == car
                penalty[i] = (
                    penalty.get(i, 0)
                    + CONSECUTIVE_WEIGHT
                    + (REPEAT_WEIGHT if repeat else 0)
                )

        best_key = None
        best_index = -1
        for i in unused:
            key = RACE_COUNT_WEIGHT * added[i] + penalty.get(i, 0)
            if best_key is None or key < best_key:
                best_key = key
                best_index = i

        total = nlanes * (len(ordered) + 1)
        exact_numerator = (
            ncars * (RACE_COUNT_WEIGHT * sum_squares + best_key)
            - RACE_COUNT_WEIGHT * total * total
        )
        if exact_numerator >= INITIAL_BEST_SCORE * ncars:
            best_index = len(rough) - 1
            if best_index not in unused:
                raise ChoseAUsedHeat(best_index)

        chosen = rough[best_index]
        if max_runs_per_car > 0 and any(
            appearances[car] >= max_runs_per_car for car in chosen
        ):
            return ordered
        unused.remove(best_index)
        sum_squares += added[best_index]
        for car in chosen:
            appearances[car] += 1
            for i in heats_of_car[car]:
                added[i] += 2
        ordered.append(chosen)
        prev_heat = chosen
    return ordered


def reference_ordered_schedule(
    nlanes: int, ncars: int, n_times_per_lane: int, max_runs_per_car: int
) -> list[Heat]:
    rough = make_rough_schedule(nlanes, ncars, n_times_per_lane, max_runs_per_car)
    return reorder_reference(rough, ncars, max_runs_per_car)


def make_ordered_schedule(
    nlanes: int, ncars: int, n_times_per_lane: int, max_runs_per_car: int
) -> list[Heat]:
    rough = make_rough_schedule(nlanes, ncars, n_times_per_lane, max_runs_per_car)
    return reorder(rough, ncars, max_runs_per_car)
