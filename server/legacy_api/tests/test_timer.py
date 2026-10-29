"""The timer protocol through `/action.php` (#32): the cases the ticket
lists beyond the PHP transcripts. A clock in the past is set by moving the
stored contact time back."""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from accounts.permissions import Perm
from core.models import ActionLog, RaceState
from legacy_api.tests.conftest import log_in, post
from racing.models import AuditEvent, TimerStatus
from racing.services import timer as timer_service

pytestmark = pytest.mark.django_db

UA = "DerbyNet-Timer/test (jar)"
Action = AuditEvent.Action


def reply(response) -> dict:
    return json.loads(response.content)


@pytest.fixture
def coordinator(client):
    found = reply(
        post(client, "action=role.login&name=RaceCoordinator&password=doyourbest")
    )
    assert found["outcome"]["code"] == "success"
    return client


@pytest.fixture
def jar(event):
    jar = Client(headers={"User-Agent": UA})
    log_in(jar, Perm.TIMER_MESSAGE, role="Timer")
    return jar


def ok(client, body) -> dict:
    found = reply(post(client, body))
    assert found["outcome"]["code"] == "success", found
    return found


def message(client, fields: str) -> str:
    response = post(client, f"action=timer-message&message={fields}")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/xml; charset=utf-8"
    return response.content.decode()


def timer_state(coordinator) -> dict:
    return reply(coordinator.get("/action.php?query=poll.coordinator"))["timer-state"]


def racing_round(coordinator, racers=4):
    ok(coordinator, "action=settings.write&unused-lane-mask=0&n-lanes=4")
    for car in range(101, 101 + racers):
        ok(
            coordinator,
            f"action=racer.import&firstname=F{car}&lastname=L{car}"
            f"&carnumber={car}&partition=Lions",
        )
    ok(coordinator, "action=racer.bulk&who=all&what=checkin")
    round_id = reply(coordinator.get("/action.php?query=poll.coordinator"))["rounds"][
        0
    ]["roundid"]
    ok(coordinator, f"action=schedule.generate&roundid={round_id}")
    ok(coordinator, f"action=heat.select&roundid={round_id}&now_racing=1")
    return round_id


def connect(jar):
    message(jar, "HELLO")
    message(jar, "IDENTIFIED&lane_count=4&timer=TestTimer")


def move_contact_back(event, seconds):
    status = TimerStatus.objects.get(event=event)
    TimerStatus.objects.filter(pk=status.pk).update(
        last_contact=status.last_contact - timedelta(seconds=seconds)
    )


# ----- heartbeats -----


def test_a_bare_unhealthy_key_counts_as_unhealthy(event, coordinator, jar):
    connect(jar)
    assert timer_state(coordinator)["state"] == TimerStatus.State.CONNECTED
    message(jar, "HEARTBEAT&unhealthy")
    assert timer_state(coordinator)["state"] == TimerStatus.State.SEARCHING


def test_confirmed_never_takes_a_staging_timer_back_to_connected(
    event, coordinator, jar
):
    racing_round(coordinator)
    connect(jar)
    assert timer_state(coordinator)["state"] == TimerStatus.State.STAGING
    body = message(jar, "HEARTBEAT&confirmed=1")
    assert "<heat-ready" not in body
    assert timer_state(coordinator)["state"] == TimerStatus.State.STAGING


def test_an_unconfirmed_timer_gets_heat_ready_on_every_heartbeat(
    event, coordinator, jar
):
    racing_round(coordinator)
    message(jar, "IDENTIFIED&lane_count=4&confirmed=0")
    for _ in range(3):
        assert "<heat-ready" in message(jar, "HEARTBEAT&confirmed=0")


# ----- the contact timeout -----


def test_a_timer_silent_for_over_a_minute_is_not_connected(event, coordinator, jar):
    connect(jar)
    move_contact_back(event, 59)
    assert timer_state(coordinator)["state"] == TimerStatus.State.CONNECTED
    move_contact_back(event, 2)
    assert timer_state(coordinator)["state"] == TimerStatus.State.NOT_CONNECTED
    # The read wrote nothing: the timeout is written by the explicit check.
    assert TimerStatus.objects.get(event=event).state == TimerStatus.State.CONNECTED
    assert not ActionLog.objects.filter(request__action="timer.*overdue").exists()


def test_the_timeout_is_written_and_logged_by_the_next_message(event, coordinator, jar):
    connect(jar)
    move_contact_back(event, 61)
    message(jar, "HEARTBEAT")
    assert ActionLog.objects.filter(request__action="timer.*overdue").count() == 1
    # A heartbeat from a timer that was not connected brings it back.
    assert timer_state(coordinator)["state"] == TimerStatus.State.CONNECTED


def test_check_contact_writes_the_timeout_once(event, jar):
    connect(jar)
    move_contact_back(event, 61)
    assert timer_service.check_contact(event)
    assert not timer_service.check_contact(event)
    assert TimerStatus.objects.get(event=event).state == TimerStatus.State.NOT_CONNECTED
    assert ActionLog.objects.filter(request__action="timer.*overdue").count() == 1


# ----- malfunctions -----


def test_an_undetectable_malfunction_lasts_a_minute(event, coordinator, jar):
    connect(jar)
    message(jar, "MALFUNCTION&detectable=0&error=Gate%20silent")
    found = timer_state(coordinator)
    assert (found["message"], found["icon"]) == (
        "Gate silent",
        "img/status/trouble.png",
    )
    status = TimerStatus.objects.get(event=event)
    TimerStatus.objects.filter(pk=status.pk).update(
        malfunction_expires_at=timezone.now() - timedelta(seconds=1)
    )
    found = timer_state(coordinator)
    assert found["icon"] == "img/status/ok.png"
    assert found["message"].startswith("CONNECTED")


def test_a_malfunction_is_audited_once_while_troubled(event, jar):
    connect(jar)
    message(jar, "MALFUNCTION&error=Unplugged")
    message(jar, "MALFUNCTION&error=Still%20unplugged")
    rows = AuditEvent.objects.filter(action=Action.TIMER_MALFUNCTION)
    assert [row.detail for row in rows] == [{"other": "Unplugged"}]


# ----- competing timers -----


def test_a_second_timer_competes_within_a_second(event, jar):
    message(jar, "HELLO")
    other = Client(headers={"User-Agent": "Other/1.0"})
    log_in(other, Perm.TIMER_MESSAGE, role="Timer")
    assert f"<competing>{UA}</competing>" in message(other, "HELLO")
    # The first timer keeps the connection, so the second hears it competes.
    assert "<competing>" in message(other, "HEARTBEAT")
    assert "<competing>" not in message(jar, "HEARTBEAT")


def test_a_second_timer_after_the_window_takes_over(event, jar):
    message(jar, "HELLO")
    move_contact_back(event, 2)
    other = Client(headers={"User-Agent": "Other/1.0"})
    log_in(other, Perm.TIMER_MESSAGE, role="Timer")
    assert "<competing>" not in message(other, "HELLO")
    assert "<competing>Other/1.0</competing>" in message(jar, "HEARTBEAT")


# ----- results -----


def test_success_follows_a_wrong_heat_failure(event, coordinator, jar):
    round_id = racing_round(coordinator)
    connect(jar)
    message(jar, "STARTED")
    body = message(
        jar,
        f"FINISHED&roundid={round_id}&heat=9&lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4",
    )
    assert (
        f"<failure code='wrong-heat'>Expecting heat results for roundid {round_id}"
        " and heat 1</failure>\n  <success/>\n" in body
    )


def test_a_heats_messages_are_in_the_audit(event, coordinator, jar):
    round_id = racing_round(coordinator)
    connect(jar)
    message(jar, "HEARTBEAT")
    message(jar, "STARTED")
    message(jar, "FINISHED&lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    messages = [
        row.detail["message"]
        for row in AuditEvent.objects.filter(action=Action.TIMER_MESSAGE).order_by("id")
    ]
    assert messages == ["HELLO", "IDENTIFIED", "HEARTBEAT", "STARTED", "FINISHED"]
    heat_1 = AuditEvent.objects.filter(round_id=round_id, heat=1)
    assert set(heat_1.values_list("action", flat=True)) >= {
        Action.TIMER_MESSAGE,
        Action.TIMER_STATE_CHANGED,
        Action.HEAT_RESULT_FROM_TIMER,
    }
    changes = [
        (row.detail["from"], row.detail["to"])
        for row in AuditEvent.objects.filter(
            action=Action.TIMER_STATE_CHANGED
        ).order_by("id")
    ]
    State = TimerStatus.State
    assert changes[-2:] == [
        (State.STAGING, State.RUNNING),
        (State.RUNNING, State.STAGING),
    ]


def test_a_failing_audit_never_changes_the_reply(event, coordinator, jar, monkeypatch):
    racing_round(coordinator)
    connect(jar)
    expected = message(jar, "HEARTBEAT")
    monkeypatch.setattr(timer_service.audit, "write", lambda *a: False)
    assert message(jar, "HEARTBEAT") == expected


# ----- remote start -----


def test_one_remote_start_reaches_one_heartbeat(event, coordinator, jar):
    message(jar, "HELLO&remote-start=YES")
    assert ok(coordinator, "action=timer.remote-start")
    bodies = [message(jar, "HEARTBEAT&remote-start=YES") for _ in range(3)]
    assert [b.count("<remote-start/>") for b in bodies] == [1, 0, 0]


def test_remote_start_is_refused_a_minute_after_the_offer(event, coordinator, jar):
    message(jar, "HELLO&remote-start=YES")
    status = TimerStatus.objects.get(event=event)
    TimerStatus.objects.filter(pk=status.pk).update(
        remote_start_seen_at=timezone.now() - timedelta(seconds=61)
    )
    found = reply(post(coordinator, "action=timer.remote-start"))
    assert found["outcome"]["code"] == "no-remote-start"


# ----- flags -----


def test_an_assigned_flag_goes_out_once_with_a_query(event, coordinator, jar):
    message(jar, "FLAGS&flag-debug=bool:false&ports=a,b&device-T=Timer")
    ok(coordinator, "action=timer.assign-flag&flag=debug&value=true")
    ok(coordinator, "action=timer.assign-port&port=b")
    ok(coordinator, "action=timer.assign-device&device=")
    body = message(jar, "HEARTBEAT")
    assert (
        '<assign-flag flag="debug" value="true"/>\n'
        '<assign-port port="b"/>\n'
        "<assign-device/>\n"
        "  <query/>\n"
    ) in body
    assert "<assign-" not in message(jar, "HEARTBEAT")


def test_flags_replace_every_setting_with_nothing_pending(event, coordinator, jar):
    message(jar, "FLAGS&flag-a=int:1&flag-b=int:2&desc-a=First")
    ok(coordinator, "action=timer.assign-flag&flag=a&value=9")
    message(jar, "FLAGS&flag-a=int:3&flag-c=string:x")
    found = reply(coordinator.get("/action.php?query=timer.settings"))
    assert [(f["name"], f["value"], f["description"]) for f in found["flags"]] == [
        ("a", 1, "First"),
        ("a", 3, None),
        ("c", "x", None),
    ]


# ----- the timer test -----


def test_the_timer_test_stages_its_lanes_and_returns(event, coordinator, jar):
    round_id = racing_round(coordinator)
    connect(jar)
    ok(coordinator, "action=timer.test&tt-mask=2")
    body = message(jar, "HEARTBEAT")
    assert "\n <abort />\n" in body
    assert (
        '<heat-ready lane-mask="13" class="Timer Test" round="1" roundid="-100"'
        ' heat="2" lanes="4"/>'
    ) in body
    message(jar, "STARTED")
    message(jar, "FINISHED&lane1=2.5&lane3=2.4&lane4=2.6")
    poll = reply(coordinator.get("/action.php?query=poll.timer.test"))
    assert poll["tt"]["heat"] == 2
    assert poll["tt"]["mask"] == 2
    assert [(r["lane"], r["place"]) for r in poll["tt"]["results"]] == [
        (1, 2),
        (3, 1),
        (4, 3),
    ]
    ok(coordinator, "action=timer.test&test-mode=0")
    current = reply(coordinator.get("/action.php?query=poll.coordinator"))[
        "current-heat"
    ]
    assert (current["roundid"], current["heat"], current["now_racing"]) == (
        round_id,
        1,
        False,
    )
    # The staged test heat is no longer current.
    assert "\n <abort />\n" in message(jar, "HEARTBEAT")


def test_timer_test_needs_an_operation(event, coordinator):
    found = reply(post(coordinator, "action=timer.test"))
    assert found["outcome"]["code"] == "unknown-operation"


# ----- refusals and errors -----


@pytest.mark.parametrize(
    "request_body",
    [
        "action=timer.assign-flag&flag=a&value=1",
        "action=timer.assign-port&port=a",
        "action=timer.assign-device&device=a",
        "action=timer.remote-start",
        "action=timer.test&test-mode=1",
    ],
)
def test_the_timer_role_cannot_use_the_coordinators_controls(event, jar, request_body):
    """S21: PHP checks no permission on these, except remote start."""
    found = reply(post(jar, request_body))
    assert found["outcome"]["code"] == "notauthorized"


@pytest.mark.parametrize(
    "query", ["timer.settings", "timer.log&seek=0", "poll.timer.test"]
)
def test_the_timer_queries_need_control_race(event, jar, query):
    found = reply(jar.get(f"/action.php?query={query}"))
    assert found["outcome"]["code"] == "notauthorized"


def test_a_timer_message_that_raises_still_answers_xml(event, jar, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(timer_service, "handle_message", broken)
    body = message(jar, "HEARTBEAT")
    assert '<failure code="server-error">Unexpected server error</failure>' in body


def test_identified_takes_the_timers_lane_count(event, coordinator, jar):
    """C26, kept for parity: the timer's lane count wins."""
    ok(coordinator, "action=settings.write&n-lanes=6")
    message(jar, "IDENTIFIED&lane_count=4")
    assert timer_state(coordinator)["lanes"] == 4
    message(jar, "IDENTIFIED&lane_count=abc")
    assert timer_state(coordinator)["lanes"] == 4


def test_racing_off_while_staging_aborts_the_timer(event, coordinator, jar):
    racing_round(coordinator)
    connect(jar)
    ok(coordinator, "action=heat.select&now_racing=0")
    assert RaceState.objects.get(event=event).racing is False
    assert "\n  <abort/>\n" in message(jar, "HEARTBEAT")
    assert timer_state(coordinator)["state"] == TimerStatus.State.CONNECTED


def test_a_timeout_is_audited_as_its_own_change(event, coordinator, jar):
    racing_round(coordinator)
    connect(jar)
    move_contact_back(event, 120)
    silent_since = TimerStatus.objects.get(event=event).last_contact
    message(jar, "HEARTBEAT")
    State = TimerStatus.State
    changes = AuditEvent.objects.filter(action=Action.TIMER_STATE_CHANGED).order_by(
        "id"
    )
    timeout, after = list(changes)[-2:]
    assert (timeout.detail, timeout.recorded_at) == (
        {"from": State.STAGING, "to": State.NOT_CONNECTED},
        silent_since + timedelta(seconds=60),
    )
    assert after.detail == {"from": State.NOT_CONNECTED, "to": State.STAGING}
