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
from racing.engine.scheduling.adjuster import (
    INJECTION_TIME_LIMIT,
    OVERALL_TIME_LIMIT,
    DeadlineBudget,
    ScheduleAdjuster,
    SearchBudget,
    n_runs_per_lane,
)
from racing.engine.scheduling.master import HeatRef
from racing.models import HAS_RESULT, HeatLane, Round
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
    """Where the race stands (D34). The running heat is the heat `RaceState`
    names as current. The staged heat is the next unraced heat after it, in
    heat order, or in master-heat order under the master schedule."""

    running: HeatRef | None
    staged: HeatRef | None
    master: bool

    def kept_in(self, round: Round) -> frozenset[int]:
        """The heat numbers of this round that must not change."""
        refs = (self.running, self.staged)
        return frozenset(ref[1] for ref in refs if ref and ref[0] == round.id)

    def pinned(self) -> frozenset[HeatRef]:
        """Under the master schedule, the heats whose master numbers must
        stay first, so the staged heat is still the one after the running
        heat once the event is renumbered."""
        return frozenset(r for r in (self.running, self.staged) if r and self.master)


type _Row = tuple[int, int, int, int, int, bool]
"""round id, heat, lane, racer id, master heat, whether it has a result"""


def _race_position(
    rows: list[_Row], state: RaceState, use_master_sched: bool
) -> _RacePosition:
    if state.current_round_id is None or state.current_heat is None:
        return _RacePosition(None, None, use_master_sched)
    current = (state.current_round_id, state.current_heat)
    # A heat is run once one of its lanes has a result.
    run = {(r, heat) for r, heat, _, _, _, done in rows if done}
    master = {(r, heat): number for r, heat, _, _, number, _ in rows}
    unraced = set(master) - run
    running = current if current in unraced else None
    if use_master_sched:
        number = master.get(current)
        later = [] if number is None else [h for h in unraced if master[h] > number]
        staged = min(later, key=master.get, default=None)
    else:
        later = [h for h in unraced if h[0] == current[0] and h[1] > current[1]]
        staged = min(later, default=None)
    return _RacePosition(running, staged, use_master_sched)


@dataclass(frozen=True)
class _Snapshot:
    """Everything the new chart depends on, read before the search and
    compared again under the lock before the chart is written (Q15)."""

    lane_count: int
    unused_lane_mask: int
    rows: tuple[tuple[int, int, int, bool], ...]  # heat, lane, racer, has result
    unscheduled: tuple[int, ...]
    departed: tuple[int, ...]
    position: _RacePosition

    def chart(self) -> list[list[int | None]]:
        nheats = max((heat for heat, _, _, _ in self.rows), default=0)
        chart: list[list[int | None]] = [
            [None] * self.lane_count for _ in range(nheats)
        ]
        for heat, lane, racer, _ in self.rows:
            chart[heat - 1][lane - 1] = racer
        return chart

    def raced(self) -> dict[int, int]:
        return {heat - 1: heat for heat, _, _, done in self.rows if done}


def _snapshot(round: Round) -> _Snapshot:
    settings = RaceSettings.objects.get(event_id=round.event_id)
    state = RaceState.objects.get(event_id=round.event_id)
    # One query, so the round's heats and the race position agree.
    rows: list[_Row] = [
        (r, heat, lane, racer, number, time is not None or place is not None)
        for r, heat, lane, racer, number, time, place in HeatLane.objects.filter(
            event_id=round.event_id
        )
        .order_by("heat", "lane")
        .values_list(
            "round_id",
            "heat",
            "lane",
            "racer_id",
            "master_heat",
            "finish_time",
            "finish_place",
        )
    ]
    pending = round_adjustments(round)
    return _Snapshot(
        lane_count=settings.lane_count,
        unused_lane_mask=settings.unused_lane_mask,
        rows=tuple(
            (heat, lane, racer, done)
            for r, heat, lane, racer, _, done in rows
            if r == round.id
        ),
        unscheduled=pending.unscheduled,
        departed=pending.departed,
        position=_race_position(rows, state, settings.use_master_sched),
    )


def _adjust(
    round: Round,
    snapshot: _Snapshot,
    racer_id: int | None,
    budget: SearchBudget | None,
    clock: Callable[[], datetime],
) -> ScheduleAdjuster:
    """The search, as the PHP action runs it, outside any transaction."""
    if not snapshot.rows:
        raise RescheduleFailure(Failure.NOCHART, "The round has no schedule.")
    if any(lane > snapshot.lane_count for _, lane, _, _ in snapshot.rows):
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
    chart = snapshot.chart()
    n_runs = n_runs_per_lane(chart)
    if budget is None:
        budget = DeadlineBudget(
            lambda: clock().timestamp(), INJECTION_TIME_LIMIT // n_runs
        )
    adjuster = ScheduleAdjuster(
        chart,
        snapshot.raced(),
        snapshot.unused_lane_mask,
        snapshot.lane_count,
        n_runs,
        budget,
        kept=[heat - 1 for heat in snapshot.position.kept_in(round)],
    )
    adjuster.remove_departed(snapshot.departed)
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
    rows = HeatLane.objects.filter(round=round)
    fixed = {heat for heat, _, _, done in snapshot.rows if done}
    # As PHP's DELETE, never a row with a result, even one written by a
    # writer that skipped the lock.
    rows.exclude(heat__in=fixed | snapshot.position.kept_in(round)).exclude(
        HAS_RESULT
    ).delete()
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
    after_chart_change(round, keep=snapshot.position.pinned())
    if state.current_round_id == round.id:
        # A current heat that has run can still move down; follow it.
        state.current_heat = renumbered.get(state.current_heat, state.current_heat)
        if adjuster.heats_squeezed_out:
            # Q5: only a committed change stops racing, never a dry run.
            state.racing = False
            state.refused_results = 0
        state.save(update_fields=["current_heat", "racing", "refused_results"])
    Kiosk.objects.filter(event_id=round.event_id).update(reload_requested=True)


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

    With `racer_id`, a racer of the round's event, fit that racer in;
    without it, fit in every passed racer of the roster who has no heat.
    Either way, departed racers become byes and heats of byes are squeezed
    out. Heats with a result, the running heat and the staged heat keep
    their racers, lanes and numbers, and a departed racer stays in the
    running or staged heat. With `dry_run`, return the chart and change
    nothing (Q5).

    Raises RescheduleFailure: `busy` when another reschedule of the event is
    running, or when the round changed under two searches in a row (Q15);
    `nochart` when nothing would change or a racer could not be fitted in;
    `alreadyscheduled` when `racer_id` already has heats in the round;
    `nlanes` when the chart uses lanes past the lane count; `noracer`
    when `racer_id` is not a racer of the round's event. `budget`
    replaces PHP's deadline, for tests.
    """
    if (
        racer_id is not None
        and not Racer.objects.filter(pk=racer_id, event_id=round.event_id).exists()
    ):
        raise RescheduleFailure(Failure.NO_RACER, "No such racer in this event.")
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
            snapshot = _snapshot(round)
            adjuster = _adjust(round, snapshot, racer_id, budget, clock)
            result = Rescheduled(adjuster.chart_debug(), adjuster.timing_keys)
            if dry_run:
                return result
            with transaction.atomic():
                state = lock_race_state(round.event_id)
                if _snapshot(round) != snapshot:
                    continue
                _write(round, state, snapshot, adjuster)
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
