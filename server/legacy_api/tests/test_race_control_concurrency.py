"""Race-control requests at the same moment, through `/action.php`, on real
concurrent transactions (#31). Run on SQLite and on Postgres."""

import json
import threading

import pytest
from django.db import connection
from django.test import Client

from core.models import RaceState
from legacy_api.tests.conftest import post
from racing.models import HeatLane, Round
from racing.services import reschedule

# serialized_rollback restores the seeded roles and award types after the flush.
concurrent = pytest.mark.django_db(transaction=True, serialized_rollback=True)

MANUAL = "lane1=2.1&lane2=2.2&lane3=2.3&lane4=2.4"


def reply(response) -> dict:
    return json.loads(response.content)


def coordinator() -> Client:
    client = Client()
    found = reply(
        post(client, "action=role.login&name=RaceCoordinator&password=doyourbest")
    )
    assert found["outcome"]["code"] == "success", found
    return client


def ok(client, body) -> dict:
    found = reply(post(client, body))
    assert found["outcome"]["code"] == "success", found
    return found


def setup_round(client, racers: int) -> Round:
    ok(client, "action=settings.write&unused-lane-mask=0&n-lanes=4")
    for car in range(101, 101 + racers):
        ok(
            client,
            f"action=racer.import&firstname=F{car}&lastname=L{car}"
            f"&carnumber={car}&partition=Lions",
        )
    ok(client, "action=racer.bulk&who=all&what=checkin")
    round = Round.objects.get()
    ok(client, f"action=schedule.generate&roundid={round.pk}")
    return round


def in_threads(*targets):
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
def test_two_reschedules_at_once_give_one_success_and_one_busy(event, monkeypatch):
    """The first holds the reschedule mutex through its search; the second
    answers `busy` and changes nothing; the chart ends as the first left it,
    with the late racer in every lane once."""
    client = coordinator()
    round = setup_round(client, racers=8)
    ok(client, f"action=heat.select&roundid={round.pk}&now_racing=1")
    ok(
        client,
        "action=racer.import&firstname=Late&lastname=Comer&carnumber=150"
        "&partition=Lions",
    )
    racers = reply(client.get("/action.php", {"query": "racer.list"}))["racers"]
    late = next(r["racerid"] for r in racers if r["carnumber"] == 150)
    ok(client, f"action=racer.pass&racer={late}&value=1")

    searching, release = threading.Event(), threading.Event()
    adjust = reschedule._adjust

    def held_adjust(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            searching.set()
            assert release.wait(30)
        return adjust(*args, **kwargs)

    monkeypatch.setattr(reschedule, "_adjust", held_adjust)
    first: dict = {}

    def first_reschedule():
        try:
            first["reply"] = reply(
                post(coordinator(), f"action=schedule.reschedule&roundid={round.pk}")
            )
        finally:
            connection.close()

    thread = threading.Thread(target=first_reschedule)
    thread.start()
    try:
        assert searching.wait(30)
        second = reply(post(client, f"action=schedule.reschedule&roundid={round.pk}"))
    finally:
        release.set()
        thread.join()

    assert second["outcome"]["code"] == "busy"
    assert "chart" not in second
    assert first["reply"]["outcome"]["code"] == "success", first["reply"]
    assert RaceState.objects.get(event=event).reschedule_started_at is None
    rows = HeatLane.objects.filter(round=round)
    for racer in {r["racerid"] for r in racers}:
        lanes = sorted(rows.filter(racer_id=racer).values_list("lane", flat=True))
        assert lanes == [1, 2, 3, 4], racer
    # The reply's chart is the chart the round holds.
    shown = first["reply"]["chart"]
    for heat, text in enumerate(shown, start=1):
        if text is None:
            continue
        held = dict(rows.filter(heat=heat).values_list("lane", "racer_id"))
        cars = [None if r == "-" else int(r) for r in text.split(" ")]
        assert {lane: r for lane, r in enumerate(cars, 1) if r} == held, heat


ITERATIONS = 5


@concurrent
def test_heat_select_and_result_write_at_once_leave_the_heat_and_results_agreeing(
    event,
):
    """Both move the current heat. Whichever takes the race-state lock first,
    one heat gets all four results, the current heat is the one after the
    next, and no other heat has a result."""
    client = coordinator()
    round = setup_round(client, racers=2 * ITERATIONS + 2)
    ok(client, f"action=heat.select&roundid={round.pk}&now_racing=1")
    other = coordinator()
    orders = set()
    for i in range(ITERATIONS):
        heat = 1 + 2 * i
        state = RaceState.objects.get(event=event)
        assert (state.current_round_id, state.current_heat) == (round.pk, heat)
        selected, written = in_threads(
            lambda: reply(post(client, "action=heat.select&heat=next&now_racing=1")),
            lambda: reply(post(other, f"action=result.write&{MANUAL}")),
        )
        assert selected["outcome"]["code"] == "success", selected
        assert written["outcome"]["code"] == "success", written
        state = RaceState.objects.get(event=event)
        assert (state.current_round_id, state.current_heat, state.racing) == (
            round.pk,
            heat + 2,
            True,
        )
        raced = HeatLane.objects.filter(round=round, heat__in=(heat, heat + 1))
        with_times = raced.exclude(finish_time=None)
        assert with_times.count() == 4, i
        assert len(set(with_times.values_list("heat", flat=True))) == 1, i
        assert sorted(with_times.values_list("finish_time", flat=True)) == [
            2.1,
            2.2,
            2.3,
            2.4,
        ]
        orders.add(with_times.first().heat - heat)
        assert not HeatLane.objects.filter(round=round, heat__gt=heat + 1).exclude(
            finish_time=None
        )
    # Each order leaves the same kind of state; which one ran is recorded.
    assert orders <= {0, 1}
