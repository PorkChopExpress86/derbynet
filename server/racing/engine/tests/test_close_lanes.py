"""Closing a damaged lane in the middle of a round (#108, D36, D37)."""

from collections import Counter
from dataclasses import dataclass

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from racing.engine.scheduling.adjuster import (
    NodeBudget,
    ScheduleAdjuster,
    n_runs_per_lane,
)
from racing.engine.scheduling.rough import CantFulfill
from racing.engine.scheduling.round import make_chart
from racing.engine.standings import oracle
from racing.engine.standings.closed_lanes import HeatResult, counted_results
from racing.engine.standings.scoring import Scoring

UNLIMITED = 10**12


@dataclass
class Case:
    chart: list[list[int | None]]
    nlanes: int
    runs: int
    nraced: int
    kept: list[int]
    closed: int  # mask, bit 0 is lane 1

    def adjuster(self):
        return ScheduleAdjuster(
            self.chart,
            {h: h + 1 for h in range(self.nraced)},
            0,
            self.nlanes,
            self.runs,
            NodeBudget(UNLIMITED),
            kept=self.kept,
        )

    def open(self, lane):
        return not self.closed & (1 << lane)


@st.composite
def cases(draw):
    """A full chart from the generator, its raced prefix, a running heat
    and a staged heat or neither, and 1 or more lanes closed that leave at
    least 2 open."""
    nlanes = draw(st.integers(3, 8))
    runs = draw(st.sampled_from([1, 2, 3]))
    nracers = draw(st.integers(2, 14))
    try:
        chart = make_chart(
            list(range(1, nracers + 1)), nlanes, runs, rotation=draw(st.booleans())
        )
    except CantFulfill:
        assume(False)
    chart = [heat + [None] * (nlanes - len(heat)) for heat in chart]
    nraced = draw(st.integers(0, len(chart) - 1))
    nkept = draw(st.integers(0, min(2, len(chart) - nraced)))
    closed = draw(st.sets(st.integers(0, nlanes - 1), min_size=1, max_size=nlanes - 2))
    return Case(
        chart=chart,
        nlanes=nlanes,
        runs=runs,
        nraced=nraced,
        kept=list(range(nraced, nraced + nkept)),
        closed=sum(1 << lane for lane in closed),
    )


def closed(case):
    adjuster = case.adjuster()
    adjuster.remove_departed([])
    adjuster.close_lanes(case.closed)
    return adjuster


def new_chart(case, adjuster):
    """Every heat of the round after the closure: raced heats as they ran,
    kept heats as they are, the rest as the adjuster left them."""
    chart = adjuster.updated_chart()
    if chart is None:
        return [list(heat) for heat in case.chart]
    kept = adjuster.kept_heats()
    renumbered = adjuster.renumbering_map()
    raced = {renumbered.get(h + 1, h + 1): case.chart[h] for h in range(case.nraced)}
    return [
        raced.get(number) or kept.get(number) or heat
        for number, heat in enumerate(chart, start=1)
    ]


@settings(max_examples=400, deadline=None)
@given(cases())
def test_raced_running_and_staged_heats_keep_their_racers_and_numbers(case):
    adjuster = closed(case)
    chart = new_chart(case, adjuster)
    assert adjuster.renumbering_map() == {}
    for h in [*range(case.nraced), *case.kept]:
        assert chart[h] == case.chart[h]


@settings(max_examples=400, deadline=None)
@given(cases())
def test_no_other_unraced_heat_has_a_racer_in_a_closed_lane(case):
    chart = new_chart(case, closed(case))
    for heat in chart[case.nraced + len(case.kept) :]:
        assert all(r is None for lane, r in enumerate(heat) if not case.open(lane))


@settings(max_examples=400, deadline=None)
@given(cases())
def test_each_racer_runs_the_rounds_runs_per_lane_in_each_open_lane(case):
    chart = new_chart(case, closed(case))
    racers = {r for heat in case.chart for r in heat if r is not None}
    runs = Counter(
        (r, lane)
        for heat in chart
        for lane, r in enumerate(heat)
        if r is not None and case.open(lane)
    )
    assert runs == {
        (r, lane): case.runs
        for r in racers
        for lane in range(case.nlanes)
        if case.open(lane)
    }


@settings(max_examples=400, deadline=None)
@given(cases())
def test_no_racer_twice_in_a_heat_and_never_more_heats(case):
    chart = new_chart(case, closed(case))
    for heat in chart:
        racers = [r for r in heat if r is not None]
        assert len(racers) == len(set(racers))
    assert len(chart) <= len(case.chart)


def test_closing_a_lane_empties_it_after_the_staged_heat():
    """4 lanes, heat 3 of 6 running and heat 4 staged."""
    chart = make_chart(list(range(1, 7)), 4, 1)
    adjuster = ScheduleAdjuster(
        chart, {0: 1, 1: 2}, 0, 4, 1, NodeBudget(UNLIMITED), kept=[2, 3]
    )
    adjuster.remove_departed([])
    adjuster.close_lanes(0b0100)
    new = adjuster.updated_chart()
    assert new[:4] == [None] * 4
    assert adjuster.kept_heats() == {3: chart[2], 4: chart[3]}
    assert [heat[2] for heat in new[4:]] == [None, None]
    assert [heat[:2] + heat[3:] for heat in new[4:]] == [
        heat[:2] + heat[3:] for heat in chart[4:]
    ]


def test_a_heat_left_with_no_car_is_dropped():
    """The squeeze cannot fill the empty heat: the only other heat has a bye
    in an open lane, and a bye matches a bye."""
    adjuster = ScheduleAdjuster(
        [[1, 2, 3], [3, None, 1], [None, None, 2]],
        {0: 1},
        0,
        3,
        1,
        NodeBudget(UNLIMITED),
    )
    adjuster.remove_departed([])
    adjuster.close_lanes(0b100)
    assert adjuster.updated_chart() == [None, [3, None, None]]


def test_a_car_whose_only_run_was_on_the_closed_lane_gets_one_new_run():
    """An abbreviated round: car 7 ran only on lane 3, car 8 on lanes 2 and
    3, so only car 7 gets a new run, in the first open bye after the kept
    heat (D37)."""
    adjuster = ScheduleAdjuster(
        [[5, 6, 7], [6, 8, 5], [5, None, 8], [6, 5, None], [None, 6, 5]],
        {0: 1, 1: 2, 2: 3},
        0,
        3,
        1,
        NodeBudget(UNLIMITED),
        kept=[3],
    )
    adjuster.remove_departed([])
    adjuster.close_lanes(0b100)
    assert adjuster.updated_chart() == [None, None, None, None, [7, 6, None]]


def test_with_no_open_bye_the_new_run_is_a_heat_at_the_end():
    adjuster = ScheduleAdjuster(
        [[5, 6, 7], [6, 5, 8], [5, 6, None]],
        {0: 1, 1: 2},
        0,
        3,
        1,
        NodeBudget(UNLIMITED),
    )
    adjuster.remove_departed([])
    adjuster.close_lanes(0b100)
    assert adjuster.updated_chart() == [None, None, [5, 6, None], [7, 8, None]]


def test_a_departed_car_gets_no_new_run():
    adjuster = ScheduleAdjuster(
        [[5, 6, 7], [6, 5, None]],
        {0: 1},
        0,
        3,
        1,
        NodeBudget(UNLIMITED),
    )
    adjuster.remove_departed([7])
    adjuster.close_lanes(0b100)
    assert adjuster.updated_chart() is None


def test_runs_per_lane_counts_only_open_lanes():
    """After lane 1 closed, it holds only the raced heats' cars."""
    chart = [[1, 2, 3], [None, 3, 1], [None, 1, 2], [None, 2, 3], [None, 3, 1]]
    chart.append([None, 1, 2])
    assert n_runs_per_lane(chart) == 1
    assert n_runs_per_lane(chart, 0b001) == 2


# ----- The standings ---------------------------------------------------------


def results(*rows):
    return [HeatResult(*row) for row in rows]


def test_a_closed_lanes_results_do_not_count_in_their_round_only():
    rows = results(
        (1, 1, 1, 3.1, None),
        (1, 1, 3, 3.0, None),
        (2, 1, 3, 3.2, None),
    )
    assert counted_results(rows, {1: 0b100}) == [rows[0], rows[2]]


def test_places_move_up_past_a_dropped_lane():
    """Lane 3 placed first, lane 1 second and lane 2 third (D37)."""
    rows = results(
        (1, 1, 1, None, 2),
        (1, 1, 2, None, 3),
        (1, 1, 3, None, 1),
        (1, 2, 1, None, 1),
        (1, 2, 3, None, 2),
    )
    assert [(r.lane, r.finish_place) for r in counted_results(rows, {1: 0b100})] == [
        (1, 1),
        (2, 2),
        (1, 1),
    ]


def test_a_round_scored_by_its_own_scoring():
    racer = oracle.Racer(1, "A", "B", 101, "", 1, 1)
    event = oracle.Event(
        groups=(oracle.Group(1, "Lions", 1),),
        subgroups=(oracle.Subgroup(1, 1, "Lions", 1),),
        rounds=(
            oracle.Round(1, 1, 1, True, Scoring.DROP_SLOWEST),
            oracle.Round(2, 1, 2, True),
        ),
        racers=(racer,),
        results=tuple(
            oracle.Result(round_id, 1, time, None)
            for round_id in (1, 2)
            for time in (3.0, 4.0)
        ),
        settings=oracle.Settings(scoring=Scoring.AVERAGE),
    )
    scores = {row.round.id: row.summary.score for row in oracle.Standings(event).rows}
    assert scores == {1: 3.0, 2: 3.5}
