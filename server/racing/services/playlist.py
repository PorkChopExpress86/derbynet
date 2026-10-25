"""The playlist and its operator gate (#25, owner decision D12).

Ported from `website/inc/playlist.inc` and `website/ajax/action.playlist.start.inc`,
with the gate PHP lacks. When a round ends and the next playlist step waits
for the operator, the next round is prepared and scheduled and its first
heat selected, racing stays off, and the race state holds an intermission
naming the waiting step and the round that ended. `start_waiting_step()` is
the one call that ends the intermission and turns racing on. A step that
does not wait behaves as PHP does.

#27 calls `round_ended` when its heat advance finds the round complete, and
#34 calls `start_playlist` for `playlist.start`. Choosing finalists reads
the standings (#26), so each call takes a `FinalistsFor`.
"""

from collections.abc import Callable, Sequence
from datetime import timedelta
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from racing.models import HeatLane, PlaylistEntry, Round
from racing.services.rounds import (
    make_new_round,
    prepare_new_round,
    round_is_complete,
    write_roster,
)
from racing.services.schedule import ScheduleFailure, generate_schedule, unschedule
from roster.models import Racer
from roster.services.groups import HAS_RESULT

FinalistsFor = Callable[[PlaylistEntry, Round], Sequence[Racer]]
"""The racers of a step's round, in roster order, when it has no roster yet."""

PLAYLIST_SCENE_DELAY = timedelta(seconds=10)


class Failure(StrEnum):
    NO_INTERMISSION = "no-intermission"
    NO_RACERS = "no_racers"


class PlaylistFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def _round_of(entry: PlaylistEntry) -> Round | None:
    return Round.objects.filter(
        group_id=entry.group_id, number=entry.round_number
    ).first()


def _first_unraced_heat(round: Round) -> int | None:
    return (
        HeatLane.objects.filter(round=round)
        .exclude(HAS_RESULT)
        .order_by("heat")
        .values_list("heat", flat=True)
        .first()
    )


def _select(state: RaceState, round: Round) -> None:
    """Make the round's first unraced heat current, with racing off. With no
    such heat the current heat stays, as `advance_heat` leaves it."""
    heat = _first_unraced_heat(round)
    state.racing = False
    state.refused_results = 0
    if heat is not None:
        state.current_round = round
        state.current_group_id = round.group_id
        state.current_heat = heat


def _racing_on(state: RaceState) -> None:
    """`set_racing_state(1)`: only with a current heat that has lanes and no
    result yet. Replay `START` belongs to #27."""
    lanes = HeatLane.objects.filter(
        round_id=state.current_round_id, heat=state.current_heat
    )
    if lanes.exists() and not lanes.filter(HAS_RESULT).exists():
        state.racing = True
        state.refused_results = 0


def _activate(state: RaceState, entry: PlaylistEntry, finalists_for: FinalistsFor):
    """`activate_playlist_entry()`: the step's round with a roster and a
    chart, its first heat selected."""
    round = _round_of(entry) or make_new_round(entry.group, entry.round_number)
    if not round.roster.exists():
        write_roster(round, finalists_for(entry, round))
    settings = RaceSettings.objects.get(event_id=entry.event_id)
    if (
        entry.n_times_per_lane > 0
        and settings.lane_count > 0
        and not HeatLane.objects.filter(round=round).exists()
    ):
        try:
            generate_schedule(round, entry.n_times_per_lane)
        except ScheduleFailure:
            pass  # PHP's schedule_one_round writes no chart and reports nothing.
        state.refresh_from_db()
    _select(state, round)
    return round


def _next_entry(entry: PlaylistEntry) -> PlaylistEntry | None:
    return (
        PlaylistEntry.objects.filter(event_id=entry.event_id, seq__gt=entry.seq)
        .order_by("seq")
        .first()
    )


def start_playlist(event, finalists_for: FinalistsFor) -> Round | None:
    """`playlist.start`: activate the first step whose round is not done,
    then turn racing on. An open intermission closes."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        for entry in PlaylistEntry.objects.filter(event=event).order_by("seq"):
            round = _round_of(entry)
            if (
                round is None
                or not HeatLane.objects.filter(round=round).exists()
                or not round_is_complete(round)
            ):
                round = _activate(state, entry, finalists_for)
                state.waiting_entry = None
                state.ended_round = None
                _racing_on(state)
                state.save()
                return round
        return None


def round_ended(round: Round, finalists_for: FinalistsFor) -> None:
    """`perform_playlist_after_round()`, with the operator gate. The caller
    has turned racing off, as `advance_heat` does before it runs the hook.

    If the next step's round is already raced, the next step after it is
    tried instead. A next step that waits is prepared and selected and opens
    the intermission; otherwise the step runs at once and racing turns back
    on if the ended step says `continue_racing`. A finish scene still shows
    while a step waits.
    """
    with transaction.atomic():
        state = lock_race_state(round.event_id)
        if RaceSettings.objects.get(event_id=round.event_id).use_master_sched:
            return
        _after_round(state, round, round, finalists_for)
        state.save()


def _after_round(
    state: RaceState, round: Round, ended: Round, finalists_for: FinalistsFor
):
    entry = PlaylistEntry.objects.filter(
        event_id=round.event_id, group_id=round.group_id, round_number=round.number
    ).first()
    if entry is None:
        return
    after = _next_entry(entry)
    waits = False
    if after is not None:
        next_round = _round_of(after)
        if (
            next_round is not None
            and HeatLane.objects.filter(round=next_round).exists()
            and round_is_complete(next_round)
        ):
            _after_round(state, next_round, ended, finalists_for)
            return
        _activate(state, after, finalists_for)
        waits = after.waits_for_operator
        if waits:
            state.waiting_entry = after
            state.ended_round = ended
    if entry.scene_at_finish_id:
        state.playlist_scene_id = entry.scene_at_finish_id
        state.playlist_scene_at = timezone.now() + PLAYLIST_SCENE_DELAY
    elif entry.continue_racing and not waits:
        _racing_on(state)


def start_waiting_step(event) -> Round:
    """End the intermission and race the waiting step's round.

    Fails with `no_racers` when the round has no heats, which is how a final
    with fewer than 2 racers stays (D28); the intermission then stays open.
    Applying the racing scene belongs to #27 and #34.
    """
    with transaction.atomic():
        state = lock_race_state(event.id)
        if state.waiting_entry_id is None:
            raise PlaylistFailure(
                Failure.NO_INTERMISSION, "No playlist step is waiting."
            )
        round = _round_of(state.waiting_entry)
        if round is None or not HeatLane.objects.filter(round=round).exists():
            raise PlaylistFailure(
                Failure.NO_RACERS, "The final needs at least 2 racers."
            )
        state.waiting_entry = None
        state.ended_round = None
        _select(state, round)
        _racing_on(state)
        state.save()
        return round


def rebuild_waiting_round(event, finalists_for: FinalistsFor) -> bool:
    """Owner decision D27: after a result in the ended round changes, make the
    waiting round's roster and chart again, unless one of its heats has a
    result. Returns whether it rebuilt them."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        if state.waiting_entry_id is None:
            return False
        entry = state.waiting_entry
        round = _round_of(entry)
        if round is not None:
            if HeatLane.objects.filter(round=round).filter(HAS_RESULT).exists():
                return False
            unschedule(round)
            prepare_new_round(entry.group, entry.round_number)
        _activate(state, entry, finalists_for)
        state.save()
        return True
