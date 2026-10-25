from collections import defaultdict

import pytest

from racing.engine.scheduling.rough import (
    CantFulfill,
    make_rough_schedule,
    max_runs_per_lane,
)
from racing.engine.scheduling.round import TooFewRacers, make_chart

from .golden import GOLDEN_DIR


def php_capacity():
    """`{(lanes, cars): runs}` from the manifests: the last run count before
    PHP's first cant-fulfill, which has 0 heats."""
    capacity = defaultdict(int)
    for path in (GOLDEN_DIR / "engine" / "ordered").glob("manifest-lanes-*.tsv"):
        for line in path.read_text().splitlines():
            case_id, nheats, _ = line.split("\t")
            nlanes, ncars, n_times, max_runs = map(int, case_id.split("/")[1:])
            if max_runs == 0 and int(nheats) > 0:
                key = (nlanes, ncars)
                capacity[key] = max(capacity[key], n_times)
    return capacity


def test_max_runs_per_lane_is_what_php_can_schedule():
    capacity = php_capacity()
    assert len(capacity) == 1178 + 200
    wrong = {
        key: (max_runs_per_lane(*key), runs)
        for key, runs in capacity.items()
        if max_runs_per_lane(*key) != runs
    }
    assert wrong == {}


def test_max_runs_per_lane_pads_with_byes_and_knows_no_table():
    assert max_runs_per_lane(6, 3) == max_runs_per_lane(6, 6)
    assert max_runs_per_lane(7, 20) == 0
    assert max_runs_per_lane(9, 20) == 0
    assert max_runs_per_lane(4, 201) == 0
    assert max_runs_per_lane(6, 40) == 1


def test_beyond_the_tables_raises_cant_fulfill():
    for args in [(4, 10, 7, 0), (7, 20, 1, 0), (4, 201, 1, 0), (6, 40, 2, 0)]:
        with pytest.raises(CantFulfill):
            make_rough_schedule(*args)
    with pytest.raises(CantFulfill):
        make_rough_schedule(4, 10, 2, 1)


def test_a_chart_pads_byes_up_to_the_lane_count():
    chart = make_chart(["a", "b"], 4, 1)
    assert len(chart) == 4
    assert all(len(heat) == 4 and heat.count(None) == 2 for heat in chart)


def test_max_runs_per_car_pads_to_whole_heats_and_runs_each_car_once():
    chart = make_chart(list(range(5)), 4, 1, max_runs_per_car=1)
    cars = [car for heat in chart for car in heat if car is not None]
    assert sorted(cars) == [0, 1, 2, 3, 4]
    assert len(chart) == 2


def test_rotation_and_unused_lanes():
    chart = make_chart(["a", "b", "c"], 3, 1, rotation=True, unused_lane_mask=0b10)
    assert chart == [
        ["a", None, "b", "c"],
        ["b", None, "c", "a"],
        ["c", None, "a", "b"],
    ]


def test_fewer_than_two_racers_is_refused():
    with pytest.raises(TooFewRacers):
        make_chart(["a"], 4, 1)
