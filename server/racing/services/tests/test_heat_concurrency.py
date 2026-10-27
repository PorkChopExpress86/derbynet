"""The `RaceState` lock under real concurrency, on SQLite and on Postgres
(#20, "Transactions and locks"), and the audit that never delays the timer
(D26)."""

import logging
import sqlite3
import threading
import time

import pytest
from django.db import connection, transaction

from core.services import lock_race_state
from racing.models import AuditEvent, HeatLane
from racing.services import results as results_service
from racing.services.heats import select_heat
from racing.services.results import (
    Outcome,
    record_manual_result,
    record_timer_result,
)
from racing.services.tests.race import current, fields, lanes, setup_race, state

# serialized_rollback restores the seeded award types after the flush.
concurrent = pytest.mark.django_db(transaction=True, serialized_rollback=True)

UPDATES_PER_THREAD = 25


def _in_threads(*targets):
    barrier = threading.Barrier(len(targets))
    errors, results = [], [None] * len(targets)

    def run(i, target):
        try:
            barrier.wait()
            results[i] = target()
        except Exception as error:
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i, t)) for i, t in enumerate(targets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    return results


@concurrent
def test_lock_read_write_loses_no_update():
    """Each thread locks `RaceState`, reads a counter, and writes it back plus
    one, as the services read and then write race state."""
    replay, _ = setup_race(racers=2)
    event = replay.event

    def count():
        for _ in range(UPDATES_PER_THREAD):
            with transaction.atomic():
                state = lock_race_state(event.id)
                seen = state.refused_results
                time.sleep(0.001)
                state.refused_results = seen + 1
                state.save(update_fields=["refused_results"])

    _in_threads(count, count)
    assert state(event).refused_results == 2 * UPDATES_PER_THREAD


ITERATIONS = 20
TIMER = "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4"
MANUAL = "lane1=2.1&lane2=2.2&lane3=2.3&lane4=2.4"


@concurrent
def test_a_timer_result_and_a_manual_result_never_interleave():
    """Both name the heat they mean: the timer posts it, and the race
    coordinator's form sends the heat it showed (D19). One writes the heat
    and advances once; the other gets `wrong-heat`."""
    replay, round = setup_race(racers=ITERATIONS + 1)
    event = replay.event
    select_heat(event, round_id=round.id, now_racing=True)
    winners = set()
    for heat in range(1, ITERATIONS + 1):
        assert current(event) == (round.id, heat, True)
        timer, manual = _in_threads(
            lambda h=heat: record_timer_result(
                event, fields(f"{TIMER}&roundid={round.id}&heat={h}")
            ),
            lambda h=heat: record_manual_result(
                event, fields(MANUAL), expected=(round.id, h)
            ),
        )
        outcomes = {timer.outcome, manual.outcome}
        assert outcomes == {Outcome.RECORDED, Outcome.WRONG_HEAT}, heat
        winner = TIMER if timer.outcome is Outcome.RECORDED else MANUAL
        winners.add(winner)
        times = [float(pair.split("=")[1]) for pair in winner.split("&")]
        assert [t for t, _ in lanes(round, heat).values()] == times, heat
        assert not HeatLane.objects.filter(round=round, heat=heat + 1).exclude(
            finish_time=None
        )
        assert current(event) == (round.id, heat + 1, True), heat


@concurrent
@pytest.mark.skipif(
    connection.vendor != "sqlite", reason="Postgres writers never block an insert"
)
def test_a_locked_database_skips_the_audit_and_never_delays_the_timer(
    monkeypatch, caplog
):
    """D26: another connection holds SQLite's write lock once the race-state
    transaction has committed. The timer's reply still comes back at once,
    with its result written, and the audit row is skipped with a warning."""
    replay, round = setup_race()
    event = replay.event
    select_heat(event, round_id=round.id, now_racing=True)
    holder = sqlite3.connect(connection.settings_dict["NAME"], timeout=0)
    write = results_service.audit.write

    def write_while_locked(event_id, records):
        holder.execute("BEGIN IMMEDIATE")
        return write(event_id, records)

    monkeypatch.setattr(results_service.audit, "write", write_while_locked)
    try:
        with caplog.at_level(logging.WARNING, logger="racing.services.audit"):
            started = time.monotonic()
            recorded = record_timer_result(event, fields(TIMER))
            elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert recorded.outcome is Outcome.RECORDED
    assert elapsed < 3, elapsed
    assert lanes(round, 1)[1] == (3.1, 1)
    assert current(event) == (round.id, 2, True)
    assert not AuditEvent.objects.filter(event=event).exists()
    assert "Skipped 1 audit record" in caplog.text
