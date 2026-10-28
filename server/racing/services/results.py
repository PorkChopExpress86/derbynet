"""Recording and clearing heat results: from the timer, by hand, and
`result.delete`.

Ported from `write_heat_results` in `website/inc/write-heat-results.inc`,
the `FINISHED` branch of `website/ajax/action.timer-message.inc`, and
`website/ajax/action.result.write.inc` and `action.result.delete.inc`. Every
write of a result sets `completed` to now, and every clear empties it, as
`racing.services.master.held_emptied_heats` reads it (D39).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from django.db import transaction
from django.utils import timezone

from core.models import Event, RaceSettings, RaceState
from core.services import lock_race_state
from racing.engine import heat_state as hs
from racing.engine.results import (
    LaneResult,
    Posted,
    is_all_zeroes,
    is_false_start,
    merge_results,
    merge_timer_results,
    mirror_results,
    non_numeric_times,
    parse_heat_results,
)
from racing.models import HeatLane, Round
from racing.services import audit
from racing.services.heats import (
    FinalistsFor,
    Inputs,
    locked_failure,
    rebuild_waiting_final,
    transition,
)

logger = logging.getLogger(__name__)

type Fields = Iterable[tuple[str, str]]
"""The posted form fields, as `parse_qsl` gives them."""


class Outcome(StrEnum):
    RECORDED = "recorded"
    NOT_RACING = "notracing"
    ALL_ZERO = "all-zero"
    WRONG_HEAT = "wrong-heat"
    NO_CURRENT_ROUND = "nocurrentround"
    RESULTS_LOCKED = "results-locked"


@dataclass(frozen=True)
class Recorded:
    """What the timer view or the result handler renders."""

    outcome: Outcome
    failure: hs.Failure | None = None


def _merged(state: RaceState, posted: list[Posted], merge=merge_results):
    if state.timer_test or state.current_round_id is None:
        return []
    rows = HeatLane.objects.filter(
        round_id=state.current_round_id, heat=state.current_heat
    ).order_by("lane")
    stored = [
        LaneResult(lane, time, place)
        for lane, time, place in rows.values_list("lane", "finish_time", "finish_place")
    ]
    return merge(stored, posted)


def _posted(fields: Fields, settings: RaceSettings, mirror: bool) -> list[Posted]:
    posted = parse_heat_results(fields, settings.lane_count)
    if mirror and settings.reverse_lanes:
        posted = mirror_results(posted, settings.lane_count)
    for lane in non_numeric_times(posted):
        logger.warning("Lane %d posted a time that is not a number (Q16).", lane)
    return posted


def _named(fields: Fields, key: str) -> str | None:
    found = None
    for name, value in fields:
        if name == key:
            found = value
    return found


def _outcome(decision: hs.Decision) -> Outcome:
    if decision.failure is not None:
        return Outcome(decision.failure.code)
    wrote = (hs.WriteResults, hs.WriteTestResults)
    if any(isinstance(e, wrote) for e in decision.effects):
        return Outcome.RECORDED
    return Outcome.ALL_ZERO


def record_timer_result(
    event: Event, fields: Fields, *, finalists_for: FinalistsFor | None = None
) -> Recorded:
    """A timer `FINISHED`: write the current heat's results, replay, and move
    on to the next heat, or refuse with `notracing`, `wrong-heat` or
    `nocurrentround`. An all-zero result replays, stops racing and writes
    nothing. A car the timer gives no time, or a time that is not a number,
    did not finish and gets `DID_NOT_FINISH` (D42). The timer writes only the
    current heat, which only the race coordinator selects, so the results
    lock (D32) never refuses it."""
    fields = list(fields)
    now = timezone.now()
    with transaction.atomic():
        state = lock_race_state(event.id)
        settings = RaceSettings.objects.get(event_id=event.id)
        posted = _posted(fields, settings, mirror=True)
        ref = (state.current_round_id, state.current_heat)
        ended = state.ended_round_id
        results = _merged(state, posted, merge_timer_results)
        decision = transition(
            state,
            hs.TimerResult(
                all_zero=is_all_zeroes(posted)
                or (
                    bool(results) and is_false_start([r.lane for r in results], posted)
                ),
                written=tuple((r.lane, _has_result(r)) for r in results),
                posted_round=_named(fields, "roundid"),
                posted_heat=_named(fields, "heat"),
            ),
            Inputs(results, posted, finalists_for, now),
        )
        if _outcome(decision) is Outcome.RECORDED:
            rebuild_waiting_final(event, [ref[0]], ended, finalists_for)
    outcome = _outcome(decision)
    if outcome is Outcome.NOT_RACING:
        audit.write(
            event.id,
            [audit.heat_result(audit.Action.TIMER_RESULT_REFUSED, None, posted)],
        )
    elif outcome is Outcome.RECORDED:
        audit.write(
            event.id,
            [audit.heat_result(audit.Action.HEAT_RESULT_FROM_TIMER, ref, posted)],
        )
    return Recorded(outcome, decision.failure)


def record_manual_result(
    event: Event,
    fields: Fields,
    expected: hs.HeatRef | None = None,
    *,
    control_race: bool = True,
    finalists_for: FinalistsFor | None = None,
) -> Recorded:
    """`result.write`: results typed in for the current heat, which advances
    only while racing. With `expected`, the heat the race coordinator was
    shown, a heat that moved meanwhile gets `wrong-heat` and nothing is
    written (D19). The legacy form sends none, so a result that loses a race
    with the timer goes to the new current heat, as in PHP."""
    fields = list(fields)
    now = timezone.now()
    with transaction.atomic():
        state = lock_race_state(event.id)
        settings = RaceSettings.objects.get(event_id=event.id)
        locked = locked_failure(state.current_round_id, control_race)
        if locked is not None and not state.timer_test:
            return Recorded(Outcome.RESULTS_LOCKED, locked)
        posted = _posted(fields, settings, mirror=False)
        ref = (state.current_round_id, state.current_heat)
        ended = state.ended_round_id
        results = _merged(state, posted)
        decision = transition(
            state,
            hs.ManualResult(
                expected=expected,
                written=tuple((r.lane, _has_result(r)) for r in results),
                posted_round=_named(fields, "roundid"),
                posted_heat=_named(fields, "heat"),
            ),
            Inputs(results, posted, finalists_for, now),
        )
        if _outcome(decision) is Outcome.RECORDED:
            rebuild_waiting_final(event, [ref[0]], ended, finalists_for)
    outcome = _outcome(decision)
    if outcome is Outcome.RECORDED and ref[0] is not None:
        audit.write(
            event.id,
            [
                audit.discarded(ref),
                audit.heat_result(audit.Action.HEAT_MANUALLY_ENTERED, ref, posted),
            ],
        )
    return Recorded(outcome, decision.failure)


class _Current:
    def __repr__(self) -> str:
        return "CURRENT"


CURRENT: Final = _Current()
"""`result.delete`'s `current`: the round or heat that is current once the
race state is locked."""


def delete_results(
    event: Event,
    round: Round | _Current | None = None,
    heat: int | _Current | None = None,
    *,
    control_race: bool = True,
    finalists_for: FinalistsFor | None = None,
) -> hs.Failure | None:
    """`result.delete`: clear the results of every round, of one round, or
    of one heat of it. The current heat and racing stay as they are.
    `CURRENT` names the current round or heat as the lock finds it; with no
    current round, nothing is cleared, as PHP's `roundid = -1` matches no
    row."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        ended = state.ended_round_id
        if round is CURRENT:
            round = Round.objects.filter(event=event, pk=state.current_round_id).first()
            if round is None:
                return None
        if heat is CURRENT:
            heat = state.current_heat
            if heat is None:
                return None
        rows = HeatLane.objects.filter(event=event)
        rounds = Round.objects.filter(event=event)
        if round is not None:
            rows = rows.filter(round=round)
            rounds = rounds.filter(pk=round.pk)
            if heat is not None:
                rows = rows.filter(heat=heat)
        for round_id in rounds.values_list("id", flat=True):
            locked = locked_failure(round_id, control_race)
            if locked is not None:
                return locked
        rows.update(finish_time=None, finish_place=None, completed=None)
        changed = None if round is None else [round.id]
        rebuild_waiting_final(event, changed, ended, finalists_for)
    if round is not None and heat is not None:
        audit.write(event.id, [audit.discarded((round.id, heat))])
    return None


def _has_result(result: LaneResult) -> bool:
    return result.time is not None or result.place is not None
