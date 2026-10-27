"""The #7 timer and replay transcripts replayed through the result and heat
services. After each message the current heat, racing, the refused count
and the replay commands equal what PHP answered.

The timer replies' `heat-ready` names the current heat; the polls name
racing and the refused count too. The XML and the timer state belong to
#32, so only the heat and the failure code are compared. `STARTED` is
#32's too; here it only queues the replay `RACE_STARTS` that PHP sends.
"""

import json
import re

import pytest

from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.services import replay
from racing.services.heats import rerun, select_heat
from racing.services.results import Outcome, record_timer_result
from racing.services.tests.race import (
    connect_replay,
    current,
    drain,
    fields,
    lanes,
    settings,
    setup_race,
    state,
)

pytestmark = pytest.mark.django_db

TIMER = GOLDEN_DIR / "http" / "timer"


def transcript(name):
    return read_transcript(TIMER / f"{name}.http")


def heat_ready(body: bytes) -> int | None:
    match = re.search(rb'<heat-ready [^>]* heat="(\d+)"', body)
    return int(match[1]) if match else None


def failure(body: bytes) -> str | None:
    match = re.search(rb"<failure code='([^']+)'", body)
    return match[1].decode() if match else None


def django_ids(posted, round):
    """The fixture's one round is PHP's round 1 (ADR-12)."""
    return [(k, str(round.id) if (k, v) == ("roundid", "1") else v) for k, v in posted]


def replay_timer(event, round, exchanges):
    """Each timer message through the services, checked against PHP's
    reply; each coordinator poll checked against the race state."""
    checked = 0
    for name, exchange in exchanges.items():
        body = exchange.get("request-body", "")
        if "message=FINISHED" in body:
            recorded = record_timer_result(event, django_ids(fields(body), round))
            code = failure(exchange["body"])
            expected = Outcome(code) if code else Outcome.RECORDED
            if "all-zeroes" in name:
                expected = Outcome.ALL_ZERO
            assert recorded.outcome is expected, name
            checked += 1
        elif "message=STARTED" in body:
            replay.send(event.id, replay.race_starts(settings(event)))
        if "poll.coordinator" in exchange["request"]:
            poll = json.loads(exchange["body"])
            heat = poll["current-heat"]
            assert current(event) == (round.id, heat["heat"], heat["now_racing"]), name
            assert state(event).refused_results == poll["refused-results"], name
            checked += 1
        elif (heat := heat_ready(exchange["body"])) is not None:
            assert current(event)[:2] == (round.id, heat), name
    return checked


def test_race_staging_results_and_the_all_zero_stop():
    replay_state, round = setup_race()
    event = replay_state.event
    select_heat(event, round_id=round.id, now_racing=True)
    assert replay_timer(event, round, transcript("race-staging-and-results")) == 6
    assert lanes(round, 1) == {
        1: (3.123, 2),
        2: (3.456, 3),
        3: (3.789, 4),
        4: (2.987, 1),
    }
    assert lanes(round, 2) == {1: (None, 2), 2: (None, 1), 3: (None, 4), 4: (None, 3)}
    assert lanes(round, 3) == {1: (3.1, 1), 2: (3.2, 2), 3: (3.3, 3), 4: (3.4, 4)}
    assert lanes(round, 4) == {1: (3.5, 1), 2: (3.6, 2), 3: (3.7, 3), 4: (3.8, 4)}
    assert (
        replay_timer(event, round, transcript("finished-all-zeroes-and-not-racing"))
        == 3
    )
    assert lanes(round, 5) == {lane: (None, None) for lane in (1, 2, 3, 4)}


def test_reverse_lanes_mirrors_the_timers_lanes():
    replay_state, round = setup_race(racers=3, mask=1)
    event = replay_state.event
    select_heat(event, round_id=round.id, now_racing=True)
    settings(event).__class__.objects.filter(event=event).update(reverse_lanes=True)
    assert replay_timer(event, round, transcript("reverse-lanes")) == 2
    # Timer lanes 2 to 4 are track lanes 3 to 1; track lane 4 got no time.
    assert lanes(round, 1) == {2: (3.3, None), 3: (3.2, None), 4: (None, None)}


def test_the_replay_command_sequence():
    replay_state, round = setup_race(racers=5, first_car=201)
    event = replay_state.event
    exchanges = read_transcript(
        GOLDEN_DIR / "http" / "replay" / "command-sequence.http"
    )

    def polled(name):
        connect_replay(event)
        expected = json.loads(exchanges[name]["body"])["replay"]
        assert drain(event) == expected, name

    connect_replay(event)
    select_heat(event, round_id=round.id, now_racing=True)
    polled("002 replay-after-heat-select-sees-cancel-and-start")
    select_heat(event, round_id=round.id, now_racing=False)
    select_heat(event, round_id=round.id, now_racing=True)
    polled("003 replay-after-racing-off-and-on")
    replay.send(event.id, replay.race_starts(settings(event)))
    polled("005 replay-after-gate-opens")
    body = exchanges["006 timer-finished"]["request-body"]
    assert record_timer_result(event, fields(body)).outcome is Outcome.RECORDED
    polled("007 replay-after-finish-playing")
    polled("008 replay-after-playback-finished")
    assert rerun(event, "last") is None
    rerun_reply = json.loads(exchanges["009 rerun-last-heat"]["body"])
    assert current(event) == (round.id, 1, True)
    assert rerun_reply["current-heat"]["heat"] == 1
    assert rerun_reply["last-heat"] == "recoverable"
    polled("010 replay-after-rerun")


def test_the_six_commands_of_the_basic_racing_script():
    """`testing/test-basic-racing.sh` checks this exact sequence after its
    first heat."""
    replay_state, round = setup_race(racers=4, partition="Lions & Tigers")
    event = replay_state.event
    connect_replay(event)
    select_heat(event, round_id=round.id, now_racing=True)
    replay.send(event.id, replay.race_starts(settings(event)))
    record_timer_result(
        event, fields("lane1=3.3294&lane2=3.4179&lane3=3.8182&lane4=2.2401")
    )
    assert drain(event) == [
        "CANCEL",
        "START Lions & Tigers_Round1_Heat01",
        "RACE_STARTS 4000 2 50",
        "REPLAY 4000 2 50",
        "CANCEL",
        "START Lions & Tigers_Round1_Heat02",
    ]


def test_no_commands_queue_while_no_replay_client_polls():
    replay_state, round = setup_race()
    event = replay_state.event
    select_heat(event, round_id=round.id, now_racing=True)
    assert drain(event) == []
