"""`reorder` against the literal `reorder_reference` on rough schedules the
tables never give: heats drawn from a few repeated heats, so that every
candidate can score 10000 or more and PHP's fallback to the last index runs."""

import random

import pytest

from racing.engine.scheduling.ordered import (
    ChoseAUsedHeat,
    reorder,
    reorder_reference,
)


def outcome(fn, rough, ncars, max_runs_per_car):
    try:
        return fn(rough, ncars, max_runs_per_car)
    except ChoseAUsedHeat as error:
        return ("ChoseAUsedHeat", error.args)


def random_rough(rng):
    ncars = rng.randint(2, 12)
    nlanes = rng.randint(1, min(ncars, 6))
    pool = [rng.sample(range(ncars), nlanes) for _ in range(rng.randint(1, 4))]
    nheats = ncars * rng.randint(1, 3)
    return [list(rng.choice(pool)) for _ in range(nheats)], ncars


@pytest.mark.parametrize("seed", range(20))
def test_reorder_makes_the_reference_choices(seed):
    rng = random.Random(seed)
    for _ in range(100):
        rough, ncars = random_rough(rng)
        max_runs = rng.choice([0, 0, 1, 2])
        expected = outcome(reorder_reference, rough, ncars, max_runs)
        assert outcome(reorder, rough, ncars, max_runs) == expected, (
            rough,
            ncars,
            max_runs,
        )


@pytest.mark.parametrize("fn", [reorder_reference, reorder])
def test_no_score_under_10000_takes_the_last_heat(fn):
    heat = [0, 1, 2, 3, 4, 5]
    swapped = [1, 0, 2, 3, 4, 5]
    # After `heat`, `swapped` scores exactly 10000 and `heat` 12000, so PHP
    # takes the last index. On the next step that index is already used.
    with pytest.raises(ChoseAUsedHeat):
        fn([heat, swapped, heat], 6, 0)
