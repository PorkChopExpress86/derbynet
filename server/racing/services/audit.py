"""The timer and heat audit (owner decision D19), legacy `Events` and
`EventLaneDetails`.

It replaces `record_timer_finish`, `record_event_for_discarded_heat` and
`record_event_for_reinstated_heat` in `website/inc/event-lane-details.inc`.
A service builds its records inside its race-state transaction and calls
`write` after that transaction ends. `write` never delays or fails its
caller (D26): it runs in its own durable transaction, waits at most
`LOCK_BUDGET` for the database, and on a timeout or any database error logs
a warning and skips the records.
"""

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import DatabaseError, connection, transaction

from racing.engine.phpcompat import php_numeric
from racing.engine.results import Posted
from racing.models import AuditEvent, AuditLaneResult

logger = logging.getLogger(__name__)

LOCK_BUDGET = timedelta(seconds=1)
"""The longest the audit waits for the database lock (D26)."""

Action = AuditEvent.Action

_FINISHES = (
    Action.HEAT_RESULT_FROM_TIMER,
    Action.HEAT_MANUALLY_ENTERED,
    Action.HEAT_RESULT_REINSTATED,
)


@dataclass(frozen=True)
class Record:
    action: Action
    round_id: int | None = None
    heat: int | None = None
    lanes: tuple[Posted, ...] = ()
    detail: dict | None = None
    # When it happened, if not when it is written.
    at: datetime | None = None


def heat_result(
    action: Action, ref: tuple[int, int] | None, posted: Iterable[Posted]
) -> Record:
    """A heat's posted results as the timer or the race coordinator sent
    them, lane 1 first. A refused result has no heat."""
    round_id, heat = ref or (None, None)
    return Record(action, round_id, heat, tuple(posted))


def discarded(ref: tuple[int, int]) -> Record:
    """The heat's results are cleared or overwritten. Recorded only when the
    heat's last audited result stands."""
    return Record(Action.HEAT_RESULT_DISCARDED, *ref)


def reinstated(ref: tuple[int, int]) -> Record:
    """The heat's discarded results come back. Recorded only after a
    discard."""
    return Record(Action.HEAT_RESULT_REINSTATED, *ref)


def timer_message(ref: tuple[int, int] | None, detail: dict) -> Record:
    """A message from the timer, at the heat that was current (D19, #32)."""
    round_id, heat = ref or (None, None)
    return Record(Action.TIMER_MESSAGE, round_id, heat, detail=detail)


def timer_state_changed(
    ref: tuple[int, int] | None, was: int, to: int, at: datetime | None = None
) -> Record:
    round_id, heat = ref or (None, None)
    return Record(
        Action.TIMER_STATE_CHANGED,
        round_id,
        heat,
        detail={"from": was, "to": to},
        at=at,
    )


def write(event_id: int, records: Iterable[Record]) -> bool:
    """Write the records, or skip them all with a warning. Never raises a
    database error. Returns whether they were written."""
    records = list(records)
    if not records:
        return True
    try:
        with _lock_budget(), transaction.atomic(durable=True):
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET LOCAL lock_timeout = %s",
                        [f"{int(LOCK_BUDGET.total_seconds() * 1000)}ms"],
                    )
            for record in records:
                _write(event_id, record)
    except DatabaseError as error:
        logger.warning(
            "Skipped %d audit record(s) for event %s: %s",
            len(records),
            event_id,
            error,
        )
        return False
    return True


@contextmanager
def _lock_budget() -> Iterator[None]:
    """On SQLite, `transaction_mode` IMMEDIATE waits for the write lock at
    `BEGIN` for the connection's busy timeout, 20 s. Shorten it for the
    audit only."""
    if connection.vendor != "sqlite":
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA busy_timeout")
        (before,) = cursor.fetchone()
        cursor.execute(
            f"PRAGMA busy_timeout = {int(LOCK_BUDGET.total_seconds() * 1000)}"
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"PRAGMA busy_timeout = {int(before)}")


def _last_for_heat(event_id: int, record: Record) -> AuditEvent | None:
    return (
        AuditEvent.objects.filter(
            event_id=event_id,
            round_id=record.round_id,
            heat=record.heat,
            action__in=(*_FINISHES, Action.HEAT_RESULT_DISCARDED),
        )
        .order_by("-id")
        .first()
    )


def _finish_of(event: AuditEvent) -> AuditEvent:
    """The result a reinstate or a discard refers to."""
    return event.result_of if event.result_of_id else event


def _write(event_id: int, record: Record) -> None:
    result_of = None
    if record.action in (Action.HEAT_RESULT_DISCARDED, Action.HEAT_RESULT_REINSTATED):
        last = _last_for_heat(event_id, record)
        wanted = (
            _FINISHES
            if record.action == Action.HEAT_RESULT_DISCARDED
            else (Action.HEAT_RESULT_DISCARDED,)
        )
        if last is None or last.action not in wanted:
            return
        result_of = _finish_of(last)
    raw = {
        f"lane{lane}": posted.time
        for lane, posted in enumerate(record.lanes, start=1)
        if posted.time not in (None, "") and php_numeric(posted.time) is None
    }
    detail = dict(record.detail or {})
    if raw:
        detail["non-numeric"] = raw
    event = AuditEvent.objects.create(
        event_id=event_id,
        action=record.action,
        round_id=record.round_id,
        heat=record.heat,
        result_of=result_of,
        detail=detail or None,
        **({"recorded_at": record.at} if record.at is not None else {}),
    )
    AuditLaneResult.objects.bulk_create(
        AuditLaneResult(
            event_id=event_id,
            audit_event=event,
            lane=lane,
            finish_time=_float(posted.time),
            finish_place=_int(posted.place),
        )
        for lane, posted in enumerate(record.lanes, start=1)
        if posted.time is not None or posted.place is not None
    )


def _float(text: str | None) -> float | None:
    number = None if text is None else php_numeric(text)
    return None if number is None else float(number)


def _int(text: str | None) -> int | None:
    number = None if text is None else php_numeric(text)
    return None if number is None or number < 0 else int(number)
