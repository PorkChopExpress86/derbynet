import pytest

from racing.engine.phpcompat import php_round
from racing.engine.scheduling.master import assign_master_heats, master_target


@pytest.mark.parametrize(
    ("x", "php"),
    [
        (1.5, 2),
        (2.5, 3),
        (4.5, 5),
        (-2.5, -3),
        (2.4999999999999996, 2),
        (0.49999999999999994, 0),
        (3.0, 3),
        (0.0, 0),
    ],
)
def test_php_round_goes_half_away_from_zero(x, php):
    assert php_round(x) == php


def heats(level, roundid, count):
    return [(level, roundid, h) for h in range(1, count + 1)]


def order(numbers):
    return [heat for heat, _ in sorted(numbers.items(), key=lambda item: item[1])]


def test_three_heats_against_two_take_two_per_pass():
    numbers = assign_master_heats(heats(1, 10, 2) + heats(1, 20, 3), 1)
    assert order(numbers) == [(10, 1), (20, 1), (20, 2), (10, 2), (20, 3)]


def test_five_heats_against_two_take_three_per_pass_where_python_takes_two():
    numbers = assign_master_heats(heats(1, 10, 2) + heats(1, 20, 5), 1)
    assert order(numbers) == [
        (10, 1),
        (20, 1),
        (20, 2),
        (20, 3),
        (10, 2),
        (20, 4),
        (20, 5),
    ]


def test_numbers_start_at_start_and_levels_go_in_order():
    unraced = heats(2, 5, 2) + heats(1, 30, 1) + heats(1, 7, 1)
    assert assign_master_heats(unraced, 12) == {
        (7, 1): 12,
        (30, 1): 13,
        (5, 1): 14,
        (5, 2): 15,
    }


def test_rounds_go_by_roundid_not_by_input_order():
    numbers = assign_master_heats(heats(1, 9, 1) + heats(1, 3, 1), 1)
    assert numbers == {(3, 1): 1, (9, 1): 2}


def test_nothing_unraced_numbers_nothing():
    assert assign_master_heats([], 4) == {}


# (masterheat, roundid, heat, has_result), one row per lane.
INDEX = [
    (1, 1, 1, True),
    (1, 1, 1, True),
    (2, 2, 1, True),
    (3, 1, 2, False),
    (3, 1, 2, False),
    (4, 2, 2, False),
]


def test_next_up_is_the_lowest_number_without_a_result():
    assert master_target(INDEX, (1, 1), "next-up") == (1, 2)


def test_next_up_counts_a_heat_with_one_lane_unraced():
    index = [(1, 1, 1, True), (1, 1, 1, False), (2, 1, 2, False)]
    assert master_target(index, None, "next-up") == (1, 1)


def test_next_up_after_a_result_delete_goes_back_to_the_cleared_heat():
    """A cleared heat keeps its old number until the next reassign."""
    index = [(n, r, h, False if (r, h) == (1, 1) else has) for n, r, h, has in INDEX]
    assert master_target(index, (1, 2), "next-up") == (1, 1)


def test_next_up_with_every_heat_raced_is_none():
    raced = [(n, r, h, True) for n, r, h, _ in INDEX]
    assert master_target(raced, (2, 2), "next-up") is None


def test_next_lands_on_a_raced_heat_without_skipping_it():
    assert master_target(INDEX, (1, 1), "next") == (2, 1)


def test_prev_and_next_step_by_one():
    assert master_target(INDEX, (1, 2), "prev") == (2, 1)
    assert master_target(INDEX, (1, 2), "next") == (2, 2)


def test_prev_from_1_and_next_from_the_last_are_none():
    assert master_target(INDEX, (1, 1), "prev") is None
    assert master_target(INDEX, (2, 2), "next") is None


def test_prev_from_1_lands_on_an_unnumbered_heat_as_php_does():
    index = [*INDEX, (0, 9, 1, False)]
    assert master_target(index, (1, 1), "prev") == (9, 1)


def test_first_with_a_current_heat_acts_as_next():
    assert master_target(INDEX, (2, 1), "first") == (1, 2)


@pytest.mark.parametrize("requested", ["first", "next", "prev"])
def test_with_no_current_heat_the_target_is_number_1(requested):
    assert master_target(INDEX, None, requested) == (1, 1)


def test_a_current_heat_not_in_the_chart_has_no_target():
    assert master_target(INDEX, (-1, -1), "next") is None


def test_a_shared_number_goes_to_the_first_row():
    index = [(0, 5, 1, False), (0, 4, 1, False)]
    assert master_target(index, None, "next-up") == (5, 1)
