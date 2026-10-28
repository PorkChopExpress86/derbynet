"""Adding a car to a round in progress from check-in (owner decision D36).

When a racer passes check-in while a round of the racer's group is running,
check-in (#42) asks whether to add the car to that round. Yes fits the car
into the remaining heats through the reschedule service (#24) with no dry
run and no preview. No is recorded on the racer's roster entry for that
round, and after it only the race coordinator can add the car, as the
dashboard (#41) offers.

The rule depends on the answer stored here, so these services take the
caller's permission bits. Every other reschedule, the dashboard's Add to
schedule and the legacy `schedule.reschedule` included, keeps
`RESCHEDULE_PERMISSION` (D16 in #15).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from django.db import transaction
from django.utils import timezone

from accounts.permissions import Perm
from core.models import RaceState
from core.services import lock_race_state
from racing.models import HAS_RESULT, HeatLane, RosterEntry, Round
from racing.services.reschedule import Rescheduled, reschedule
from roster.models import Racer

RESCHEDULE_PERMISSION: Final = Perm.CONTROL_RACE
"""What every reschedule needs, except a check-in Yes (D16)."""

CHECK_IN_ADD_PERMISSION: Final = Perm.CHECK_IN_RACERS | Perm.CONTROL_RACE
"""What adding a car from check-in needs while nobody answered No (D36)."""


class Failure(StrEnum):
    NOT_IN_PROGRESS = "not-in-progress"
    NOT_CHECKED_IN = "not-checked-in"
    ALREADY_SCHEDULED = "alreadyscheduled"
    NOT_AUTHORIZED = "notauthorized"


class CheckInFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def _started(round: Round, state: RaceState) -> bool:
    """A round has started when it is the current round or has a raced
    heat."""
    return (
        state.current_round_id == round.pk
        or HeatLane.objects.filter(round=round).filter(HAS_RESULT).exists()
    )


def _running_round(racer: Racer) -> Round | None:
    """The round of the racer's group that has started, still has an unraced
    heat, and has the racer on its roster, with or without heats."""
    state = RaceState.objects.get(event_id=racer.event_id)
    for round in Round.objects.filter(group_id=racer.group_id).order_by("number", "pk"):
        if (
            RosterEntry.objects.filter(round=round, racer=racer).exists()
            and HeatLane.objects.filter(round=round).exclude(HAS_RESULT).exists()
            and _started(round, state)
        ):
            return round
    return None


def round_in_progress_for(racer: Racer) -> Round | None:
    """The round check-in offers to add the racer's car to: the round of
    the racer's group that has started and still has an unraced heat, when
    the racer is on its roster with no heats there. Otherwise None."""
    round = _running_round(racer)
    if round is None or HeatLane.objects.filter(round=round, racer=racer).exists():
        return None
    return round


@dataclass(frozen=True)
class Added:
    round: Round
    rescheduled: Rescheduled


def add_to_round_in_progress(racer: Racer, granted: int) -> Added:
    """Yes: fit the car into the remaining heats of the round in progress,
    with `reschedule(roundid, racer_id)`, no dry run and no preview.

    `granted` is the caller's permission bits (-1 for all). It needs
    `CHECK_IN_RACERS` or `CONTROL_RACE`, and only `CONTROL_RACE` once check-in
    answered No for this round. Raises `CheckInFailure`: `not-checked-in`,
    `not-in-progress`, `alreadyscheduled` for a car with heats in the round,
    and `notauthorized`. The reschedule's own failures pass through as
    `RescheduleFailure`.
    """
    racer = Racer.objects.get(pk=racer.pk)
    if not racer.passed_inspection:
        raise CheckInFailure(
            Failure.NOT_CHECKED_IN, "The racer has not passed check-in."
        )
    round = _running_round(racer)
    if round is None:
        raise CheckInFailure(
            Failure.NOT_IN_PROGRESS, "No round of the racer's group is in progress."
        )
    if HeatLane.objects.filter(round=round, racer=racer).exists():
        raise CheckInFailure(
            Failure.ALREADY_SCHEDULED, "The car already has heats in this round."
        )
    entry = RosterEntry.objects.get(round=round, racer=racer)
    needed = RESCHEDULE_PERMISSION if entry.left_out_at else CHECK_IN_ADD_PERMISSION
    if not granted & needed:
        raise CheckInFailure(
            Failure.NOT_AUTHORIZED, "Only the race coordinator can add this car now."
        )
    return Added(round, reschedule(round, racer.pk))


def leave_out_of_round_in_progress(racer: Racer, granted: int) -> Round:
    """No: record on the racer's roster entry that the car stays out of the
    round in progress. Needs `CHECK_IN_RACERS` or `CONTROL_RACE`."""
    if not granted & CHECK_IN_ADD_PERMISSION:
        raise CheckInFailure(Failure.NOT_AUTHORIZED, "Not authorized.")
    with transaction.atomic():
        lock_race_state(racer.event_id)
        round = round_in_progress_for(racer)
        if round is None:
            raise CheckInFailure(
                Failure.NOT_IN_PROGRESS,
                "No round of the racer's group is in progress for this car.",
            )
        RosterEntry.objects.filter(round=round, racer=racer).update(
            left_out_at=timezone.now()
        )
        return round
