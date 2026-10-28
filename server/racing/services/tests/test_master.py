import json
import os
import random
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import RaceSettings
from core.tests.factories import EventFactory
from racing.engine.scheduling.master import master_target
from racing.engine.tests.golden import (
    DEVIATIONS_FILE,
    GOLDEN_DIR,
    read_cases,
    read_transcript,
    transcript_deviations,
)
from racing.models import HAS_RESULT, HeatLane
from racing.services import master
from racing.services.heats import select_heat
from racing.services.master import master_index, reassign_master_heats
from racing.services.results import record_timer_result
from racing.services.schedule import (
    Failure,
    ScheduleFailure,
    generate_schedule,
    unschedule,
)
from racing.services.tests.race import current, fields
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


HALF_WEIGHTS = "http/master-schedule/half-weights.http"
Q18_EXPECTED = "deviations/Q18/half-weights.http"
# Q18: the polls after the refused generate, and the polls before it, which
# hold the state Django keeps.
Q18_EXCHANGES = {
    "018 coordinator-after-results-exist": "015 coordinator-before-results-exist",
    "019 ondeck-after-results-exist": "016 ondeck-before-results-exist",
}
Q18_REASON = (
    "A refused schedule.generate renumbers the unraced heats and moves the current"
    " heat to the first of them in PHP. Django changes nothing (D30)."
)


def php_body(exchange, path=HALF_WEIGHTS):
    return json.loads(read_transcript(GOLDEN_DIR / path)[exchange]["body"])


def chart_numbers(body):
    """`{(roundid, heat): masterheat}` from a `values=ondeck` poll."""
    return {
        (row["roundid"], row["heat"]): row["masterheat"]
        for row in body["ondeck"]["chart"]
    }


def php_ondeck(exchange):
    return chart_numbers(php_body(exchange))


def half_weights_race():
    """`capture/scenarios/05-master-schedule.sh`: rounds of 2, 3, 5 and 9
    racers on two lanes under the master schedule. Returns the rounds and
    `php_id`, which maps their ids to PHP's roundids 1 to 4."""
    first = new_round(2)
    event = first.event
    RaceSettings.objects.filter(event=event).update(lane_count=2, use_master_sched=True)
    rounds = [first] + [new_round(n, event) for n in (3, 5, 9)]
    return rounds, {round.id: i for i, round in enumerate(rounds, start=1)}


def php_numbers(event, php_id):
    """`single_numbers` keyed by PHP's roundids."""
    return {(php_id[r], h): n for (r, h), n in single_numbers(event).items()}


def test_the_half_weights_race_numbers_heats_as_php():
    """`capture/scenarios/05-master-schedule.sh` through the services: PHP
    roundids 1 to 4 are the rounds made here, in order."""
    rounds, php_id = half_weights_race()
    first, event = rounds[0], rounds[0].event

    for round in rounds:
        generate_schedule(round, 1)
    assert php_numbers(event, php_id) == php_ondeck("007 ondeck-in-master-order")

    finish(first, 1)
    generate_schedule(rounds[3], 1)
    assert php_numbers(event, php_id) == php_ondeck("013 ondeck-after-regenerating")


def test_a_refused_generate_renumbers_nothing():
    """Q18 (D30), exchanges 014 to 019 of the half-weights race. Heat 9 of
    round 4, which holds the highest master number, races out of order, so
    the numbers of the heats left are stale. Regenerating round 1, which has
    a result, is refused with `results-exist`. PHP renumbers the unraced
    heats from 20 and moves the current heat to round 1 heat 2. Django keeps
    the numbers and the current heat of the polls before the request."""
    rounds, php_id = half_weights_race()
    event = rounds[0].event
    for round in rounds:
        generate_schedule(round, 1)
    finish(rounds[0], 1)
    generate_schedule(rounds[3], 1)
    select_heat(event, round_id=rounds[3].id, heat=9, now_racing=True)
    finished = read_transcript(GOLDEN_DIR / HALF_WEIGHTS)[
        "014 finished-last-master-heat-out-of-order"
    ]["request-body"]
    record_timer_result(event, fields(finished.split("message=FINISHED&", 1)[1]))

    def state():
        roundid, heat, racing = current(event)
        return php_numbers(event, php_id), (php_id[roundid], heat, racing)

    def php_state(coordinator, ondeck):
        heat = coordinator["current-heat"]
        return chart_numbers(ondeck), (
            heat["roundid"],
            heat["heat"],
            heat["now_racing"],
        )

    before = state()
    assert before == php_state(
        php_body("015 coordinator-before-results-exist"),
        php_body("016 ondeck-before-results-exist"),
    )

    with pytest.raises(ScheduleFailure) as refused:
        generate_schedule(rounds[0], 1)

    assert refused.value.code == Failure.RESULTS_EXIST
    assert state() == before
    expected = transcript_deviations(HALF_WEIGHTS)
    assert state() == php_state(
        *(json.loads(expected[name][1]["body"]) for name in Q18_EXCHANGES)
    )
    php_after = php_state(*(php_body(name) for name in Q18_EXCHANGES))
    assert php_after[0] != before[0] and php_after[1] != before[1]


def write_q18_deviation():
    """Each Q18 exchange keeps PHP's header and takes the body of the poll
    before the refused request, which `test_a_refused_generate_renumbers_nothing`
    checks is Django's state after it."""
    raw = (GOLDEN_DIR / HALF_WEIGHTS).read_bytes()
    php = read_transcript(GOLDEN_DIR / HALF_WEIGHTS)
    out = b""
    for name, before in Q18_EXCHANGES.items():
        start = raw.index(b"### " + name.encode())
        header = raw[start : raw.index(b"\nbody:\n", start)].decode()
        data = php[before]["body"]
        header = re.sub(r"length: \d+", f"length: {len(data)}", header)
        out += header.encode() + b"\nbody:\n" + data + b"\n"
    (GOLDEN_DIR / Q18_EXPECTED).parent.mkdir(parents=True, exist_ok=True)
    (GOLDEN_DIR / Q18_EXPECTED).write_bytes(out)
    listing = json.loads(DEVIATIONS_FILE.read_text())
    entries = [e for e in listing["deviations"] if e["quirk"] != "Q18"]
    entries.append(
        {
            "quirk": "Q18",
            "fixture": HALF_WEIGHTS,
            "exchanges": list(Q18_EXCHANGES),
            "reason": Q18_REASON,
            "expected": Q18_EXPECTED,
        }
    )
    listing["deviations"] = sorted(entries, key=lambda e: int(e["quirk"][1:]))
    DEVIATIONS_FILE.write_text(json.dumps(listing, indent=2, ensure_ascii=False) + "\n")


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the Q18 deviation"
)
def test_write_q18_deviation():
    """`WRITE_DEVIATIONS=1 uv run pytest racing/services/tests/test_master.py`"""
    write_q18_deviation()


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
        .filter(HAS_RESULT)
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
            elif HeatLane.objects.filter(round=round).filter(HAS_RESULT):
                reassign_master_heats(event)
            else:
                generate_schedule(round, rng.randint(1, 2))
            check_invariants(event, before, raced)


# ----- engine/master-heats.json through the services (#100) -----------------

MASTER_HEATS = read_cases(GOLDEN_DIR / "engine" / "master-heats.json")
REQUESTS = ("first", "next", "prev", "next-up")


def load_master_case(case):
    """The case's rounds and rows in a new event. Each PHP round is a round
    of its own group numbered by its level, made in roundid order so the ids
    keep PHP's order, and the rows are written in PHP's resultid order.
    Returns the event and `{PHP roundid: round id}`."""
    event = EventFactory()
    ids = {}
    for roundid, level in sorted(case["rounds"]):
        ids[roundid] = RoundFactory(group=GroupFactory(event=event), number=level).id
    if case["rows"]:
        racer = RacerFactory(group=GroupFactory(event=event))
        HeatLane.objects.bulk_create(
            HeatLane(
                event=event,
                round_id=ids[roundid],
                heat=heat,
                lane=lane,
                racer=racer,
                finish_time=time,
                finish_place=place,
                master_heat=n,
            )
            for roundid, heat, lane, time, place, n in case["rows"]
        )
    return event, ids


def service_navigation(event, ids, currents):
    """`master_target` over `master_index(event)` for each current heat the
    fixture names, in PHP's roundids. A PHP roundid with no round, the
    heat that does not exist, maps to an id no round has."""
    php_id = {v: k for k, v in ids.items()}
    index = master_index(event)
    missing = max(ids.values(), default=0) + 1000
    answers = {}
    for key in currents:
        current = None
        if key != "none":
            roundid, heat = map(int, key.split("/"))
            current = (ids.get(roundid, missing), heat)
        found = [master_target(index, current, r) for r in REQUESTS]
        answers[key] = [None if t is None else [php_id[t[0]], t[1]] for t in found]
    return answers


@pytest.mark.parametrize("case_id", list(MASTER_HEATS))
def test_the_services_number_and_navigate_as_php(case_id):
    """Each case of `engine/master-heats.json` loaded into the database, then
    renumbered by `reassign_master_heats`: every row gets the number PHP's
    `assign_master_heats()` wrote, and `master_target` over `master_index`
    answers as PHP's `get_next_heat()` did, before and after."""
    case = MASTER_HEATS[case_id]
    event, ids = load_master_case(case)
    assert (
        service_navigation(event, ids, case["navigation_before"])
        == (case["navigation_before"])
    )

    reassign_master_heats(event)

    rows = HeatLane.objects.filter(event=event).order_by("id")
    assert list(rows.values_list("master_heat", flat=True)) == case["masterheats"]
    assert (
        service_navigation(event, ids, case["navigation_after"])
        == (case["navigation_after"])
    )
