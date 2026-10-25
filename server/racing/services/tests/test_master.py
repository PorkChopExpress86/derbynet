import json
import random

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import RaceSettings
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HeatLane
from racing.services import master
from racing.services.master import reassign_master_heats
from racing.services.schedule import generate_schedule, unschedule
from racing.tests.factories import RosterEntryFactory, RoundFactory
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db


def new_round(nracers, event=None, group=None, number=1):
    """A round of `nracers` checked-in racers, in a new group of `event` (a
    new event on 4 lanes by default), or as round `number` of `group`."""
    if group is None:
        group = GroupFactory(event=event) if event else GroupFactory()
        if event is None:
            RaceSettings.objects.filter(event=group.event).update(lane_count=4)
    round = RoundFactory(group=group, number=number)
    for _ in range(nracers):
        RosterEntryFactory(
            round=round, racer=RacerFactory(group=group, passed_inspection=True)
        )
    return round


def numbering(event):
    """`{(roundid, heat): {master heat of each lane row}}`."""
    seen = {}
    for roundid, heat, n in HeatLane.objects.filter(event=event).values_list(
        "round_id", "heat", "master_heat"
    ):
        seen.setdefault((roundid, heat), set()).add(n)
    return seen


def single_numbers(event):
    seen = numbering(event)
    assert all(len(ns) == 1 for ns in seen.values()), seen
    return {heat: ns.pop() for heat, ns in seen.items()}


def finish(round, heat):
    HeatLane.objects.filter(round=round, heat=heat).update(finish_time=3.0)


def php_ondeck(exchange):
    php = read_transcript(GOLDEN_DIR / "http" / "master-schedule" / "half-weights.http")
    return {
        (row["roundid"], row["heat"]): row["masterheat"]
        for row in json.loads(php[exchange]["body"])["ondeck"]["chart"]
    }


def test_the_half_weights_race_numbers_heats_as_php():
    """`capture/scenarios/05-master-schedule.sh` through the services: PHP
    roundids 1 to 4 are the rounds made here, in order."""
    first = new_round(2)
    event = first.event
    RaceSettings.objects.filter(event=event).update(lane_count=2, use_master_sched=True)
    rounds = [first] + [new_round(n, event) for n in (3, 5, 9)]
    php_id = {round.id: i for i, round in enumerate(rounds, start=1)}

    def as_php(numbers):
        return {(php_id[r], h): n for (r, h), n in numbers.items()}

    for round in rounds:
        generate_schedule(round, 1)
    assert as_php(single_numbers(event)) == php_ondeck("007 ondeck-in-master-order")

    finish(first, 1)
    generate_schedule(rounds[3], 1)
    assert as_php(single_numbers(event)) == php_ondeck("013 ondeck-after-regenerating")


def test_unschedule_renumbers_what_is_left():
    a = new_round(4)
    event = a.event
    b = new_round(4, event)
    generate_schedule(a, 1)
    generate_schedule(b, 1)
    assert sorted(single_numbers(event).values()) == list(range(1, 9))

    unschedule(a)

    assert single_numbers(event) == {(b.id, h): h for h in range(1, 5)}


def test_reassign_leaves_other_events_alone():
    a = new_round(4)
    b = new_round(4)
    generate_schedule(a, 1)
    generate_schedule(b, 1)
    assert single_numbers(a.event) == {(a.id, h): h for h in range(1, 5)}
    assert single_numbers(b.event) == {(b.id, h): h for h in range(1, 5)}


def test_a_failed_master_update_rolls_back_the_chart(monkeypatch):
    round = new_round(4)
    other = new_round(5, round.event)
    generate_schedule(round, 1)
    generate_schedule(other, 1)
    before_chart = list(
        HeatLane.objects.filter(event=round.event)
        .order_by("id")
        .values_list("round_id", "heat", "lane", "racer_id", "master_heat")
    )

    def broken(unraced, start):
        raise RuntimeError("master update failed")

    monkeypatch.setattr(master, "assign_master_heats", broken)
    with pytest.raises(RuntimeError, match="master update failed"):
        generate_schedule(round, 2)
    with pytest.raises(RuntimeError, match="master update failed"):
        unschedule(other)

    after_chart = list(
        HeatLane.objects.filter(event=round.event)
        .order_by("id")
        .values_list("round_id", "heat", "lane", "racer_id", "master_heat")
    )
    assert after_chart == before_chart


def test_1200_heats_are_numbered_in_one_update():
    round = new_round(6)
    racers = list(round.roster.values_list("racer_id", flat=True))
    HeatLane.objects.bulk_create(
        HeatLane(event=round.event, round=round, heat=heat, lane=lane, racer_id=racer)
        for heat in range(1, 1201)
        for lane, racer in enumerate(racers, start=1)
    )

    with CaptureQueriesContext(connection) as queries:
        reassign_master_heats(round.event)

    updates = [q for q in queries if q["sql"].lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 1
    assert single_numbers(round.event) == {(round.id, h): h for h in range(1, 1201)}


def check_invariants(event, before, raced):
    """Raced heats keep their numbers, every lane row of a heat has one
    number, and the unraced heats are numbered consecutively from 1 above the
    highest number a lane with a result had before the change."""
    numbers = single_numbers(event)
    for heat in raced:
        assert numbers[heat] == before[heat], heat
    with_result = set(
        HeatLane.objects.filter(event=event)
        .filter(master.HAS_RESULT)
        .values_list("round_id", "heat")
    )
    highest = max((before[heat] for heat in with_result), default=0)
    unraced = sorted(n for heat, n in numbers.items() if heat not in raced)
    assert unraced == list(range(highest + 1, highest + 1 + len(unraced)))


def fully_raced(event):
    rows = HeatLane.objects.filter(event=event)
    open_heats = set(
        rows.filter(finish_time__isnull=True, finish_place__isnull=True).values_list(
            "round_id", "heat"
        )
    )
    return set(rows.values_list("round_id", "heat")) - open_heats


@pytest.mark.parametrize("seed", range(12))
def test_numbering_holds_over_any_sequence_of_chart_and_result_changes(seed):
    rng = random.Random(seed)
    first = new_round(rng.randint(2, 6))
    event = first.event
    RaceSettings.objects.filter(event=event).update(lane_count=rng.choice([2, 3, 4]))
    rounds = [first]
    for _ in range(rng.randint(1, 3)):
        rounds.append(new_round(rng.randint(2, 9), event))
    for parent in list(rounds):
        if rng.random() < 0.5:
            rounds.append(new_round(rng.randint(2, 5), group=parent.group, number=2))

    for _ in range(25):
        op = rng.choice(["generate", "unschedule", "result", "partial", "clear"])
        before, raced = single_numbers(event), fully_raced(event)
        unraced = sorted(set(before) - raced)
        if op in ("result", "partial") and unraced:
            roundid, heat = rng.choice(unraced)
            rows = HeatLane.objects.filter(round_id=roundid, heat=heat)
            if op == "partial":
                rows = rows.filter(id=rows.order_by("lane").first().id)
            rows.update(finish_time=rng.uniform(2, 4))
        elif op == "clear" and raced:
            roundid, heat = rng.choice(sorted(raced))
            HeatLane.objects.filter(round_id=roundid, heat=heat).update(
                finish_time=None, finish_place=None
            )
        else:
            round = rng.choice(rounds)
            if op == "unschedule":
                unschedule(round)
                raced = {heat for heat in raced if heat[0] != round.id}
            elif HeatLane.objects.filter(round=round).filter(master.HAS_RESULT):
                reassign_master_heats(event)
            else:
                generate_schedule(round, rng.randint(1, 2))
            check_invariants(event, before, raced)
