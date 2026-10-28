"""Closing a damaged lane and reopening it (#108, owner decisions D36, D37).

The race coordinator closes a lane from the race dashboard (#41), which
holds `CONTROL_RACE` (D16); permission checks belong to the handler and the
view. A closed lane goes into `unused-lane-mask`, so every later round
(`racing.services.schedule`) and every late racer fitted in
(`racing.services.reschedule`) leaves it out. Closed during a round, a
`LaneClosure` names that round: its remaining heats are rescheduled on the
open lanes, keeping the running and the staged heat (D34, D39), and the
standings drop every result of that round in the closed lane
(`racing.services.standings`). The rows keep their times, so a heat whose
only car ran on the closed lane still counts as run (Q11 in #20).

Closing a lane can also change how rounds are scored, as the `scoring`
setting does. Every earlier round with a result keeps the scoring it had,
in `Round.scoring`, and the round being raced and every later round take the
new one. A lane can be reopened only until another round is scheduled
(D36). The round that closed it keeps its chart and its dropped times, and
only later rounds race on it again (D37).

PHP has no lane closure. Every writer takes the `RaceState` lock first.
"""

from collections.abc import Callable, Iterable
from datetime import datetime
from enum import StrEnum

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from core.models import Event, RaceSettings, RaceState
from core.services import lock_race_state
from racing.engine.scheduling.adjuster import SearchBudget
from racing.engine.scheduling.lanes import count_one_bits
from racing.engine.standings.scoring import Scoring
from racing.models import HAS_RESULT, HeatLane, LaneClosure, Round
from racing.services.master import held_emptied_heats
from racing.services.reschedule import Rescheduled, RescheduleFailure, adjust_round

MIN_OPEN_LANES = 2


class Failure(StrEnum):
    NO_LANES = "no-lanes"
    NO_SUCH_LANE = "no-such-lane"
    ALREADY_CLOSED = "already-closed"
    TOO_FEW_LANES = "too-few-lanes"
    NO_SUCH_SCORING = "no-such-scoring"
    NOT_CLOSED = "not-closed"
    ROUND_SCHEDULED = "round-scheduled"
    BUSY = "busy"
    NLANES = "nlanes"
    NOCHART = "nochart"


class LanesFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def _mask(lanes: Iterable[int]) -> int:
    return sum(1 << (lane - 1) for lane in set(lanes))


def _lanes(mask: int) -> list[int]:
    return [bit + 1 for bit in range(mask.bit_length()) if mask & (1 << bit)]


def _new_lanes(settings: RaceSettings, lanes: set[int]) -> int:
    """The mask of the lanes to close that are not closed already, or a
    failure."""
    if not lanes:
        raise LanesFailure(Failure.NO_LANES, "Choose a lane to close.")
    if any(not 1 <= lane <= settings.lane_count for lane in lanes):
        raise LanesFailure(
            Failure.NO_SUCH_LANE, f"The track has lanes 1 to {settings.lane_count}."
        )
    new = _mask(lanes) & ~settings.unused_lane_mask
    if not new:
        raise LanesFailure(Failure.ALREADY_CLOSED, "Those lanes are already closed.")
    closed = settings.unused_lane_mask | new
    if settings.lane_count - count_one_bits(closed) < MIN_OPEN_LANES:
        raise LanesFailure(
            Failure.TOO_FEW_LANES,
            f"At least {MIN_OPEN_LANES} lanes must stay open.",
        )
    return new


def _round_in_progress(event_id: int) -> Round | None:
    """The current round, while it has a heat not yet run: a lane with no
    result, or an emptied heat held under D39."""
    state = RaceState.objects.get(event_id=event_id)
    if state.current_round_id is None:
        return None
    round = Round.objects.get(pk=state.current_round_id)
    if HeatLane.objects.filter(round=round).exclude(HAS_RESULT).exists() or any(
        held.round_id == round.id for held in held_emptied_heats(round.event)
    ):
        return round
    return None


def close_lanes(
    event: Event,
    lanes: Iterable[int],
    scoring: int | None = None,
    *,
    budget: SearchBudget | None = None,
    clock: Callable[[], datetime] = timezone.now,
) -> Rescheduled | None:
    """Close `lanes` (lane numbers from 1) from now on, and score by
    `scoring`, a legacy `scoring` value, or leave the scoring as it is.

    With a round in progress, its remaining heats are rescheduled through
    `racing.services.reschedule`, with its guard and its retry (Q15), and
    the new chart is returned. Otherwise only later rounds change, and None
    is returned.

    Raises LanesFailure, with nothing changed: `no-lanes`, `no-such-lane`
    for a lane the track does not have, `already-closed` when every lane
    named is closed or unused already, `too-few-lanes` when fewer than 2
    would stay open, `no-such-scoring`, and `busy`, `nlanes` or `nochart`
    (a heat 0) from the reschedule.
    """
    lanes = set(lanes)
    if scoring is not None and scoring not in {s.value for s in Scoring}:
        raise LanesFailure(Failure.NO_SUCH_SCORING, f"No scoring {scoring}.")
    _new_lanes(RaceSettings.objects.get(event_id=event.id), lanes)
    round = _round_in_progress(event.id)
    if round is None:
        with transaction.atomic():
            lock_race_state(event.id)
            _record_closure(event.id, None, lanes, scoring, clock())
        return None
    new = _new_lanes(RaceSettings.objects.get(event_id=event.id), lanes)
    try:
        return adjust_round(
            round,
            close=new,
            also=lambda state: _record_closure(
                event.id, round, lanes, scoring, clock(), expected=new
            ),
            budget=budget,
            clock=clock,
        )
    except RescheduleFailure as failure:
        raise LanesFailure(Failure(str(failure.code)), failure.description) from None


def _record_closure(
    event_id: int,
    round: Round | None,
    lanes: set[int],
    scoring: int | None,
    now: datetime,
    expected: int | None = None,
) -> None:
    """Under the lock: the mask, the `LaneClosure`, and the scoring."""
    settings = RaceSettings.objects.get(event_id=event_id)
    new = _new_lanes(settings, lanes)
    if expected is not None and new != expected:
        # The reschedule rechecks the mask, so only a writer that skipped
        # the lock gets here.
        raise LanesFailure(Failure.BUSY, "The lanes changed. Try again.")
    LaneClosure.objects.create(event_id=event_id, round=round, mask=new, closed_at=now)
    settings.unused_lane_mask |= new
    fields = ["unused_lane_mask"]
    if scoring is not None:
        results = HeatLane.objects.filter(round=OuterRef("pk")).filter(HAS_RESULT)
        earlier = Round.objects.filter(event_id=event_id, scoring__isnull=True)
        if round is not None:
            earlier = earlier.exclude(pk=round.pk)
        earlier.filter(Exists(results)).update(scoring=settings.scoring)
        settings.scoring = scoring
        fields.append("scoring")
        if round is not None:
            Round.objects.filter(pk=round.pk).update(scoring=scoring)
    settings.save(update_fields=fields)


def reopen_lanes(
    event: Event, lanes: Iterable[int], *, clock: Callable[[], datetime] = timezone.now
) -> None:
    """Reopen closed lanes for the rounds scheduled from now on (D37).

    Raises LanesFailure, with nothing changed: `not-closed` for a lane no
    open closure holds, as a lane left unused on the settings page, and
    `round-scheduled` once a round was scheduled after the lane closed
    (D36).
    """
    mask = _mask(lanes)
    with transaction.atomic():
        lock_race_state(event.id)
        closures = list(
            LaneClosure.objects.filter(event=event, reopened_at__isnull=True).order_by(
                "id"
            )
        )
        closing = [c for c in closures if c.mask & mask]
        held = 0
        for closure in closing:
            held |= closure.mask
        if not mask or mask & ~held:
            raise LanesFailure(Failure.NOT_CLOSED, "Those lanes are not closed.")
        if any(c.next_round_at is not None for c in closing):
            raise LanesFailure(
                Failure.ROUND_SCHEDULED,
                "A round was scheduled after the lane closed, so it stays closed.",
            )
        now = clock()
        for closure in closing:
            kept = closure.mask & ~mask
            if kept:
                # The lanes left closed get a row of their own.
                LaneClosure.objects.create(
                    event=event,
                    round=closure.round,
                    mask=kept,
                    closed_at=closure.closed_at,
                )
            closure.mask &= mask
            closure.reopened_at = now
            closure.save(update_fields=["mask", "reopened_at"])
        settings = RaceSettings.objects.get(event=event)
        settings.unused_lane_mask &= ~mask
        settings.save(update_fields=["unused_lane_mask"])


def poll_lanes(event: Event) -> dict:
    """The poll field for the race dashboard (#41): each lane closed now,
    the round it closed in (null between rounds), and whether it can still
    be reopened, then the scoring in force: the current round's, or the
    event's."""
    rows = []
    for closure in LaneClosure.objects.filter(
        event=event, reopened_at__isnull=True
    ).order_by("id"):
        rows += [
            {
                "lane": lane,
                "roundid": closure.round_id,
                "reopen": closure.next_round_at is None,
            }
            for lane in _lanes(closure.mask)
        ]
    rows.sort(key=lambda row: row["lane"])
    scoring = RaceSettings.objects.get(event=event).scoring
    current = RaceState.objects.get(event=event).current_round_id
    if current is not None:
        own = Round.objects.get(pk=current).scoring
        scoring = scoring if own is None else own
    return {"closed-lanes": rows, "scoring": scoring}
