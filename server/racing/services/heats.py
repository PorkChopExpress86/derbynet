"""The current heat and racing state: `heat.select`, racing on and off,
reruns, reinstates, and the advance after a result.

`transition` carries out one decision of `racing.engine.heat_state` on the
locked `RaceState`: it loads the chart, decides, writes the state, and
performs the effects in PHP's order. Every service that moves the current
heat or turns racing on or off goes through it, inside its own
`transaction.atomic()` that locks `RaceState` first.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Event, RaceSettings, RaceState
from core.services import lock_race_state
from kiosks.services import apply_scene
from racing.engine import heat_state as hs
from racing.engine.results import LaneResult, Posted
from racing.models import HAS_RESULT, EmptiedHeat, HeatLane, Round, TimerStatus
from racing.services import audit, replay
from racing.services.master import held_emptied_heats

type FinalistsFor = Callable
"""`racing.services.playlist.FinalistsFor`."""


class LastHeat:
    """`RaceState.last_heat`: `{"round_id", "heat", "results"}` after a
    result (`available`), plus `"lanes"` after a rerun saved them
    (`recoverable`), each `{"lane", "time", "place"}` so a reinstate brings
    back both (Q4). A last heat imported from PHP keeps PHP's one column in
    `"results"` instead."""

    @staticmethod
    def recorded(ref: hs.HeatRef) -> dict:
        return {"round_id": ref[0], "heat": ref[1], "results": None}

    @staticmethod
    def saved(ref: hs.HeatRef, lanes: Sequence[LaneResult]) -> dict:
        return LastHeat.recorded(ref) | {
            "lanes": [{"lane": r.lane, "time": r.time, "place": r.place} for r in lanes]
        }

    @staticmethod
    def kind(value: dict | None) -> str:
        """The poll's `last-heat`: `none`, `available` or `recoverable`."""
        if value is None:
            return "none"
        if value.get("lanes") is not None or value.get("results") is not None:
            return "recoverable"
        return "available"

    @staticmethod
    def ref(value: dict | None) -> hs.HeatRef | None:
        return None if value is None else (value["round_id"], value["heat"])


def engine_state(state: RaceState) -> hs.State:
    if state.timer_test:
        round_id = hs.TIMER_TEST_ROUND
    else:
        round_id = state.current_round_id
    return hs.State(
        round_id=round_id,
        heat=state.current_heat,
        racing=state.racing,
        refused=state.refused_results,
        intermission=state.waiting_entry_id is not None,
        last_saved=LastHeat.kind(state.last_heat) == "recoverable",
    )


def load_chart(event_id: int, use_master_sched: bool) -> hs.Chart:
    rows = HeatLane.objects.filter(event_id=event_id).order_by("id")
    lanes = tuple(
        (n, r, h, lane, time is not None or place is not None)
        for n, r, h, lane, time, place in rows.values_list(
            "master_heat", "round_id", "heat", "lane", "finish_time", "finish_place"
        )
    )
    emptied = tuple((n, r, h) for _, r, h, n in held_emptied_heats(Event(pk=event_id)))
    return hs.Chart(lanes, emptied, use_master_sched)


@dataclass
class Inputs:
    """What some effects write: the merged results of the current heat and
    the posted lanes of a timer test, from `racing.services.results`."""

    results: Sequence[LaneResult] = ()
    posted: Sequence[Posted] = ()
    finalists_for: FinalistsFor | None = None
    now: datetime = field(default_factory=timezone.now)


def transition(
    state: RaceState, event: hs.Event, inputs: Inputs | None = None
) -> hs.Decision:
    """Decide `event` and carry it out. The caller holds the `RaceState`
    lock on `state`, which is saved."""
    inputs = inputs or Inputs()
    settings = RaceSettings.objects.get(event_id=state.event_id)
    decision = hs.decide(
        engine_state(state),
        event,
        load_chart(state.event_id, settings.use_master_sched),
    )
    _write_state(state, decision.state)
    effects = _Effects(state, settings, inputs)
    for effect in decision.effects:
        effects.do(effect)
    state.save()
    return decision


def _write_state(state: RaceState, new: hs.State) -> None:
    if new.round_id == hs.TIMER_TEST_ROUND:
        state.timer_test, state.current_round_id = True, None
    else:
        state.timer_test = False
        if new.round_id is None or new.round_id < 0:
            state.current_round_id = state.current_group_id = None
        elif new.round_id != state.current_round_id or state.current_group_id is None:
            # ClassID follows the round (Q2).
            state.current_round_id = new.round_id
            state.current_group_id = (
                Round.objects.filter(pk=new.round_id)
                .values_list("group_id", flat=True)
                .first()
            )
    state.current_heat = new.heat
    state.racing = new.racing
    state.refused_results = new.refused
    if not new.intermission:
        state.waiting_entry_id = state.ended_round_id = None


class _Effects:
    def __init__(self, state: RaceState, settings: RaceSettings, inputs: Inputs):
        self.state = state
        self.settings = settings
        self.inputs = inputs

    def do(self, effect: hs.Effect) -> None:
        handler = getattr(self, "_" + type(effect).__name__)
        handler(effect)

    def _SendReplay(self, effect: hs.SendReplay) -> None:
        if effect.command is hs.Replay.START:
            command = _start_command(*effect.at)
        elif effect.command is hs.Replay.REPLAY:
            command = replay.replay(self.settings)
        else:
            command = replay.CANCEL
        replay.send(self.state.event_id, command, self.inputs.now)

    def _Moved(self, effect: hs.Moved) -> None:
        if effect.round_id is not None and effect.round_id > 0:
            release_emptied_heats_left_behind(self.state.event_id, effect.round_id)

    def _WriteResults(self, effect: hs.WriteResults) -> None:
        round_id, heat = effect.ref
        for result in self.inputs.results:
            HeatLane.objects.filter(
                round_id=round_id, heat=heat, lane=result.lane
            ).update(
                finish_time=result.time,
                finish_place=result.place,
                completed=self.inputs.now,
            )
        self.state.last_heat = LastHeat.recorded(effect.ref)

    def _WriteTestResults(self, effect: hs.WriteTestResults) -> None:
        results = {}
        for lane, posted in enumerate(self.inputs.posted, start=1):
            entry = {}
            if posted.time is not None:
                entry["time"] = posted.time
            if posted.place is not None:
                entry["place"] = posted.place
            if entry:
                results[str(lane)] = entry
        TimerStatus.objects.filter(event_id=self.state.event_id).update(
            test_results_heat=effect.heat, test_results=results
        )

    def _CaptureLastHeat(self, effect: hs.CaptureLastHeat) -> None:
        round_id, heat = effect.ref
        lanes = HeatLane.objects.filter(round_id=round_id, heat=heat).order_by("lane")
        self.state.last_heat = LastHeat.saved(
            effect.ref,
            [
                LaneResult(lane, time, place)
                for lane, time, place in lanes.values_list(
                    "lane", "finish_time", "finish_place"
                )
            ],
        )

    def _ClearResults(self, effect: hs.ClearResults) -> None:
        round_id, heat = effect.ref
        HeatLane.objects.filter(round_id=round_id, heat=heat).update(
            finish_time=None, finish_place=None, completed=None
        )

    def _RestoreLastHeat(self, effect: hs.RestoreLastHeat) -> None:
        last = self.state.last_heat
        rows = HeatLane.objects.filter(round_id=last["round_id"], heat=last["heat"])
        if last.get("lanes") is not None:
            for saved in last["lanes"]:
                rows.filter(lane=saved["lane"]).update(
                    finish_time=saved["time"],
                    finish_place=saved["place"],
                    completed=self.inputs.now,
                )
            return
        # PHP's record: one column per lane from lane 1, by `use-points`.
        column = "finish_place" if self.settings.use_points else "finish_time"
        for lane, value in enumerate(last["results"] or [], start=1):
            rows.filter(lane=lane).update(
                **{column: value, "completed": self.inputs.now}
            )

    def _ClearLastHeat(self, effect: hs.ClearLastHeat) -> None:
        self.state.last_heat = None

    def _ApplyRacingScene(self, effect: hs.ApplyRacingScene) -> None:
        if self.settings.racing_scene_id:
            apply_scene(self.state, self.settings.racing_scene_id)

    def _TimerToConnected(self, effect: hs.TimerToConnected) -> None:
        TimerStatus.objects.filter(
            event_id=self.state.event_id,
            state__in=(TimerStatus.State.STAGING, TimerStatus.State.RUNNING),
        ).update(state=TimerStatus.State.CONNECTED)

    def _ClearTimerCurrent(self, effect: hs.ClearTimerCurrent) -> None:
        TimerStatus.objects.filter(event_id=self.state.event_id).update(
            staged_round=None, staged_heat=None
        )

    def _PlaylistHook(self, effect: hs.PlaylistHook) -> None:
        # The playlist moves the current heat through `transition`, so this
        # module cannot import it at load time.
        from racing.services import playlist, rounds

        round = Round.objects.filter(pk=effect.round_id).first()
        if round is None:
            return
        self.state.save()
        playlist.round_ended(
            round, self.inputs.finalists_for or rounds.playlist_finalists()
        )
        self.state.refresh_from_db()


def _start_command(round_id: int | None, heat: int | None) -> str:
    if round_id == hs.TIMER_TEST_ROUND:
        return replay.start("Timer Test", 1, heat or 0)
    round = Round.objects.select_related("group").filter(pk=round_id).first()
    if round is None:
        return replay.start("", 0, heat or 0)
    return replay.start(round.group.name, round.number, heat or 0)


def release_emptied_heats_left_behind(event_id: int, round_id: int) -> None:
    """D40: once the current heat moves to another round, an emptied heat
    with no heat after it in its own round is let go, so moving to the next
    heat never lands on it again."""
    for emptied in EmptiedHeat.objects.filter(event_id=event_id).exclude(
        round_id=round_id
    ):
        later = (
            HeatLane.objects.filter(
                round_id=emptied.round_id, heat__gt=emptied.heat
            ).exists()
            or EmptiedHeat.objects.filter(
                round_id=emptied.round_id, heat__gt=emptied.heat
            ).exists()
        )
        if not later:
            emptied.delete()


# ----- the services -----


def results_locked(round_id: int) -> bool:
    """D32: the round's results fed a final, a later round of its group or a
    round of an aggregate that draws from its group or one of its
    subgroups, and a heat of that final has a result."""
    round = Round.objects.filter(pk=round_id).first()
    if round is None:
        return False
    finals = Round.objects.filter(event_id=round.event_id).filter(
        Q(group_id=round.group_id, number__gt=round.number)
        | Q(group__constituent_links__constituent_id=round.group_id)
        | Q(group__constituent_subgroup_links__subgroup__group_id=round.group_id)
    )
    return HeatLane.objects.filter(round__in=finals).filter(HAS_RESULT).exists()


def locked_failure(round_id: int | None, control_race: bool) -> hs.Failure | None:
    """`results-locked` for a caller without `CONTROL_RACE` (D32)."""
    if control_race or round_id is None or not results_locked(round_id):
        return None
    return hs.Failure(
        hs.Code.RESULTS_LOCKED,
        "A final has raced, so only the race coordinator can change the"
        " results it was chosen from.",
    )


def select_heat(
    event: Event,
    *,
    round_id: int | None = None,
    heat: int | str | None = None,
    now_racing: bool | None = None,
    finalists_for: FinalistsFor | None = None,
) -> hs.Failure | None:
    """`heat.select`. A round alone means its heat 1. `now_racing` applies
    after the move. A failure leaves the changes made before it."""
    inputs = Inputs(finalists_for=finalists_for)
    with transaction.atomic():
        state = lock_race_state(event.id)
        if round_id is not None or heat is not None:
            requested = heat if heat is not None else 1
            failure = transition(state, hs.Select(round_id, requested), inputs).failure
            if failure is not None:
                return failure
        if now_racing is None:
            return None
        if not now_racing:
            return transition(state, hs.RacingOff(), inputs).failure
        by = hs.By.ROUND_BUTTON if round_id is not None else hs.By.MANAGER
        return transition(state, hs.RacingOn(by), inputs).failure


def set_racing(event: Event, on: bool) -> hs.Failure | None:
    """The racing switch on the dashboard."""
    return select_heat(event, now_racing=on)


def advance(
    event: Event,
    requested: int | str,
    *,
    report: bool = True,
    finalists_for: FinalistsFor | None = None,
) -> hs.Failure | None:
    with transaction.atomic():
        state = lock_race_state(event.id)
        return transition(
            state,
            hs.Advance(requested, report=report),
            Inputs(finalists_for=finalists_for),
        ).failure


def cancel_running_round(event: Event) -> None:
    with transaction.atomic():
        transition(lock_race_state(event.id), hs.Cancel())


def maybe_cancel_running_round(state: RaceState, round_id: int) -> None:
    """Inside a locked transaction: cancel if `round_id` is current."""
    if state.current_round_id == round_id:
        transition(state, hs.Cancel())


def rerun(
    event: Event, which: str = "current", *, control_race: bool = True
) -> hs.Failure | None:
    """`heat.rerun`: race the current heat, or the last heat with a result,
    again. Its results are saved for a reinstate and then cleared, so only
    the new results stand."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        if which == "current":
            target = (
                None
                if state.current_round_id is None or state.current_heat is None
                else (state.current_round_id, state.current_heat)
            )
        else:
            target = LastHeat.ref(state.last_heat)
        if target is not None:
            locked = locked_failure(target[0], control_race)
            if locked is not None:
                return locked
        decision = transition(state, hs.Rerun(target))
    if decision.failure is None:
        audit.write(event.id, [audit.discarded(target)])
    return decision.failure


def reinstate(event: Event, *, control_race: bool = True) -> hs.Failure | None:
    """`heat.reinstate`: bring back the times and places a rerun saved
    (Q4), then racing off."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        target = LastHeat.ref(state.last_heat)
        if target is not None:
            locked = locked_failure(target[0], control_race)
            if locked is not None:
                return locked
        transition(state, hs.Reinstate())
    if target is not None:
        audit.write(event.id, [audit.reinstated(target)])
    return None
