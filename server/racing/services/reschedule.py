"""Rescheduling a round in progress (#24): `schedule.reschedule`.

The race coordinator previews a late racer's place with a dry run, then
presses Add to schedule, which runs the same call for real (D32). Nothing
else calls this service, so passing check-in never changes a chart by
itself. The same call removes departed racers and squeezes out heats of
byes. The engine's `ScheduleAdjuster` does the search; this module loads the
round, keeps the running heat and the staged heat (D34), and writes the
result. Permission checks belong to the handler and the view.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from kiosks.models import Kiosk
from racing.engine.heat_state import RacingOff
from racing.engine.scheduling.adjuster import (
    INJECTION_TIME_LIMIT,
    OVERALL_TIME_LIMIT,
    DeadlineBudget,
    ScheduleAdjuster,
    SearchBudget,
    n_runs_per_lane,
)
from racing.engine.scheduling.master import HeatRef
from racing.models import (
    HAS_RESULT,
    EmptiedHeat,
    HeatLane,
    LaneClosure,
    RosterEntry,
    Round,
)
from racing.services import heats
from racing.services.master import held_emptied_heats
from racing.services.rounds import round_adjustments
from racing.services.schedule import after_chart_change
from roster.models import Racer

GUARD_EXPIRY = timedelta(seconds=2 * OVERALL_TIME_LIMIT)
"""A guard older than this is from a request that died without clearing it."""

_UNDERWAY = "Another schedule adjustment is already underway"


class Failure(StrEnum):
    NOCHART = "nochart"
    BUSY = "busy"
    NLANES = "nlanes"
    ALREADY_SCHEDULED = "alreadyscheduled"
    NO_RACER = "noracer"
    NOT_ON_ROSTER = "notonroster"


class RescheduleFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


@dataclass(frozen=True)
class Rescheduled:
    """The new chart as PHP's reply `chart` shows it, and the keys of PHP's
    `timing.internal`."""

    chart: list[str | None]
    timing_keys: list[str]


@dataclass(frozen=True)
class _RacePosition:
    """Where the race stands (D34). The current heat is the heat `RaceState`
    names; it is the running heat while none of its lanes has a result. The
    staged heat is the next unraced heat after it in the same round, or under
    the master schedule the unraced heat with the next master number, in any
    round (D39). A running or staged heat whose cars have all left has no
    lanes, and an `EmptiedHeat` keeps its number and master number, so it
    stays the running or the staged heat until the race passes it (D39)."""

    current: HeatRef | None
    running: HeatRef | None
    staged: HeatRef | None
    master: bool
    emptied: frozenset[HeatRef] = frozenset()  # held emptied heats (D39)

    def kept_in(self, round: Round) -> frozenset[int]:
        """The heat numbers of this round that must not change: the running
        heat, the staged heat, and every held emptied heat, whichever round
        is current (D39)."""
        refs = (self.running, self.staged, *self.emptied)
        return frozenset(ref[1] for ref in refs if ref and ref[0] == round.id)

    def pinned(self, round: Round, renumbered: dict[int, int]) -> frozenset[HeatRef]:
        """Under the master schedule, the heats whose master numbers must stay
        once the event is renumbered, so `next` goes from the current heat to
        the staged heat: the current heat, run or not, since master numbering
        renumbers a heat with a result in only some lanes, and the staged
        heat. `renumbered` is this round's heat renumbering, which can move a
        current heat that has run."""
        if not (self.master and self.current):
            return frozenset()
        r, heat = self.current
        current = (r, renumbered.get(heat, heat) if r == round.id else heat)
        return frozenset({current, *((self.staged,) if self.staged else ())})


type _Row = tuple[int, int, int, int, bool, int]
"""round id, heat, lane, racer id, whether it has a result, master heat"""

type _Emptied = tuple[int, int, int]
"""round id, heat, master heat of an `EmptiedHeat`"""


def _race_position(
    rows: list[_Row],
    emptied: tuple[_Emptied, ...],
    state: RaceState,
    use_master_sched: bool,
) -> _RacePosition:
    held = frozenset((r, heat) for r, heat, _ in emptied)
    if state.current_round_id is None or state.current_heat is None:
        return _RacePosition(None, None, None, use_master_sched, held)
    current = (state.current_round_id, state.current_heat)
    number = {(r, heat): n for r, heat, _, _, _, n in rows}
    number |= {(r, heat): n for r, heat, n in emptied}
    # A heat is run once one of its lanes has a result.
    run = {(r, heat) for r, heat, _, _, done, _ in rows if done}
    unraced = number.keys() - run
    running = current if current in unraced else None
    if use_master_sched:
        after = number.get(current, 0)
        later = [
            (n, ref)
            for ref, n in number.items()
            if ref in unraced and ref != current and n > after
        ]
        staged = min(later, default=(None, None))[1]
    else:
        staged = min(
            (h for h in unraced if h[0] == current[0] and h[1] > current[1]),
            default=None,
        )
    return _RacePosition(current, running, staged, use_master_sched, held)


@dataclass(frozen=True)
class _Snapshot:
    """Everything the new chart depends on, read before the search and
    compared again under the lock before the chart is written (Q15)."""

    lane_count: int
    unused_lane_mask: int
    closed_mask: int  # the lanes closed in this round, reopened or not (#108)
    rows: tuple[tuple[int, int, int, bool], ...]  # heat, lane, racer, has result
    unscheduled: tuple[int, ...]
    departed: tuple[int, ...]
    emptied: tuple[_Emptied, ...]
    on_roster: bool | None  # whether the racer to fit in is on the roster
    position: _RacePosition

    def chart(self, kept: frozenset[int] = frozenset()) -> list[list[int | None]]:
        """The heats by lane, through the last heat with lanes or the last
        kept heat, so a kept heat whose cars all left keeps its place."""
        nheats = max((heat for heat, _, _, _ in self.rows), default=0)
        nheats = max(nheats, *kept) if kept else nheats
        chart: list[list[int | None]] = [
            [None] * self.lane_count for _ in range(nheats)
        ]
        for heat, lane, racer, _ in self.rows:
            chart[heat - 1][lane - 1] = racer
        return chart

    def raced(self) -> dict[int, int]:
        return {heat - 1: heat for heat, _, _, done in self.rows if done}


def _snapshot(round: Round, racer_id: int | None = None) -> _Snapshot:
    settings = RaceSettings.objects.get(event_id=round.event_id)
    state = RaceState.objects.get(event_id=round.event_id)
    # One query, so the round's heats and the race position agree.
    rows: list[_Row] = [
        (r, heat, lane, racer, time is not None or place is not None, master)
        for r, heat, lane, racer, time, place, master in HeatLane.objects.filter(
            event_id=round.event_id
        )
        .order_by("heat", "lane")
        .values_list(
            "round_id",
            "heat",
            "lane",
            "racer_id",
            "finish_time",
            "finish_place",
            "master_heat",
        )
    ]
    emptied = tuple(
        sorted((r, heat, n) for _, r, heat, n in held_emptied_heats(round.event))
    )
    pending = round_adjustments(round)
    closed_mask = 0
    for mask in LaneClosure.objects.filter(round=round).values_list("mask", flat=True):
        closed_mask |= mask
    return _Snapshot(
        lane_count=settings.lane_count,
        unused_lane_mask=settings.unused_lane_mask,
        closed_mask=closed_mask,
        rows=tuple(
            (heat, lane, racer, done)
            for r, heat, lane, racer, done, _ in rows
            if r == round.id
        ),
        unscheduled=pending.unscheduled,
        departed=pending.departed,
        emptied=emptied,
        on_roster=None
        if racer_id is None
        else RosterEntry.objects.filter(round=round, racer_id=racer_id).exists(),
        position=_race_position(rows, emptied, state, settings.use_master_sched),
    )


def _adjust(
    round: Round,
    snapshot: _Snapshot,
    racer_id: int | None,
    budget: SearchBudget | None,
    clock: Callable[[], datetime],
    close: int = 0,
) -> ScheduleAdjuster:
    """The search, as the PHP action runs it, outside any transaction.

    With `close`, a lane mask, close those lanes instead of fitting in late
    racers (#108), and a chart that needs no change is no failure. A late
    racer left out of the round stays out (D36)."""
    if snapshot.on_roster is False:
        raise RescheduleFailure(
            Failure.NOT_ON_ROSTER,
            "The racer is not on this round's roster. Add the racer to the"
            " roster first.",
        )
    if not snapshot.rows:
        raise RescheduleFailure(Failure.NOCHART, "The round has no schedule.")
    if any(heat < 1 for heat, _, _, _ in snapshot.rows):
        # Only an imported database can hold one; every writer here counts
        # heats from 1.
        raise RescheduleFailure(
            Failure.NOCHART, "The schedule has a heat 0, which cannot be adjusted."
        )
    if any(not 1 <= lane <= snapshot.lane_count for _, lane, _, _ in snapshot.rows):
        raise RescheduleFailure(
            Failure.NLANES,
            "The schedule uses lanes the track no longer has. Restore the lane"
            " count or schedule the round again.",
        )
    if racer_id is not None and any(r == racer_id for _, _, r, _ in snapshot.rows):
        # PHP would fit the racer in a second time, as after a double press.
        raise RescheduleFailure(
            Failure.ALREADY_SCHEDULED, "The racer already has heats in this round."
        )
    kept = snapshot.position.kept_in(round)
    chart = snapshot.chart(kept)
    # A lane closed in this round still holds the cars that raced there.
    n_runs = n_runs_per_lane(chart, snapshot.closed_mask)
    if budget is None:
        budget = DeadlineBudget(
            lambda: clock().timestamp(), INJECTION_TIME_LIMIT // n_runs
        )
    adjuster = ScheduleAdjuster(
        chart,
        snapshot.raced(),
        # A lane closed in this round stays closed for the rest of it, even
        # once it is reopened for later rounds (D37).
        snapshot.unused_lane_mask | snapshot.closed_mask,
        snapshot.lane_count,
        n_runs,
        budget,
        kept=[heat - 1 for heat in kept],
    )
    adjuster.remove_departed(snapshot.departed)
    if close:
        adjuster.close_lanes(close)
        return adjuster
    started = clock()
    for late in (racer_id,) if racer_id is not None else snapshot.unscheduled:
        if (clock() - started).total_seconds() < OVERALL_TIME_LIMIT:
            adjuster.inject(late)
    adjuster.squeeze_out_byes()
    if adjuster.updated_chart() is None:
        raise RescheduleFailure(Failure.NOCHART, "Unable to build new schedule")
    return adjuster


def _write(
    round: Round, state: RaceState, snapshot: _Snapshot, adjuster: ScheduleAdjuster
):
    if adjuster.updated_chart() is None:
        return  # a lane closure that changes no heat
    rows = HeatLane.objects.filter(round=round)
    fixed = {heat for heat, _, _, done in snapshot.rows if done}
    # As PHP's DELETE, never a row with a result, even one written by a
    # writer that skipped the lock.
    held = adjuster.kept_heats()
    _hold_emptied_heats(round, snapshot, held)
    rows.exclude(heat__in=fixed | held.keys()).exclude(HAS_RESULT).delete()
    # A departed racer's lane in a kept heat runs empty (D37).
    for heat, racers in held.items():
        vacated = [lane for lane, racer in enumerate(racers, start=1) if racer is None]
        rows.filter(heat=heat, lane__in=vacated).exclude(HAS_RESULT).delete()
    renumbered = adjuster.renumbering_map()
    # Heats only move down, so in this order no heat moves onto one that has
    # not moved yet.
    for old, new in sorted(renumbered.items()):
        rows.filter(heat=old).update(heat=new)
    HeatLane.objects.bulk_create(
        HeatLane(
            event_id=round.event_id,
            round=round,
            heat=heat,
            lane=lane,
            racer_id=racer_id,
        )
        for heat, racers in enumerate(adjuster.updated_chart(), start=1)
        if racers is not None
        for lane, racer_id in enumerate(racers, start=1)
        if racer_id is not None
    )
    after_chart_change(
        round, keep=snapshot.position.pinned(round, renumbered), advance=False
    )
    if state.current_round_id == round.id:
        # A current heat that has run can still move down; follow it.
        state.current_heat = renumbered.get(state.current_heat, state.current_heat)
        state.save(update_fields=["current_heat"])
        if adjuster.heats_squeezed_out:
            # Q5: only a committed change stops racing, never a dry run.
            heats.transition(state, RacingOff())
    Kiosk.objects.filter(event_id=round.event_id).update(reload_requested=True)


def _hold_emptied_heats(
    round: Round, snapshot: _Snapshot, held: dict[int, list[int | None]]
) -> None:
    """Record each kept heat of the round that this write leaves with no
    cars, with its master number (D39). `after_chart_change` then deletes
    the emptied heats the race has passed."""
    number = {(r, heat): n for r, heat, n in snapshot.emptied}
    lanes = HeatLane.objects.filter(round=round, heat__in=held.keys())
    number |= {
        (round.id, heat): n for heat, n in lanes.values_list("heat", "master_heat")
    }
    for heat, racers in held.items():
        if all(racer is None for racer in racers):
            EmptiedHeat.objects.update_or_create(
                event_id=round.event_id,
                round=round,
                heat=heat,
                defaults={"master_heat": number.get((round.id, heat), 0)},
                create_defaults={
                    "master_heat": number.get((round.id, heat), 0),
                    "held_at": timezone.now(),
                },
            )


def _take_guard(round: Round, now: datetime, held: datetime | None = None) -> bool:
    """Set `reschedule_started_at` to `now` if it is free or expired, or, to
    renew it, if it is still `held`."""
    free = (
        Q(reschedule_started_at=held)
        if held
        else Q(reschedule_started_at__isnull=True)
        | Q(reschedule_started_at__lte=now - GUARD_EXPIRY)
    )
    return bool(
        RaceState.objects.filter(event_id=round.event_id)
        .filter(free)
        .update(reschedule_started_at=now)
    )


def reschedule(
    round: Round,
    racer_id: int | None = None,
    dry_run: bool = False,
    *,
    budget: SearchBudget | None = None,
    clock: Callable[[], datetime] = timezone.now,
) -> Rescheduled:
    """Adjust the round's unraced heats and return the new chart.

    With `racer_id`, a racer on the round's roster, fit that racer in;
    without it, fit in every passed racer of the roster who has no heat.
    Either way, departed racers become byes and heats of byes are squeezed
    out. Heats with a result, the running heat and the staged heat keep
    their racers, lanes and numbers, except that a departed racer's lane in
    the running or staged heat runs empty (D37), even when no car is left in
    it (D39). With `dry_run`, return the chart and change nothing (Q5).

    Raises RescheduleFailure: `busy` when another reschedule of the event is
    running, or when the round changed under two searches in a row (Q15);
    `nochart` when nothing would change, a racer could not be fitted in, or
    the chart has a heat 0;
    `alreadyscheduled` when `racer_id` already has heats in the round;
    `nlanes` when the chart uses lane 0 or lanes past the lane count; `noracer`
    when `racer_id` is not a racer of the round's event; `notonroster` when
    the racer is not on the round's roster, as a racer of another group or
    one not picked for a final (D39). `budget` replaces PHP's deadline, for
    tests.
    """
    if (
        racer_id is not None
        and not Racer.objects.filter(pk=racer_id, event_id=round.event_id).exists()
    ):
        raise RescheduleFailure(Failure.NO_RACER, "No such racer in this event.")
    return adjust_round(round, racer_id, dry_run, budget=budget, clock=clock)


def adjust_round(
    round: Round,
    racer_id: int | None = None,
    dry_run: bool = False,
    *,
    close: int = 0,
    also: Callable[[RaceState], None] | None = None,
    budget: SearchBudget | None = None,
    clock: Callable[[], datetime] = timezone.now,
) -> Rescheduled:
    """`reschedule`, under its guard, snapshot, recheck and retry (Q15).
    With `close`, close the lanes of that mask instead of fitting in late
    racers (#108). `also` runs in the transaction that writes the chart,
    after the recheck, with the locked `RaceState`; whatever it raises
    rolls the write back."""
    held = clock()
    if not _take_guard(round, held):
        raise RescheduleFailure(Failure.BUSY, _UNDERWAY)
    try:
        for attempt in range(2):
            if attempt:
                # A second search may run past the first one's guard expiry.
                renewed = clock()
                if not _take_guard(round, renewed, held):
                    raise RescheduleFailure(Failure.BUSY, _UNDERWAY)
                held = renewed
            snapshot = _snapshot(round, racer_id)
            adjuster = _adjust(round, snapshot, racer_id, budget, clock, close)
            result = Rescheduled(adjuster.chart_debug(), adjuster.timing_keys)
            if dry_run:
                return result
            with transaction.atomic():
                state = lock_race_state(round.event_id)
                if _snapshot(round, racer_id) != snapshot:
                    continue
                _write(round, state, snapshot, adjuster)
                if also is not None:
                    also(state)
            return result
        raise RescheduleFailure(
            Failure.BUSY,
            "The heats or the roster changed while the schedule was adjusted."
            " Try again.",
        )
    finally:
        RaceState.objects.filter(
            event_id=round.event_id, reschedule_started_at=held
        ).update(reschedule_started_at=None)
