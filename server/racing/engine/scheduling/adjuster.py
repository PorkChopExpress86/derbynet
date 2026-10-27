"""Rescheduling a round in progress (#24): PHP's `ScheduleAdjuster`.

Ported from `website/ajax/action.schedule.reschedule.inc`. The adjuster turns
departed racers into byes, fits late racers into the unraced heats, and
squeezes out heats whose racers can fill byes elsewhere. Each method keeps
PHP's algorithm, loop order and comparisons, so the chart equals PHP's
whenever PHP's search ends before its deadline. Where PHP stops a search on
the wall clock, a `SearchBudget` decides here.

A chart is a list of heats, each a list of racer ids by physical lane, None
for an empty lane. In the adjuster a heat that cannot change is None.
"""

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import Protocol

from racing.engine.scheduling.lanes import count_one_bits

INJECTION_TIME_LIMIT = 10
"""Seconds for fitting one racer in, shared by the runs per lane."""

OVERALL_TIME_LIMIT = 60
"""Seconds after which no further late racer is fitted in."""

type Heat = list[int | None]


class SearchBudget(Protocol):
    """When a search for the best place to fit a racer in must stop.

    `start` begins each search, `proposal` follows each complete proposal,
    and the search stops once `exhausted` is true. It then keeps the best
    proposal found so far.
    """

    def start(self) -> None: ...

    def proposal(self) -> None: ...

    def exhausted(self) -> bool: ...


class NodeBudget:
    """Ends each search after `n` proposals, so a test always gets the same
    chart."""

    def __init__(self, n: int):
        self.n = n
        self._count = 0

    def start(self) -> None:
        self._count = 0

    def proposal(self) -> None:
        self._count += 1

    def exhausted(self) -> bool:
        return self._count >= self.n


class DeadlineBudget:
    """PHP's deadline: a search ends once the clock is more than `seconds`
    past its start, counted in whole seconds as PHP's `time()` counts them.
    PHP gives each search `INJECTION_TIME_LIMIT // n_runs_per_lane` seconds.
    """

    def __init__(self, clock: Callable[[], float], seconds: int):
        self._clock = clock
        self.seconds = seconds
        self._deadline = 0

    def start(self) -> None:
        self._deadline = int(self._clock()) + self.seconds

    def proposal(self) -> None:
        pass

    def exhausted(self) -> bool:
        return int(self._clock()) > self._deadline


def n_runs_per_lane(chart: Iterable[Heat]) -> int:
    """PHP's `n_runs_per_lane()`: in the lowest lane that holds a racer, the
    number of heats with a racer there, integer-divided by the number of
    distinct racers there. Raced heats count."""
    chart = list(chart)
    lane = min(lane for heat in chart for lane, r in enumerate(heat) if r is not None)
    racers = [heat[lane] for heat in chart if heat[lane] is not None]
    return len(racers) // len(set(racers))


def _in_heat_loosely(candidate: int | None, heat: Heat) -> bool:
    """PHP's loose `in_array($candidate, $heat)` in the squeeze. A bye
    (`null`) matches any bye, so a heat with a bye in an active lane is never
    squeezed out."""
    return candidate in heat


def _no_heat(index: int | None) -> bool:
    """PHP's `!$relos[$lane]`, which also takes heat index 0 for no heat, so
    the first heat of the chart never takes a displaced racer (Q3 in #20,
    reproduced in v1 by D19; #65 fixes it)."""
    return not index


class ScheduleAdjuster:
    """One reschedule of one round.

    `chart` holds the round's heats in heat order, each `nlanes` wide.
    `raced` maps the index of each heat with a result to its heat number.
    `kept` holds the indices of unraced heats that must not change either:
    the running heat and the staged heat (D34). Kept heats are treated as
    raced, except that `chart_debug` shows their racers and no heat before
    the last kept heat is squeezed out, so none of them changes its number.

    As in PHP, `remove_departed` is what first looks for byes, so call it,
    with no racers if none departed, before `inject`.
    """

    def __init__(
        self,
        chart: Iterable[Heat],
        raced: Mapping[int, int],
        lane_mask: int,
        nlanes: int,
        n_runs_per_lane: int,
        budget: SearchBudget,
        kept: Iterable[int] = (),
    ):
        self._chart: list[Heat | None] = [list(heat) for heat in chart]
        if any(len(heat) != nlanes for heat in self._chart):
            raise ValueError(f"every heat must be {nlanes} lanes wide")
        if any(r is not None and r <= 0 for heat in self._chart for r in heat):
            raise ValueError("racer ids must be positive")
        kept = set(kept)
        if kept & set(raced):
            raise ValueError("a kept heat cannot be raced")
        self._numbers: list[int | None] = [None] * len(self._chart)
        # Each kept heat's racers, by its heat number, which never changes.
        self._kept = {index + 1: self._chart[index] for index in kept}
        for index, number in [*raced.items(), *((i, i + 1) for i in kept)]:
            self._chart[index] = None
            self._numbers[index] = number
        self._nlanes = nlanes
        self._lane_mask = lane_mask
        self._nlanes_usable = nlanes - count_one_bits(lane_mask)
        self._n_runs = n_runs_per_lane
        self._budget = budget
        self._squeeze_from = max(kept, default=-1) + 1
        # Counts the heats unraced at load, less those squeezed out. As in
        # PHP, heats added for a late racer are not counted.
        self._nheats_unraced = len(self._chart) - len(raced) - len(kept)
        self._nheats_new = 0
        self._modified = False
        self._failed = False
        self._has_byes = False
        self._byes: list[list[int]] = [[] for _ in range(nlanes)]
        self.heats_squeezed_out = 0
        self.timing_keys = ["constructed"]
        """The keys of PHP's `timing.internal`, in PHP's order."""

        self._keep_going = True
        self._best: tuple[float, list[int | None], list[int | None]] | None = None
        self._injection_heats: list[int | None] = []
        self._displacement_heats: list[int | None] = []
        self._to_race: list[Heat | None] = []

    def _time(self, key: str) -> None:
        """PHP's `$this->times[key] = ...`: a key set again keeps its place."""
        if key not in self.timing_keys:
            self.timing_keys.append(key)

    def _active(self, lane: int) -> bool:
        return self._lane_mask & (1 << lane) == 0

    # ----- Results ----------------------------------------------------------

    def updated_chart(self) -> list[Heat | None] | None:
        """The new chart, None for a heat that cannot change, or None when
        nothing changed or a racer could not be fitted in."""
        if not self._modified or self._failed:
            return None
        return deepcopy(self._chart)

    def renumbering_map(self) -> dict[int, int]:
        """`{old heat number: new heat number}` for each raced heat that a
        squeeze moves. Kept heats never move."""
        return {
            number: index + 1
            for index, number in enumerate(self._numbers)
            if number is not None and number != index + 1
        }

    def chart_debug(self) -> list[str | None]:
        """PHP's reply `chart`: one string per heat, `-` for a bye, None for a
        raced heat. A kept heat shows its racers."""
        return [
            None
            if heat is None
            else " ".join("-" if r is None else str(r) for r in heat)
            for heat in self._heats_to_race()
        ]

    def _heats_to_race(self) -> list[Heat | None]:
        """The chart with each kept heat's racers in place, None for a raced
        heat. Kept heats count in the scoring, as unraced heats do in PHP."""
        return [
            self._kept.get(self._numbers[index]) if heat is None else heat
            for index, heat in enumerate(self._chart)
        ]

    # ----- Departed racers and byes -------------------------------------------

    def remove_departed(self, racer_ids: Iterable[int]) -> None:
        """Turn each departed racer's lanes in the unraced heats into byes."""
        for gone in racer_ids:
            for heat in self._chart:
                if heat is not None:
                    for lane in range(self._nlanes):
                        if heat[lane] == gone:
                            heat[lane] = None
                            self._modified = True
        self._look_for_byes()

    def _look_for_byes(self) -> bool:
        self._has_byes = False
        for lane in range(self._nlanes):
            self._byes[lane] = []
            if self._active(lane):
                for h, heat in enumerate(self._chart):
                    if heat is not None and heat[lane] is None:
                        self._byes[lane].append(h)
                        self._has_byes = True
        return self._has_byes

    def squeeze_out_byes(self) -> None:
        """Remove heats one at a time while some heat's racers can each move
        to a bye in the same lane of another heat."""
        while self._look_for_byes():
            for lane in range(self._nlanes):
                if self._active(lane) and not self._byes[lane]:
                    return
            if not self._squeeze_out_one_heat_of_byes():
                return

    def _squeeze_out_one_heat_of_byes(self) -> bool:
        for h in range(self._squeeze_from, len(self._chart)):
            heat = self._chart[h]
            if heat is None:
                continue
            relos: list[int | None] = [None] * self._nlanes
            plausible = True
            for lane in range(self._nlanes):
                if not self._active(lane):
                    continue
                for bh in self._byes[lane]:
                    if _in_heat_loosely(heat[lane], self._chart[bh]):
                        continue
                    relos[lane] = bh
                    break
                if _no_heat(relos[lane]):
                    plausible = False
                    break
            if plausible:
                for lane, bh in enumerate(relos):
                    if not _no_heat(bh):
                        self._chart[bh][lane] = heat[lane]
                del self._chart[h]
                del self._numbers[h]
                self._modified = True
                self._nheats_unraced -= 1
                self.heats_squeezed_out += 1
                return True
        return False

    # ----- Fitting a late racer in --------------------------------------------

    def inject(self, racer_id: int) -> None:
        """Fit the racer into `n_runs_per_lane` heats in each usable lane.

        For each run, a search proposes an unraced heat per lane. A racer
        already in that slot moves to the same lane of a heat added for this
        run. The proposal whose pairings vary least wins. With no proposal,
        one more heat is added, up to once per usable lane.
        """
        if racer_id <= 0:
            raise ValueError("racer ids must be positive")
        for run in range(self._n_runs):
            self._add_minimal_new_heats()
            # PHP stops after the search with `nlanes_usable - 1` extra heats
            # and never searches with the last heat it adds, so it can fail
            # when the racer needs a new heat in every lane (Q21 in #20,
            # fixed for D34). The extra search runs only where PHP fails.
            for extra in range(self._nlanes_usable + 1):
                self._time(f"run-{run}-extra-{extra}-start")
                self._best = None
                self._inject_once(racer_id)
                if self._best is not None or not self._keep_going:
                    if not self._keep_going:
                        self._time(f"run-{run}-timed-out")
                    self._time(f"run-{run}-injected")
                    break
                self._add_heat()
                self._nheats_new += 1
            if self._best is not None:
                _, injection, displacement = self._best
                self._chart = self._injected_chart(injection, displacement, racer_id)
                self._modified = True
                self._time(f"run-{run}-racechart")
            else:
                self._failed = True

    def _add_minimal_new_heats(self) -> None:
        self._nheats_new = max(
            0 if self._has_byes else 1, self._nlanes_usable - self._nheats_unraced
        )
        for _ in range(self._nheats_new):
            self._add_heat()

    def _add_heat(self) -> None:
        """An empty heat at the end. PHP leaves `heat_numbers` short, and its
        `array_splice` past the end does nothing, so an unnumbered heat here
        gives the same renumbering."""
        self._chart.append([None] * self._nlanes)
        self._numbers.append(None)

    def _inject_once(self, racer_id: int) -> None:
        self._budget.start()
        self._keep_going = True
        self._best = None
        self._injection_heats = [None] * self._nlanes
        self._displacement_heats = [None] * self._nlanes
        # The search writes displaced racers into these same heat lists.
        self._to_race = self._heats_to_race()
        self._inject_at_lane(racer_id, 0)

    def _inject_at_lane(self, racer_id: int, lane: int) -> None:
        if self._budget.exhausted():
            self._keep_going = False
            return
        if lane >= self._nlanes:
            self._consider_proposal(racer_id)
        elif not self._active(lane):
            self._injection_heats[lane] = None
            self._displacement_heats[lane] = None
            self._inject_at_lane(racer_id, lane + 1)
        else:
            for heat in range(len(self._chart)):
                if not self._keep_going:
                    return
                self._inject_at_lane_in_heat(racer_id, lane, heat)

    def _inject_at_lane_in_heat(self, racer_id: int, lane: int, heat: int) -> None:
        if self._budget.exhausted():
            self._keep_going = False
            return
        row = self._chart[heat]
        if row is None or racer_id in row or heat in self._injection_heats:
            return
        displaced = row[lane]
        self._injection_heats[lane] = heat
        if displaced is None:
            self._inject_at_lane(racer_id, lane + 1)
        else:
            nheats = len(self._chart)
            for dheat in range(nheats - self._nheats_new, nheats):
                if self._budget.exhausted():
                    self._keep_going = False
                if not self._keep_going:
                    return
                drow = self._chart[dheat]
                if drow is not None and displaced not in drow:
                    self._displacement_heats[lane] = dheat
                    drow[lane] = displaced
                    self._inject_at_lane(racer_id, lane + 1)
                    drow[lane] = None
                    self._displacement_heats[lane] = None
        self._injection_heats[lane] = None

    def _consider_proposal(self, racer_id: int) -> None:
        score = self._score_proposal(racer_id)
        if self._best is None or self._best[0] > score:
            self._best = (
                score,
                list(self._injection_heats),
                list(self._displacement_heats),
            )
            if score == 0:
                self._keep_going = False
        self._budget.proposal()
        if self._budget.exhausted():
            self._keep_going = False

    def _injected_chart(
        self,
        injection: list[int | None],
        displacement: list[int | None],
        racer_id: int,
    ) -> list[Heat | None]:
        chart = deepcopy(self._chart)
        for lane in range(self._nlanes):
            inj = injection[lane]
            if inj is None:
                continue
            if displacement[lane] is not None:
                chart[displacement[lane]][lane] = chart[inj][lane]
            chart[inj][lane] = racer_id
        return chart

    def _score_proposal(self, racer_id: int) -> float:
        """The variance of how often each pair of racers meets in a heat,
        with the racer in each proposed heat. Lower is better."""
        replacements: dict[int, Heat] = {}
        for lane, inj in enumerate(self._injection_heats):
            if inj is not None:
                heat = list(self._chart[inj])
                heat[lane] = racer_id
                replacements[inj] = heat
        # Insertion order matters: the float sums below run in PHP's order.
        matchups: dict[int, dict[int, int]] = {}
        for h, heat in enumerate(self._to_race):
            heat = replacements.get(h, heat)
            if heat is None:
                continue
            for racer1 in heat:
                if racer1 is None:
                    continue
                pairings = matchups.setdefault(racer1, {})
                for racer2 in heat:
                    if racer2 is None or racer1 >= racer2:
                        continue
                    pairings[racer2] = pairings.get(racer2, 0) + 1
        return _matchup_variance(matchups)


def _matchup_variance(matchups: dict[int, dict[int, int]]) -> float:
    """PHP's `compute_matchup_stats()`, with its operations in PHP's order so
    each float comes out the same."""
    nracers = len(matchups)
    if nracers <= 1:
        # PHP returns no variance. The missing value reads as null, and
        # `null == 0` ends the search on this proposal, as a 0 does.
        return 0
    npairs = nracers * (nracers - 1)
    mean = 0
    for pairings in matchups.values():
        for m in pairings.values():
            mean += 2 * m
    mean = mean / npairs
    sigma2 = 0
    for pairings in matchups.values():
        for m in pairings.values():
            sigma2 -= 2 * (2 * m * mean - m * m)
    return mean * mean + sigma2 / npairs
