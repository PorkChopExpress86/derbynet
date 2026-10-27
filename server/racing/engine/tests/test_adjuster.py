import pytest

from racing.engine.scheduling.adjuster import (
    DeadlineBudget,
    NodeBudget,
    ScheduleAdjuster,
    n_runs_per_lane,
)


def squeezed(chart, raced=()):
    adjuster = ScheduleAdjuster(
        chart, {h: h + 1 for h in raced}, 0, 2, 1, NodeBudget(1000)
    )
    adjuster.remove_departed([])
    adjuster.squeeze_out_byes()
    return adjuster.updated_chart() and adjuster.chart_debug()


def test_the_first_heat_never_takes_a_displaced_racer():
    """Q3, reproduced (D19). Heat 3's racers fit the byes of heats 1 and 2,
    but heat 1 is index 0. PHP 8.4 gives both results through the
    differential harness of #24."""
    assert squeezed([[1, None], [None, 2], [3, 4]]) is None
    assert squeezed([[5, 6], [1, None], [None, 2], [3, 4]], raced=[0]) == [
        None,
        "1 4",
        "3 2",
    ]


def test_runs_per_lane_come_from_the_lowest_lane_in_use():
    assert n_runs_per_lane([[None, 1, 2], [None, 2, 1], [None, 1, 2]]) == 1
    assert (
        n_runs_per_lane([[None, 1, 2], [None, 2, 1], [None, 1, 2], [None, 2, 1]]) == 2
    )


def test_racer_zero_and_a_kept_raced_heat_are_refused():
    with pytest.raises(ValueError):
        ScheduleAdjuster([[0, 1]], {}, 0, 2, 1, NodeBudget(1))
    with pytest.raises(ValueError):
        ScheduleAdjuster([[2, 1]], {0: 1}, 0, 2, 1, NodeBudget(1), kept=[0])
    adjuster = ScheduleAdjuster([[2, 1]], {}, 0, 2, 1, NodeBudget(1))
    with pytest.raises(ValueError):
        adjuster.inject(0)


def test_a_deadline_counts_whole_seconds_as_php_time_does():
    now = [100.9]
    budget = DeadlineBudget(lambda: now[0], 5)
    budget.start()
    now[0] = 105.99
    assert not budget.exhausted()
    now[0] = 106.0
    assert budget.exhausted()


def test_fitting_in_two_racers_names_each_timing_key_once():
    """PHP's `times` is keyed by name, so a second racer's run 0 sets the
    first racer's keys again and adds none."""
    chart = [[1, 2], [3, 4], [2, 1], [4, 3]]
    adjuster = ScheduleAdjuster(chart, {0: 1}, 0, 2, 1, NodeBudget(1000))
    adjuster.remove_departed([])
    adjuster.inject(5)
    once = list(adjuster.timing_keys)
    adjuster.inject(6)

    assert adjuster.updated_chart() is not None
    assert once == [
        "constructed",
        "run-0-extra-0-start",
        "run-0-injected",
        "run-0-racechart",
    ]
    assert adjuster.timing_keys == once
