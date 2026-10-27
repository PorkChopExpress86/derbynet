"""Properties of the adjuster over random charts (#24)."""

from collections import Counter
from dataclasses import dataclass

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from racing.engine.scheduling.adjuster import (
    NodeBudget,
    ScheduleAdjuster,
    n_runs_per_lane,
)


@dataclass
class Case:
    chart: list[list[int | None]]
    nlanes: int
    mask: int
    nraced: int
    departed: list[int]
    late: list[int]
    budget: int

    @property
    def usable(self):
        return [lane for lane in range(self.nlanes) if not self.mask & (1 << lane)]

    def adjuster(self, kept=()):
        return ScheduleAdjuster(
            self.chart,
            {h: h + 1 for h in range(self.nraced)},
            self.mask,
            self.nlanes,
            n_runs_per_lane(self.chart),
            NodeBudget(self.budget),
            kept=kept,
        )


@st.composite
def cases(draw, min_unraced=0):
    nlanes = draw(st.integers(2, 8))
    usable = draw(
        st.lists(st.integers(0, nlanes - 1), min_size=1, max_size=nlanes, unique=True)
    )
    mask = sum(1 << lane for lane in range(nlanes) if lane not in usable)
    nracers = draw(st.integers(1, 10))
    racers = list(range(1, nracers + 1))
    nheats = draw(st.integers(max(1, min_unraced), 8))
    chart = []
    for _ in range(nheats):
        heat = [None] * nlanes
        chosen = draw(st.permutations(racers + [None] * len(usable)))
        for lane, racer in zip(sorted(usable), chosen, strict=False):
            heat[lane] = racer
        chart.append(heat)
    if not any(r for heat in chart for r in heat):
        chart[0][usable[0]] = 1
    nraced = draw(st.integers(0, nheats - min_unraced))
    departed = draw(st.lists(st.sampled_from(racers), unique=True, max_size=3))
    late = draw(st.lists(st.integers(11, 13), unique=True, max_size=2))
    budget = draw(st.integers(1, 200))
    return Case(chart, nlanes, mask, nraced, departed, late, budget)


def run(case, adjuster, inject=True, squeeze=True):
    adjuster.remove_departed(case.departed)
    if inject:
        for racer in case.late:
            adjuster.inject(racer)
    if squeeze:
        adjuster.squeeze_out_byes()
    return adjuster


def unraced(chart):
    return [heat for heat in chart if heat is not None]


@settings(max_examples=300, deadline=None)
@given(cases())
def test_raced_heats_keep_their_racers_and_their_order(case):
    adjuster = run(case, case.adjuster())
    chart = adjuster.updated_chart()
    if chart is None:
        return
    renumbered = adjuster.renumbering_map()
    new = [renumbered.get(h, h) for h in range(1, case.nraced + 1)]
    assert new == sorted(new)
    assert [i + 1 for i, heat in enumerate(chart) if heat is None] == new


@settings(max_examples=300, deadline=None)
@given(cases())
def test_no_racer_twice_in_a_heat_and_masked_lanes_stay_empty(case):
    chart = run(case, case.adjuster()).updated_chart()
    for heat in unraced(chart or []):
        racers = [r for r in heat if r is not None]
        assert len(racers) == len(set(racers))
        assert all(
            heat[lane] is None for lane in range(case.nlanes) if lane not in case.usable
        )


@settings(max_examples=300, deadline=None)
@given(cases())
def test_departed_racers_are_in_no_unraced_heat(case):
    case.late = [r for r in case.late if r not in case.departed]
    chart = run(case, case.adjuster()).updated_chart()
    for heat in unraced(chart or []):
        assert not set(heat) & set(case.departed)


@settings(max_examples=300, deadline=None)
@given(cases())
def test_a_late_racer_runs_n_runs_per_lane_times_in_each_usable_lane(case):
    adjuster = run(case, case.adjuster(), squeeze=False)
    chart = adjuster.updated_chart()
    if not case.late:
        return
    assert chart is not None
    runs = n_runs_per_lane(case.chart)
    for racer in case.late:
        lanes = Counter(
            lane for heat in unraced(chart) for lane, r in enumerate(heat) if r == racer
        )
        assert lanes == dict.fromkeys(case.usable, runs)


@settings(max_examples=300, deadline=None)
@given(cases())
def test_a_displaced_racer_keeps_its_lane(case):
    before = run(case, case.adjuster(), inject=False, squeeze=False).updated_chart()
    after = run(case, case.adjuster(), squeeze=False).updated_chart()
    if before is None:
        before = [
            None if h < case.nraced else heat for h, heat in enumerate(case.chart)
        ]
    if after is None:
        return

    def lanes(chart):
        return Counter(
            (r, lane)
            for heat in unraced(chart)
            for lane, r in enumerate(heat)
            if r is not None and r not in case.late
        )

    assert lanes(after) == lanes(before)


@settings(max_examples=300, deadline=None)
@given(cases())
def test_a_squeeze_never_adds_heats(case):
    adjuster = run(case, case.adjuster(), squeeze=False)
    before = len(adjuster.chart_debug())
    adjuster.squeeze_out_byes()
    assert len(adjuster.chart_debug()) <= before


@st.composite
def race_positions(draw):
    """A case whose raced heats are any of its heats, with the running heat
    any unraced heat and the staged heat the next unraced heat after it, or
    none. Unraced heats before the running heat were skipped."""
    case = draw(cases(min_unraced=1))
    nheats = len(case.chart)
    raced = draw(st.sets(st.integers(0, nheats - 1), max_size=nheats - 1))
    unraced = [h for h in range(nheats) if h not in raced]
    running = draw(st.sampled_from(unraced))
    later = [h for h in unraced if h > running]
    if draw(st.booleans()) and later:
        # No heat after the staged heat.
        case.chart = case.chart[: later[0] + 1]
        raced = {h for h in raced if h <= later[0]}
        assume(any(r for heat in case.chart for r in heat))
    return case, raced, [running, *later[:1]]


def text(heat):
    return " ".join("-" if r is None else str(r) for r in heat)


@settings(max_examples=500, deadline=None)
@given(race_positions())
def test_the_running_and_staged_heats_keep_racers_lanes_and_numbers(position):
    """D34, with the running heat anywhere in the round."""
    case, raced, kept = position
    adjuster = ScheduleAdjuster(
        case.chart,
        {h: h + 1 for h in raced},
        case.mask,
        case.nlanes,
        n_runs_per_lane(case.chart),
        NodeBudget(case.budget),
        kept=kept,
    )
    chart = run(case, adjuster).updated_chart()
    if chart is None:
        return
    debug = adjuster.chart_debug()
    for h in kept:
        assert chart[h] is None
        assert debug[h] == text(case.chart[h])
    renumbered = adjuster.renumbering_map()
    assert all(old > max(kept) + 1 for old in renumbered)
    if raced | set(kept) == set(range(len(case.chart))):
        for racer in case.late:
            heats = [h for h, heat in enumerate(chart) if heat and racer in heat]
            assert heats and min(heats) >= len(case.chart)
