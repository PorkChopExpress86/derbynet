import pytest

from racing.engine.results import (
    LaneResult,
    Posted,
    is_all_zeroes,
    merge_results,
    mirror_mask,
    mirror_results,
    parse_heat_results,
)


def parse(body, nlanes=4):
    fields = [tuple(pair.split("=", 1)) for pair in body.split("&") if pair]
    return parse_heat_results(fields, nlanes)


def test_lane_and_place_fields_fill_their_lanes():
    assert parse("lane1=3.1&place2=1&lane2=2.9&lane9=1.0&place0=3&lane_count=4") == [
        Posted(time="3.1"),
        Posted(time="2.9", place="1"),
        Posted(),
        Posted(),
    ]


def test_a_later_field_for_the_same_lane_wins():
    assert parse("lane1=3.1&lane01=2.0", nlanes=1) == [Posted(time="2.0")]


def test_reverse_lanes_mirrors_results_and_the_lane_mask():
    results = parse("lane2=3.2&lane3=3.3&lane4=3.4")
    assert mirror_results(results, 4) == [
        Posted(time="3.4"),
        Posted(time="3.3"),
        Posted(time="3.2"),
        Posted(),
    ]
    assert mirror_mask(0b1110, 4) == 0b0111
    assert mirror_mask(0b0001, 4) == 0b1000


@pytest.mark.parametrize(
    ("body", "all_zero"),
    [
        ("lane1=0.000&lane2=0.000&lane3=0.000&lane4=0.000", True),
        ("lane1=0.000&lane2=0.000", False),
        ("place1=1&place2=2&place3=3&place4=4", False),
        ("lane1=&lane2=&lane3=&lane4=", True),
        ("lane1=0&lane2=.&lane3=00.0&lane4=0.", True),
        ("lane1=0.000&lane2=0.001&lane3=0.000&lane4=0.000", False),
        ("lane1=0.000&lane2=-0&lane3=0.000&lane4=0.000", False),
    ],
)
def test_all_zeroes_needs_a_zero_time_on_every_lane(body, all_zero):
    assert is_all_zeroes(parse(body)) is all_zero


def stored(*lanes):
    return [LaneResult(lane, None, None) for lane in lanes]


def test_places_come_from_times_with_ties_sharing_a_place():
    merged = merge_results(
        stored(1, 2, 3, 4), parse("lane1=3.2&lane2=3.1&lane3=3.1&lane4=3.0")
    )
    assert merged == [
        LaneResult(1, 3.2, 4),
        LaneResult(2, 3.1, 2),
        LaneResult(3, 3.1, 2),
        LaneResult(4, 3.0, 1),
    ]


def test_ties_take_one_place_and_the_next_place_skips():
    merged = merge_results(stored(1, 2, 3), parse("lane1=2.5&lane2=2.5&lane3=2.9"))
    assert [r.place for r in merged] == [1, 1, 3]


def test_a_posted_zero_counts_as_not_posted():
    before = [LaneResult(1, 3.0, 2), LaneResult(2, 2.0, 1)]
    merged = merge_results(before, parse("lane1=0&lane2=2.5&place1=0&place2=1"))
    assert merged == [LaneResult(1, 3.0, 2), LaneResult(2, 2.5, 1)]


def test_a_zero_with_a_decimal_point_is_a_time():
    merged = merge_results(stored(1, 2), parse("lane1=0.000&lane2=2.5"))
    assert merged == [LaneResult(1, 0.0, 1), LaneResult(2, 2.5, 2)]


def test_places_are_not_derived_unless_every_lane_has_a_new_time():
    merged = merge_results(stored(1, 2, 3), parse("lane1=2.5&lane2=2.4"))
    assert merged == [
        LaneResult(1, 2.5, None),
        LaneResult(2, 2.4, None),
        LaneResult(3, None, None),
    ]


def test_posted_places_stand_when_every_lane_has_one():
    merged = merge_results(stored(1, 2), parse("lane1=2.5&lane2=2.4&place1=1&place2=2"))
    assert merged == [LaneResult(1, 2.5, 1), LaneResult(2, 2.4, 2)]


def test_places_only_leave_the_times_as_they_were():
    before = [LaneResult(1, 3.0, None), LaneResult(2, None, None)]
    assert merge_results(before, parse("place1=2&place2=1")) == [
        LaneResult(1, 3.0, 2),
        LaneResult(2, None, 1),
    ]


def test_a_non_numeric_time_counts_as_not_posted():
    """Q16: PHP stores `---` as text in the time column."""
    merged = merge_results(stored(1, 2), parse("lane1=---&lane2=3.200"))
    assert merged == [LaneResult(1, None, None), LaneResult(2, 3.2, None)]


def test_only_scheduled_lanes_are_written():
    merged = merge_results(stored(2, 4), parse("lane1=1&lane2=2&lane3=3&lane4=4"))
    assert merged == [LaneResult(2, 2.0, 1), LaneResult(4, 4.0, 2)]
