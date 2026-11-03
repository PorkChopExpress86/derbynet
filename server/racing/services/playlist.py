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
from django.db.models import Max, Min
from django.utils import timezone

from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from kiosks.models import Scene
from kiosks.services import apply_scene
from racing.engine import heat_state as hs
from racing.models import HAS_RESULT, HeatLane, PlaylistEntry, RosterEntry, Round
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
from roster.models import Group, GroupConstituent, GroupConstituentSubgroup

FinalistsFor = Callable[[PlaylistEntry, Round], Entrants]
"""The racers of a step's round, in roster order, when it has no roster
yet, with the cut's concerns."""

PLAYLIST_SCENE_DELAY = timedelta(seconds=10)


class Failure(StrEnum):
    NO_INTERMISSION = "no-intermission"
    # The playlist page's refusals (#44).
    NO_SUCH_GROUP = "no-such-group"
    NO_SUCH_STEP = "no-such-step"
    NO_SUCH_SCENE = "no-such-scene"
    NEEDS_TOP = "needs-top"
    BAD_RUNS = "bad-runs"
    BAD_ROUND = "bad-round"
    STALE_ORDER = "stale-order"


class PlaylistFailure(Exception):
    """`no-intermission`, a refusal of the playlist page, or the
    `ScheduleFailure` code of a waiting round that cannot be scheduled."""

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
    negative positions first, as positions are unique. A step left out
    that holds one of the new positions, as a stale tab's order can name,
    goes after every other step, where PHP would store both at one
    position."""
    with transaction.atomic():
        lock_race_state(event.id)
        for step, seq in seqs.items():
            PlaylistEntry.objects.filter(pk=step.pk).update(seq=-seq)
        steps = PlaylistEntry.objects.filter(event=event)
        last = max([*seqs.values(), steps.aggregate(last=Max("seq"))["last"] or 0])
        taken = steps.filter(seq__in=seqs.values()).order_by("seq")
        for after, step in enumerate(taken, start=last + 1):
            PlaylistEntry.objects.filter(pk=step.pk).update(seq=after)
        for step, seq in seqs.items():
            PlaylistEntry.objects.filter(pk=step.pk).update(seq=seq)


def apply_due_scene(event, now: datetime) -> bool:
    """`maybe_apply_playlist_scene()`: show the finished step's scene once
    its due time has passed. The kiosk polls (`kiosks.polls`) and the legacy
    `query=poll.coordinator` call it, so the scene changes while either a
    kiosk or the PHP coordinator page polls. Returns whether it applied the
    scene. The polls call it every few seconds, so the lock is
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


# ----- the playlist page (#44) ---------------------------------------------------
#
# The page's editor takes ids and posted values, and every rule about stored
# state is checked here, under the `RaceState` lock: the group and scene of
# the event, the step still there, and a final's top N (D43). One user
# action is one call.

MAX_RUNS_PER_LANE = 6
"""The runs per lane select of `playlist.php` offers 1 to 6."""

STOP = 0
NEXT_ROUND = -1
"""`playlist.js`'s "And then" values: 0 stops racing, -1 starts the next
round, and a scene id shows that scene."""


@dataclass(frozen=True)
class StepChoice:
    """What the entry sheet sets. `top` None keeps the stored cut, as
    `playlist.js` posts the stored cut for a round whose roster is built.
    `then` is the "And then" value: `STOP`, `NEXT_ROUND` or a scene id."""

    top: int | None = None
    bucketed: bool = False
    runs: int = 1
    then: int = STOP
    waits: bool = True


@dataclass(frozen=True)
class RemovedStep:
    """A removed step, enough to put it back where it was (Undo). It travels
    in the Undo link's query, so `restore_step` checks it again as it would
    a new step."""

    group_id: int
    round_number: int
    position: int
    choice: StepChoice

    def to_query(self) -> dict[str, int]:
        c = self.choice
        return {
            "group": self.group_id,
            "round": self.round_number,
            "position": self.position,
            "top": c.top or 0,
            "bucketed": int(c.bucketed),
            "runs": c.runs,
            "then": c.then,
            "waits": int(c.waits),
        }

    @classmethod
    def from_query(cls, query) -> RemovedStep | None:
        """The step `to_query` wrote, or None for a query that lacks a field
        or holds one that is not a whole number."""
        values = {}
        for name in cls(0, 0, 0, StepChoice()).to_query():
            try:
                values[name] = int(str(query.get(name, "")).strip())
            except ValueError:
                return None
            if not -(2**31) < values[name] < 2**31:
                return None
        return cls(
            group_id=values["group"],
            round_number=values["round"],
            position=values["position"],
            choice=StepChoice(
                top=values["top"],
                bucketed=bool(values["bucketed"]),
                runs=values["runs"],
                then=values["then"],
                waits=bool(values["waits"]),
            ),
        )


def is_aggregate(group_id: int) -> bool:
    return (
        GroupConstituent.objects.filter(aggregate_id=group_id).exists()
        or GroupConstituentSubgroup.objects.filter(aggregate_id=group_id).exists()
    )


def _cut_rule(round_number: int, aggregate: bool, built: bool) -> bool:
    return (round_number >= 2 or aggregate) and not built


def needs_cut(group_id: int, round_number: int) -> bool:
    """A step whose round picks its racers when it starts: a follow-on round,
    or an aggregate's first round, whose roster is not built yet. Its top N
    is asked for and kept (D43). `playlist.php` shows the cut fields when the
    round does not exist yet."""
    built = RosterEntry.objects.filter(
        round__group_id=group_id, round__number=round_number
    ).exists()
    return _cut_rule(round_number, is_aggregate(group_id), built)


def cuts_needed(event) -> Callable[[int, int], bool]:
    """`needs_cut` for every step of a page, from three queries."""
    aggregates = set(
        GroupConstituent.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    ) | set(
        GroupConstituentSubgroup.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    )
    built = set(
        RosterEntry.objects.filter(event=event)
        .values_list("round__group_id", "round__number")
        .distinct()
    )
    return lambda group_id, number: _cut_rule(
        number, group_id in aggregates, (group_id, number) in built
    )


def _choice_fields(event, choice: StepChoice, stored=None) -> dict:
    if not 1 <= choice.runs <= MAX_RUNS_PER_LANE:
        raise PlaylistFailure(
            Failure.BAD_RUNS,
            f"Runs per lane is 1 to {MAX_RUNS_PER_LANE}.",
        )
    scene = None
    if choice.then > 0:
        scene = Scene.objects.filter(event=event, pk=choice.then).first()
        if scene is None:
            raise PlaylistFailure(Failure.NO_SUCH_SCENE, "That scene is gone.")
    fields = {
        "n_times_per_lane": choice.runs,
        # `read_after_action_select()`: a scene continues racing too, as
        # PHP stores it, and the scene wins when the round ends.
        "scene_at_finish": scene,
        "continue_racing": choice.then != STOP,
        "waits_for_operator": choice.waits,
    }
    if choice.top is not None:
        fields["bucket_limit"] = max(choice.top, 0)
        fields["bucketed"] = choice.bucketed
    elif stored is None:
        fields["bucket_limit"] = 0
        fields["bucketed"] = False
    return fields


def _check_cut(group_id: int, round_number: int, top: int) -> None:
    if needs_cut(group_id, round_number) and top < 1:
        raise PlaylistFailure(
            Failure.NEEDS_TOP,
            "Say how many racers go to this final.",
        )


def _group(event, group_id: int, round_number: int) -> Group:
    group = Group.objects.filter(event=event, pk=group_id).first()
    if group is None:
        raise PlaylistFailure(Failure.NO_SUCH_GROUP, "That group is gone.")
    if not 1 <= round_number <= 99:
        raise PlaylistFailure(Failure.BAD_ROUND, "Pick a round to add.")
    return group


def queue_step(event, group_id: int, round_number: int, choice: StepChoice):
    """Add a round to the end of the playlist from the entry sheet."""
    with transaction.atomic():
        lock_race_state(event.id)
        group = _group(event, group_id, round_number)
        fields = _choice_fields(event, choice)
        _check_cut(group.pk, round_number, fields["bucket_limit"])
        return PlaylistEntry.objects.create(
            event=event,
            seq=_free_seq(event),
            group=group,
            round_number=round_number,
            **fields,
        )


def _step(event, entry_id: int) -> PlaylistEntry:
    step = PlaylistEntry.objects.filter(event=event, pk=entry_id).first()
    if step is None:
        raise PlaylistFailure(
            Failure.NO_SUCH_STEP, "That step is no longer in the playlist."
        )
    return step


def change_step(event, entry_id: int, choice: StepChoice) -> PlaylistEntry:
    """Save the entry sheet of a step: every field `playlist.edit` sets, and
    whether the step waits for the operator (D12)."""
    with transaction.atomic():
        lock_race_state(event.id)
        step = _step(event, entry_id)
        fields = _choice_fields(event, choice, stored=step)
        _check_cut(
            step.group_id,
            step.round_number,
            fields.get("bucket_limit", step.bucket_limit),
        )
        for name, value in fields.items():
            setattr(step, name, value)
        step.save(update_fields=list(fields))
        return step


def set_step_waits(event, entry_id: int, waits: bool) -> PlaylistEntry:
    """The "Wait for operator" switch on a step's card (D12)."""
    with transaction.atomic():
        lock_race_state(event.id)
        step = _step(event, entry_id)
        step.waits_for_operator = waits
        step.save(update_fields=["waits_for_operator"])
        return step


def remove_step(event, entry_id: int) -> RemovedStep:
    """Remove a step, as `playlist.delete` does, and say where it was. A
    step the intermission waits on closes the intermission first."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        step = _step(event, entry_id)
        order = list(
            PlaylistEntry.objects.filter(event=event)
            .order_by("seq")
            .values_list("pk", flat=True)
        )
        removed = RemovedStep(
            group_id=step.group_id,
            round_number=step.round_number,
            position=order.index(step.pk) + 1,
            choice=StepChoice(
                top=step.bucket_limit,
                bucketed=step.bucketed,
                runs=step.n_times_per_lane,
                then=_then_of(step),
                waits=step.waits_for_operator,
            ),
        )
        if state.waiting_entry_id == step.pk:
            state.waiting_entry = None
            state.ended_round = None
            state.save(update_fields=["waiting_entry", "ended_round"])
        step.delete()
        return removed


def _then_of(step: PlaylistEntry) -> int:
    """`append_playlist_entry_li()`: a scene, else the next round if the
    step continues racing, else stop."""
    if step.scene_at_finish_id:
        return step.scene_at_finish_id
    return NEXT_ROUND if step.continue_racing else STOP


def restore_step(event, removed: RemovedStep) -> PlaylistEntry | None:
    """Undo a removal: the step comes back at its old position, or last if
    the playlist is shorter now, checked as `queue_step` checks a new step.
    A step of that round already in the playlist, as a second tap on Undo
    finds, leaves the playlist as it is and returns None. An intermission
    the removal closed stays closed."""
    with transaction.atomic():
        lock_race_state(event.id)
        group = _group(event, removed.group_id, removed.round_number)
        queued = PlaylistEntry.objects.filter(event=event)
        if queued.filter(group=group, round_number=removed.round_number).exists():
            return None
        fields = _choice_fields(event, removed.choice)
        _check_cut(group.pk, removed.round_number, fields["bucket_limit"])
        order = list(queued.order_by("seq").values_list("pk", flat=True))
        step = PlaylistEntry.objects.create(
            event=event,
            seq=_free_seq(event),
            group=group,
            round_number=removed.round_number,
            **fields,
        )
        order.insert(max(removed.position, 1) - 1, step.pk)
        _renumber(event, order)
        step.refresh_from_db()
        return step


def _free_seq(event) -> int:
    """A seq past every one held, negative ones too."""
    held = PlaylistEntry.objects.filter(event=event).aggregate(
        last=Max("seq"), first=Min("seq")
    )
    return max(abs(held["last"] or 0), abs(held["first"] or 0)) + 1


def reorder_steps(event, entry_ids: list[int]) -> None:
    """The playlist in a new order, from a drag or a row's Move up and Move
    down: `entry_ids[i]` gets `seq = i + 1`. The order names every step
    once, or a stale tab's order is refused."""
    with transaction.atomic():
        lock_race_state(event.id)
        _renumber(event, entry_ids)


def _renumber(event, entry_ids) -> None:
    """Renumber the playlist under the lock the caller holds.

    `seq` is unique per event, and SQLite has no deferrable unique constraint,
    so every entry first moves to a seq below `-max(abs(seq))`, which no entry
    holds even when an imported seq is already negative. The second pass then
    never meets a seq that is still taken.
    """
    entries = {
        entry.id: entry
        for entry in PlaylistEntry.objects.select_for_update().filter(event=event)
    }
    if sorted(entry_ids) != sorted(entries):
        raise PlaylistFailure(
            Failure.STALE_ORDER,
            "The playlist changed in another tab. Reload to see it.",
        )
    offset = max((abs(entry.seq) for entry in entries.values()), default=0)
    for position, entry_id in enumerate(entry_ids, start=1):
        PlaylistEntry.objects.filter(id=entry_id).update(seq=-(offset + position))
    for position, entry_id in enumerate(entry_ids, start=1):
        PlaylistEntry.objects.filter(id=entry_id).update(seq=position)
