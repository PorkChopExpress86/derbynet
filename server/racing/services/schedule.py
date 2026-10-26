"""Writing and deleting a round's heat chart (#22).

`schedule.generate` and `schedule.unschedule` call these. Permission checks
belong to the handler and the view.
"""

from dataclasses import dataclass
from enum import StrEnum

from django.db import transaction

from core.models import RaceSettings, RaceState
from racing.engine.scheduling.lanes import count_one_bits
from racing.engine.scheduling.rough import CantFulfill, max_runs_per_lane
from racing.engine.scheduling.round import TooFewRacers, make_chart
from racing.models import HAS_RESULT, HeatLane, RosterEntry, Round
from racing.services.master import reassign_master_heats


class Failure(StrEnum):
    NLANES = "nlanes"
    RESULTS_EXIST = "results-exist"
    TOO_FEW_RACERS = "too-few-racers"
    CANT_FULFILL = "cant-fulfill"
    BUSY = "busy"


class ScheduleFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


@dataclass(frozen=True)
class _Inputs:
    """Everything the chart depends on, read before the engine runs and
    compared again under the lock before the chart is written."""

    lane_count: int
    unused_lane_mask: int
    max_runs_per_car: int
    rotation: bool
    roster: tuple[int, ...]
    results_exist: bool

    @property
    def nlanes(self) -> int:
        return self.lane_count - count_one_bits(self.unused_lane_mask)


def _read_inputs(round: Round) -> _Inputs:
    settings = RaceSettings.objects.get(event_id=round.event_id)
    order = ("racer__car_number", "id") if settings.rotation_schedule else ("id",)
    roster = RosterEntry.objects.filter(
        round=round, racer__passed_inspection=True
    ).order_by(*order)
    return _Inputs(
        lane_count=settings.lane_count,
        unused_lane_mask=settings.unused_lane_mask,
        max_runs_per_car=settings.max_runs_per_car,
        rotation=settings.rotation_schedule,
        roster=tuple(roster.values_list("racer_id", flat=True)),
        results_exist=HeatLane.objects.filter(round=round).filter(HAS_RESULT).exists(),
    )


def _cant_fulfill_description(inputs: _Inputs, n_times_per_lane: int) -> str:
    if n_times_per_lane < 1:
        return "Choose at least one run per lane."
    nracers = len(inputs.roster)
    supported = (
        1 if inputs.max_runs_per_car else max_runs_per_lane(inputs.nlanes, nracers)
    )
    where = f"{nracers} racers on {inputs.nlanes} lanes"
    if supported == 0:
        return (
            f"The schedule tables have no schedule for {where}."
            " Turn on rotation scheduling to race this round."
        )
    return (
        f"Cannot schedule {n_times_per_lane} runs per lane for {where};"
        f" the most is {supported}. Choose fewer runs per lane,"
        " or turn on rotation scheduling."
    )


def _make_chart(inputs: _Inputs, n_times_per_lane: int) -> list[list[int | None]]:
    if inputs.nlanes <= 0:
        raise ScheduleFailure(Failure.NLANES, "Number of lanes hasn't been determined.")
    if inputs.results_exist:
        raise ScheduleFailure(
            Failure.RESULTS_EXIST, "Race results already exist for this round"
        )
    try:
        return make_chart(
            inputs.roster,
            inputs.nlanes,
            n_times_per_lane,
            max_runs_per_car=inputs.max_runs_per_car,
            rotation=inputs.rotation,
            unused_lane_mask=inputs.unused_lane_mask,
        )
    except TooFewRacers:
        raise ScheduleFailure(
            Failure.TOO_FEW_RACERS,
            "There need to be at least two racers checked in to generate a schedule.",
        ) from None
    except CantFulfill:
        raise ScheduleFailure(
            Failure.CANT_FULFILL, _cant_fulfill_description(inputs, n_times_per_lane)
        ) from None


def generate_schedule(round: Round, n_times_per_lane: int) -> int:
    """Replace the round's chart and return its number of heats.

    Raises ScheduleFailure and leaves the old chart in place on any failure.
    The engine runs outside the transaction, on a snapshot of its inputs, so a
    large roster does not hold the race-state lock (#20, "Transactions and
    locks"). If the inputs changed by the time the lock is held, it tries
    once more, then fails with `busy`.

    The recheck holds only if every writer of the roster, of inspection
    status, of the lane settings, and of results takes the same `RaceState`
    lock. On Postgres a writer that skips it can commit between the recheck
    and the write, so the chart is made from stale inputs, or a result
    recorded in that gap is deleted with the old chart.

    Fewer than one run per lane is `cant-fulfill`, after the checks PHP makes
    first. PHP deletes the chart and reports success with no heats.
    """
    for _ in range(2):
        inputs = _read_inputs(round)
        chart = _make_chart(inputs, n_times_per_lane)
        with transaction.atomic():
            RaceState.objects.select_for_update().get(event_id=round.event_id)
            if _read_inputs(round) != inputs:
                continue
            HeatLane.objects.filter(round=round).delete()
            HeatLane.objects.bulk_create(
                HeatLane(
                    event_id=round.event_id,
                    round=round,
                    heat=heat,
                    lane=lane,
                    racer_id=racer_id,
                )
                for heat, racers in enumerate(chart, start=1)
                for lane, racer_id in enumerate(racers, start=1)
                if racer_id is not None
            )
            after_chart_change(round)
        return len(chart)
    raise ScheduleFailure(
        Failure.BUSY,
        "The roster or the results changed while the schedule was made. Try again.",
    )


def unschedule(round: Round) -> int:
    """Delete the round's chart, results included. Returns the rows deleted."""
    with transaction.atomic():
        RaceState.objects.select_for_update().get(event_id=round.event_id)
        deleted, _ = HeatLane.objects.filter(round=round).delete()
        after_chart_change(round)
    return deleted


def after_chart_change(round: Round, keep=()) -> None:
    """Runs inside the transaction of every change to a round's chart.

    PHP follows each one with `assign_master_heats()`, then under the master
    schedule `advance_heat(current, 'next-up')`, and `schedule.unschedule`
    also cancels the round if it is current. #27 adds the last two here.
    `keep` holds heats whose master numbers stay, as `reassign_master_heats`
    describes.
    """
    reassign_master_heats(round.event, keep)
