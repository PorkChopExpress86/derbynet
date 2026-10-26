"""The same reschedule with the same NodeBudget gives the same chart (#24)."""

import pytest

from racing.engine.scheduling.adjuster import (
    NodeBudget,
    ScheduleAdjuster,
    n_runs_per_lane,
)
from racing.engine.scheduling.round import make_chart


def adjusted(chart, budget):
    adjuster = ScheduleAdjuster(
        chart, {0: 1}, 0, 4, n_runs_per_lane(chart), NodeBudget(budget)
    )
    adjuster.remove_departed([5])
    adjuster.inject(30)
    adjuster.inject(31)
    adjuster.squeeze_out_byes()
    return adjuster.chart_debug(), adjuster.renumbering_map()


@pytest.mark.parametrize("budget", [1, 40, 500])
def test_ten_runs_give_one_chart(budget):
    """Small budgets cut the search short, where a clock would make the
    result depend on the machine."""
    chart = make_chart(list(range(1, 18)), 4, 1)
    results = [adjusted(chart, budget) for _ in range(10)]
    assert all(result == results[0] for result in results)


def test_the_budget_decides_where_the_search_stops():
    chart = make_chart(list(range(1, 18)), 4, 1)
    assert adjusted(chart, 1) != adjusted(chart, 500)
