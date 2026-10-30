"""The playlist and its operator gate (#25, owner decision D12).

Ported from `website/inc/playlist.inc` and `website/ajax/action.playlist.start.inc`,
with the gate PHP lacks. When a round ends and the next playlist step waits
for the operator, the next round is prepared and scheduled and its first
heat selected, racing stays off, and the race state holds an intermission
naming the waiting step and the round that ended. `start_waiting_step()`
ends the intermission and turns racing on; `skip_waiting_step()` ends it
by passing over the waiting step. A step that does not wait behaves as
PHP does.

#27 calls `round_ended` when its heat advance finds the round complete, and
#34 calls `start_playlist` for `playlist.start`. Choosing finalists reads
the standings (#26), so each call takes a `FinalistsFor`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from kiosks.models import Scene
from kiosks.services import apply_scene
from racing.engine import heat_state as hs
from racing.models import HAS_RESULT, HeatLane, PlaylistEntry, Round
from racing.services import heats, schedule
from racing.services.rounds import (
    Entrants,
    make_new_round,
    prepare_new_round,
    record_cut,
    round_is_complete,
    write_roster,
)
from racing.services.schedule import ScheduleFailure, generate_schedule, unschedule
from roster.models import Group, GroupConstituent

FinalistsFor = Callable[[PlaylistEntry, Round], Entrants]
"""The racers of a step's round, in roster order, when it has no roster
yet, with the cut's concerns."""

PLAYLIST_SCENE_DELAY = timedelta(seconds=10)


class Failure(StrEnum):
    NO_INTERMISSION = "no-intermission"


class PlaylistFailure(Exception):
    """`no-intermission`, or the `ScheduleFailure` code of a waiting round
    that cannot be scheduled."""

    def __init__(self, code: Failure | schedule.Failure, description: str):
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
    """`advance_heat` to the round's first unraced heat, as
    `activate_playlist_entry()` does. With no such heat, racing turns off
    and the current heat stays."""
    heat = _first_unraced_heat(round)
    if heat is None:
        heats.transition(state, hs.RacingOff())
    else:
        heats.transition(state, hs.Advance(heat, round_id=round.id))


def _racing_on(state: RaceState, by: hs.By = hs.By.PLAYLIST) -> None:
    """`set_racing_state(1)`: only with a current heat that has lanes and no
    result yet."""
    heats.transition(state, hs.RacingOn(by))


def _activate(
    state: RaceState,
    entry: PlaylistEntry,
    finalists_for: FinalistsFor,
    select: bool = True,
):
    """`activate_playlist_entry()`: the step's round with a roster and a
    chart, its first heat selected unless `select` is false."""
    round = _round_of(entry) or make_new_round(entry.group, entry.round_number)
    if not round.roster.exists():
        write_roster(round, finalists_for(entry, round).racers)
        record_cut(round, entry.bucket_limit, entry.bucketed)
    if not HeatLane.objects.filter(round=round).exists():
        # PHP's schedule_one_round writes no chart and reports nothing.
        # `start_waiting_step` reports the failure.
        _schedule(round, entry)
        state.refresh_from_db()
    if select:
        _select(state, round)
    return round


def _schedule(round: Round, entry: PlaylistEntry) -> ScheduleFailure | None:
    try:
        generate_schedule(round, entry.n_times_per_lane)
    except ScheduleFailure as failure:
        return failure
    return None


def _next_entry(entry: PlaylistEntry) -> PlaylistEntry | None:
    return (
        PlaylistEntry.objects.filter(event_id=entry.event_id, seq__gt=entry.seq)
        .order_by("seq")
        .first()
    )


def _is_raced(entry: PlaylistEntry) -> bool:
    round = _round_of(entry)
    return (
        round is not None
        and HeatLane.objects.filter(round=round).exists()
        and round_is_complete(round)
    )


def start_playlist(event, finalists_for: FinalistsFor) -> Round | None:
    """`playlist.start`: activate the first step whose round is not done,
    then turn racing on. An open intermission closes."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        for entry in PlaylistEntry.objects.filter(event=event).order_by("seq"):
            if not _is_raced(entry):
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
        if _is_raced(after):
            _after_round(state, _round_of(after), ended, finalists_for)
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

    A round with no chart is scheduled now. If that fails, the intermission
    stays open and the failure is raised with `generate_schedule`'s code. A
    final with fewer than 2 racers says so (D28). Racing on applies the
    racing scene.
    """
    with transaction.atomic():
        state = lock_race_state(event.id)
        entry = state.waiting_entry
        if entry is None:
            raise PlaylistFailure(
                Failure.NO_INTERMISSION, "No playlist step is waiting."
            )
        round = _round_of(entry)
        remade = round is None
        if remade:
            round = make_new_round(entry.group, entry.round_number)
        if not HeatLane.objects.filter(round=round).exists():
            failure = _schedule(round, entry)
            if failure is not None:
                raise _waiting_failure(state, entry, remade, failure) from failure
            state.refresh_from_db()
        state.waiting_entry = None
        state.ended_round = None
        _select(state, round)
        _racing_on(state, hs.By.WAITING_STEP)
        state.save()
        return round


def _waiting_failure(
    state: RaceState, entry: PlaylistEntry, remade: bool, failure: ScheduleFailure
) -> PlaylistFailure:
    """A final short of racers, zero included, gets D28's message. A round
    whose roster was deleted during the intermission was remade empty, which
    is not a final short of racers."""
    description = failure.description
    if failure.code == schedule.Failure.TOO_FEW_RACERS:
        if remade:
            description = "The waiting round has no racers in its roster."
        elif state.ended_round is not None and _feeds(state.ended_round, entry):
            description = "The final needs at least 2 racers."
    return PlaylistFailure(failure.code, description)


def skip_waiting_step(event, finalists_for: FinalistsFor) -> Round | None:
    """End the intermission by passing over the waiting step (D28), and
    prepare the next step whose round is not raced, as `round_ended` does.

    If that step waits, the intermission moves to it and still names the
    round that ended. If it does not wait, it starts with racing on. With no
    step left the playlist is done and racing stays off. Returns the next
    step's round, or None. The skipped round keeps its roster.
    """
    with transaction.atomic():
        state = lock_race_state(event.id)
        if state.waiting_entry_id is None:
            raise PlaylistFailure(
                Failure.NO_INTERMISSION, "No playlist step is waiting."
            )
        after = _next_entry(state.waiting_entry)
        while after is not None and _is_raced(after):
            after = _next_entry(after)
        round = None
        if after is not None:
            round = _activate(state, after, finalists_for)
        if after is not None and after.waits_for_operator:
            state.waiting_entry = after
        else:
            state.waiting_entry = None
            state.ended_round = None
            heats.transition(state, hs.RacingOff())
            if after is not None:
                _racing_on(state)
        state.save()
        return round


def _feeds(ended: Round, entry: PlaylistEntry) -> bool:
    """Whether the step's round takes its racers from `ended`: a later round
    of the same group, or an aggregate that draws from that group."""
    if entry.group_id == ended.group_id:
        return entry.round_number > ended.number
    return GroupConstituent.objects.filter(
        aggregate_id=entry.group_id, constituent_id=ended.group_id
    ).exists()


def _same_finalists(round: Round, entry: PlaylistEntry, finalists_for) -> bool:
    """The round has a chart and its roster is the racers the cut picks now,
    in the cut's order, which is the order the schedule reads, so a rebuild
    would make the same roster and chart again."""
    if not HeatLane.objects.filter(round=round).exists():
        return False
    roster = list(round.roster.order_by("id").values_list("racer_id", flat=True))
    return roster == [racer.pk for racer in finalists_for(entry, round).racers]


def rebuild_waiting_round(event, finalists_for: FinalistsFor) -> bool:
    """Owner decision D27: after a result in the ended round changes, make the
    waiting round's roster and chart again, when the waiting round draws its
    racers from the ended round, none of its heats has a result, and the cut
    now picks other racers. Returns whether it rebuilt them. A current heat
    outside the waiting round, as during a rerun of the ended round, stays
    current."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        if state.waiting_entry_id is None or state.ended_round_id is None:
            return False
        entry = state.waiting_entry
        if not _feeds(state.ended_round, entry):
            return False
        round = _round_of(entry)
        select = state.current_round_id is None or (
            round is not None and state.current_round_id == round.id
        )
        if round is not None:
            if HeatLane.objects.filter(round=round).filter(HAS_RESULT).exists():
                return False
            if _same_finalists(round, entry, finalists_for):
                return False
            unschedule(round)
            prepare_new_round(entry.group, entry.round_number)
            state.refresh_from_db()
        _activate(state, entry, finalists_for, select)
        state.save()
        return True


# ----- editing the playlist (#34) ----------------------------------------------


@dataclass(frozen=True)
class StepFields:
    """What `playlist.add` and `playlist.edit` set on a step. Zero runs per
    lane, PHP's -1, means the step never schedules its round."""

    bucket_limit: int = 0
    bucketed: bool = False
    n_times_per_lane: int = 0
    scene_at_finish: Scene | None = None
    continue_racing: bool = False


def add_step(
    event, group, round_number: int, fields: StepFields, waits: bool = False
) -> PlaylistEntry:
    """`playlist.add`: a step after the others. A step from the legacy API
    does not wait for the operator, as PHP has no gate (D12)."""
    with transaction.atomic():
        lock_race_state(event.id)
        last = PlaylistEntry.objects.filter(event=event).aggregate(last=Max("seq"))
        return PlaylistEntry.objects.create(
            event=event,
            seq=(last["last"] or 0) + 1,
            group=group,
            round_number=round_number,
            waits_for_operator=waits,
            **vars(fields),
        )


def queue_entry(step: PlaylistEntry) -> dict:
    """The `queue-entry` of `playlist.add`'s reply. PHP's -1 stands for no
    runs per lane and no scene. The round name leads with the class when the
    event has more than one (`use_groups()`)."""
    name = step.group.name
    round_name = f"Round {step.round_number}"
    if Group.objects.filter(event_id=step.event_id).count() > 1:
        round_name = f"{name}, {round_name}"
    return {
        "queueid": step.pk,
        "seq": step.seq,
        "classid": step.group_id,
        "round": step.round_number,
        "bucket_limit": step.bucket_limit,
        "bucketed": int(step.bucketed),
        "n_times_per_lane": step.n_times_per_lane or -1,
        "sceneid_at_finish": step.scene_at_finish_id or -1,
        "continue_racing": int(step.continue_racing),
        "class": name,
        "roundname": round_name,
    }


def edit_step(step: PlaylistEntry | None, fields: StepFields) -> None:
    if step is None:
        return
    with transaction.atomic():
        lock_race_state(step.event_id)
        PlaylistEntry.objects.filter(pk=step.pk).update(**vars(fields))


def delete_step(step: PlaylistEntry | None) -> None:
    """A step the intermission waits on cannot go: the intermission closes
    first, as `skip_waiting_step` would, since the race state names it."""
    if step is None:
        return
    with transaction.atomic():
        state = lock_race_state(step.event_id)
        if state.waiting_entry_id == step.pk:
            state.waiting_entry = None
            state.ended_round = None
            state.save(update_fields=["waiting_entry", "ended_round"])
        step.delete()


def order_steps(event, seqs: dict[PlaylistEntry, int]) -> None:
    """`playlist.order`: each step to its new position. Moved through
    negative positions first, as positions are unique."""
    with transaction.atomic():
        lock_race_state(event.id)
        for step, seq in seqs.items():
            PlaylistEntry.objects.filter(pk=step.pk).update(seq=-seq)
        for step, seq in seqs.items():
            PlaylistEntry.objects.filter(pk=step.pk).update(seq=seq)


def apply_due_scene(event, now: datetime) -> bool:
    """`maybe_apply_playlist_scene()`: show the finished step's scene once
    its due time has passed. The kiosk poll (#34) and a periodic check call
    it, so the scene changes even with no kiosk polling. Returns whether it
    applied the scene. The polls call it every few seconds, so the lock is
    taken only when a scene is due."""
    due = RaceState.objects.filter(event=event, playlist_scene_at__lt=now)
    if not due.exists():
        return False
    with transaction.atomic():
        state = lock_race_state(event.id)
        if state.playlist_scene_at is None or not state.playlist_scene_at < now:
            return False
        state.playlist_scene_at = None
        if state.playlist_scene_id is not None:
            apply_scene(state, state.playlist_scene_id)
        state.save()
        return True
