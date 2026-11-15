"""The timer protocol through `/action.php` (#32): the cases the ticket
lists beyond the PHP transcripts. A clock in the past is set by moving the
stored contact time back."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from accounts.permissions import Perm
from core.models import ActionLog, RaceSettings, RaceState
from legacy_api.tests.conftest import log_in, post
from racing.models import AuditEvent, HeatLane, TimerSetting, TimerStatus
from racing.services import polls
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
    # D100: the read wrote nothing; the next message writes the timeout.
    assert TimerStatus.objects.get(event=event).state == TimerStatus.State.CONNECTED
    assert not ActionLog.objects.filter(request__action="timer.*overdue").exists()


def test_the_timeout_is_written_and_logged_by_the_next_message(event, coordinator, jar):
    connect(jar)
    move_contact_back(event, 61)
    message(jar, "HEARTBEAT")
    assert ActionLog.objects.filter(request__action="timer.*overdue").count() == 1
    # A heartbeat from a timer that was not connected brings it back.
    assert timer_state(coordinator)["state"] == TimerStatus.State.CONNECTED


@pytest.mark.parametrize(
    ("silent", "state", "overdue"),
    [
        (59, TimerStatus.State.CONNECTED, 0),
        (61, TimerStatus.State.NOT_CONNECTED, 1),
    ],
    ids=["59-s", "61-s"],
)
def test_a_rerun_applies_the_contact_timeout_first(
    event, coordinator, jar, silent, state, overdue
):
    """D100 (#307): heat.rerun, a POST, applies the timeout before it moves a
    staging timer back to connected, as PHP's `new TimerState()` does
    (`action.heat.rerun.inc:59-63`). A timer silent for 59 s is connected
    again; one silent for 61 s is not connected, with one overdue row and
    its audited change from staging."""
    racing_round(coordinator)
    connect(jar)
    assert TimerStatus.objects.get(event=event).state == TimerStatus.State.STAGING
    move_contact_back(event, silent)
    ok(coordinator, "action=heat.rerun&heat=current")
    assert TimerStatus.objects.get(event=event).state == state
    assert ActionLog.objects.filter(request__action="timer.*overdue").count() == overdue
    changes = AuditEvent.objects.filter(
        action=Action.TIMER_STATE_CHANGED, detail__to=TimerStatus.State.NOT_CONNECTED
    )
    assert [c.detail["from"] for c in changes] == [TimerStatus.State.STAGING] * overdue


SECOND = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)


def test_the_timeout_row_is_dated_when_the_minute_ran_out(event, jar):
    """#307, D100: the `timer.*overdue` row the next message writes, and its
    audited change, are dated to the first moment the timeout rule holds: a
    contact 0.9 s past SECOND times out at 61 s past SECOND, the whole
    second 61 s after its own, and not 1 microsecond before. The
    Retrospective lists the row there; PHP writes it at the first read after
    the minute (`timer-state.inc:72-79`)."""
    connect(jar)
    contact = SECOND + timedelta(seconds=0.9)
    TimerStatus.objects.filter(event=event).update(last_contact=contact)
    at = SECOND + timedelta(seconds=61)
    row = TimerStatus.objects.get(event=event)
    assert timer_service.TimerState.read(row, at).timed_out
    assert not timer_service.TimerState.read(
        row, at - timedelta(microseconds=1)
    ).timed_out
    message(jar, "HEARTBEAT")
    assert ActionLog.objects.get(request__action="timer.*overdue").received == at
    change = AuditEvent.objects.get(
        action=Action.TIMER_STATE_CHANGED, detail__to=TimerStatus.State.NOT_CONNECTED
    )
    assert change.recorded_at == at


def test_a_connected_timers_age_counts_whole_seconds(event):
    """#307: the status text's age is PHP's `time() - $this->last_contact`
    (`timer-state.inc:97-102`), in whole seconds: a contact 0.9 s past
    SECOND, read 60.2 s past it, is 60 sec. old, though only 59.3 s have
    passed."""
    contact = SECOND + timedelta(seconds=0.9)
    now = SECOND + timedelta(seconds=60.2)
    assert (now - contact).total_seconds() == pytest.approx(59.3)
    row = TimerStatus(
        event=event, state=TimerStatus.State.CONNECTED, last_contact=contact
    )
    found = timer_service.TimerState.read(row, now)
    assert polls._timer_status(found, now) == (
        "CONNECTED (60 sec.)",
        "img/status/ok.png",
    )


@pytest.mark.parametrize(
    ("elapsed", "timed_out"),
    [(60.05, False), (60.1, True)],
    ids=["60.05-s-later", "60.1-s-later"],
)
def test_the_timeout_counts_whole_seconds(event, elapsed, timed_out):
    """#307: PHP's `last_contact + 60 < time()` (`timer-state.inc:72`) compares
    whole seconds. A contact 0.9 s past a whole second is timed out once the
    clock reaches 61 s past that whole second: 60.05 s after the contact it
    is not, 60.1 s after it is."""
    contact = SECOND + timedelta(seconds=0.9)
    now = contact + timedelta(seconds=elapsed)
    # 60.1 s after the contact is 61 s past SECOND; 60.05 s is short of it.
    assert (now >= SECOND + timedelta(seconds=61)) is timed_out
    row = TimerStatus(
        event=event, state=TimerStatus.State.CONNECTED, last_contact=contact
    )
    found = timer_service.TimerState.read(row, now)
    assert found.timed_out is timed_out
    assert (found.state == TimerStatus.State.NOT_CONNECTED) is timed_out


def test_a_rerun_leaves_a_searching_timer_searching(event, coordinator, jar):
    """#307: heat.rerun moves only a staging or running timer back to
    connected, as `action.heat.rerun.inc:60-63` does; a searching one is
    left as it is."""
    racing_round(coordinator)
    message(jar, "HELLO")
    assert TimerStatus.objects.get(event=event).state == TimerStatus.State.SEARCHING
    ok(coordinator, "action=heat.rerun&heat=current")
    assert TimerStatus.objects.get(event=event).state == TimerStatus.State.SEARCHING


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


def test_a_competing_timers_messages_still_change_the_timer(event, jar):
    """#307: the server acts on a second client's messages, as PHP does
    (`action.timer-message.inc:72`, `:98-127`, `:170-190`): its HELLO is told
    it competes and still sets searching, its IDENTIFIED renames the timer,
    and its unhealthy heartbeat sets searching again. The status card says
    so (`ui/timer/status.html`)."""
    connect(jar)
    other = Client(headers={"User-Agent": "Other/1.0"})
    log_in(other, Perm.TIMER_MESSAGE, role="Timer")
    State = TimerStatus.State

    def status():
        found = TimerStatus.objects.get(event=event)
        return found.agent, found.state, found.timer_type

    assert status() == (UA, State.CONNECTED, "TestTimer")
    assert f"<competing>{UA}</competing>" in message(other, "HELLO")
    assert status() == (UA, State.SEARCHING, "TestTimer")
    assert "<competing>" not in message(other, "IDENTIFIED&timer=Other")
    assert status() == (UA, State.CONNECTED, "Other")
    assert f"<competing>{UA}</competing>" in message(other, "HEARTBEAT&unhealthy")
    assert status() == (UA, State.SEARCHING, "Other")


def test_no_client_competes_before_one_says_hello(event, jar):
    """#307: with no client recorded, as before any HELLO, nothing competes:
    PHP checks `$ua &&` first (`action.timer-message.inc:86`, `:192`)."""
    assert "<competing>" not in message(jar, "HEARTBEAT")
    assert TimerStatus.objects.get(event=event).agent == ""
    other = Client(headers={"User-Agent": "Other/1.0"})
    log_in(other, Perm.TIMER_MESSAGE, role="Timer")
    assert "<competing>" not in message(other, "HELLO")
    assert TimerStatus.objects.get(event=event).agent == "Other/1.0"


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


def reported_lanes(event):
    return TimerStatus.objects.get(event=event).reported_lane_count


def test_identified_sets_the_lane_count_while_nothing_is_scheduled(
    event, coordinator, jar
):
    """D74, C26: with no heat scheduled, the timer's lane count wins."""
    ok(coordinator, "action=settings.write&n-lanes=6")
    message(jar, "IDENTIFIED&lane_count=4")
    assert timer_state(coordinator)["lanes"] == 4
    assert reported_lanes(event) == 4
    message(jar, "IDENTIFIED&lane_count=abc")
    assert timer_state(coordinator)["lanes"] == 4
    assert reported_lanes(event) == 4


def test_identified_keeps_the_lane_count_once_heats_are_scheduled(
    event, coordinator, jar
):
    """D74, C26: a schedule keeps the race's lane count; the timer's count
    is stored to show beside it. PHP writes the timer's count."""
    racing_round(coordinator)
    message(jar, "HELLO")
    message(jar, "IDENTIFIED&lane_count=6")
    assert RaceSettings.objects.get(event=event).lane_count == 4
    assert timer_state(coordinator)["lanes"] == 4
    assert reported_lanes(event) == 6


def test_a_raced_schedule_still_keeps_the_lane_count(event, coordinator, jar):
    """D74: every heat run is still a schedule."""
    racing_round(coordinator)
    HeatLane.objects.filter(event=event).update(
        finish_time=2.5, completed=timezone.now()
    )
    message(jar, "IDENTIFIED&lane_count=6")
    assert RaceSettings.objects.get(event=event).lane_count == 4
    assert reported_lanes(event) == 6


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
        timer_service.timed_out_at(silent_since),
    )
    assert after.detail == {"from": State.NOT_CONNECTED, "to": State.STAGING}


# ----- odd input from the timer -----


# Each text a timer message stores, with the width its column had before
# #307: (id, message, model, column, width). PHP keeps every one whole,
# since SQLite enforces no VARCHAR (the PHP probe in #307's PR body).
TIMER_TEXT = [
    ("interface", "HELLO&interface={}", TimerStatus, "interface", 200),
    ("timer", "IDENTIFIED&timer={}", TimerStatus, "timer_type", 200),
    ("human", "IDENTIFIED&human={}", TimerStatus, "human", 200),
    ("ident", "IDENTIFIED&ident={}", TimerStatus, "ident", 200),
    ("options", "IDENTIFIED&options={}", TimerStatus, "options", 500),
    ("vid", "IDENTIFIED&vid={}", TimerStatus, "vid", 20),
    ("pid", "IDENTIFIED&pid={}", TimerStatus, "pid", 20),
    (
        "error",
        "MALFUNCTION&detectable=1&error={}",
        TimerStatus,
        "malfunction_message",
        500,
    ),
    ("flag-name", "FLAGS&flag-{}=bool:true", TimerSetting, "name", 100),
    ("flag-type", "FLAGS&flag-f={}:true", TimerSetting, "value_type", 10),
    ("flag-value", "FLAGS&flag-f=bool:{}", TimerSetting, "value", 100),
    ("desc", "FLAGS&flag-f=bool:true&desc-f={}", TimerSetting, "description", 500),
    ("ports", "FLAGS&ports={}", TimerSetting, "value", 100),
    ("device-name", "FLAGS&device-{}=Timer", TimerSetting, "name", 100),
    ("device-desc", "FLAGS&device-T={}", TimerSetting, "description", 500),
]


def stored(model, event, column) -> list[str]:
    return [
        v for v in model.objects.filter(event=event).values_list(column, flat=True) if v
    ]


@pytest.mark.parametrize("over", [0, 1], ids=["at-width", "one-over"])
@pytest.mark.parametrize(
    ("fields", "model", "column", "width"),
    [row[1:] for row in TIMER_TEXT],
    ids=[row[0] for row in TIMER_TEXT],
)
def test_timer_text_is_kept_whole(event, jar, fields, model, column, width, over):
    """#307: a text at its column's old width, and one character over it, is
    stored whole with a plain success, on SQLite and Postgres, as PHP keeps
    it (`write_raceinfo()`, `data.inc:108-119`; `decode_timer_flags()`,
    `timer-flags.inc:24-59`)."""
    text = "x" * (width + over)
    body = message(jar, fields.format(text))
    assert "<success/>" in body
    assert "<failure" not in body
    assert text in stored(model, event, column)


@pytest.mark.parametrize("over", [0, 1], ids=["at-width", "one-over"])
def test_the_user_agent_is_kept_whole(event, over):
    """#307: the client's User-Agent, at 200 characters and at 201, is stored
    whole (`action.timer-message.inc:94`)."""
    agent = "u" * (200 + over)
    client = Client(headers={"User-Agent": agent})
    log_in(client, Perm.TIMER_MESSAGE, role="Timer")
    assert "<success/>" in message(client, "HELLO")
    assert TimerStatus.objects.get(event=event).agent == agent


@pytest.mark.parametrize(
    ("second", "competes"),
    [("u" * 200 + "a", False), ("u" * 200 + "b", True)],
    ids=["same-agent", "differs-at-201"],
)
def test_long_user_agents_are_told_apart_by_every_character(event, second, competes):
    """#307: two User-Agents equal in their first 200 characters are two
    clients, as PHP compares them whole (`action.timer-message.inc:84-94`,
    `:191-194`); the same User-Agent is one."""
    first = Client(headers={"User-Agent": "u" * 200 + "a"})
    other = Client(headers={"User-Agent": second})
    log_in(first, Perm.TIMER_MESSAGE, role="Timer")
    log_in(other, Perm.TIMER_MESSAGE, role="Timer")
    message(first, "HELLO")
    assert ("<competing>" in message(other, "HEARTBEAT")) is competes
    assert ("<competing>" in message(other, "HELLO")) is competes


def test_a_long_flag_name_keeps_its_description(event, coordinator, jar):
    """#307: `desc-` finds a flag whose name is 101 characters long, past
    the 100 its column held."""
    name = "n" * 101
    assert len(name) == 101
    message(jar, f"FLAGS&flag-{name}=bool:true&desc-{name}=Help")
    found = reply(coordinator.get("/action.php?query=timer.settings"))
    assert [(f["name"], f["description"]) for f in found["flags"]] == [(name, "Help")]


@pytest.mark.parametrize(
    ("lanes", "kept"),
    [
        ("1", 1),
        ("0", 6),
        ("0.99", 6),
        ("1.0", 1),
        ("1.5", 1),
        ("32767", 32767),
        ("32767.5", 32767),
        ("32768", 6),
        ("4.5", 4),
        ("1e3", 1000),
    ],
    ids=[
        "smallest",
        "one-under",
        "fraction-under-1",
        "one-as-fraction",
        "fraction-over-1",
        "largest",
        "fraction-over-largest",
        "one-over",
        "fraction",
        "exponent",
    ],
)
def test_a_lane_count_is_a_number_from_1_to_32767(event, coordinator, jar, lanes, kept):
    """D99 (#307): IDENTIFIED cuts a lane count PHP reads as a number toward
    zero, then takes it from 1 to 32767: 0.99 becomes 0 and is ignored, as 0
    and 32768 are, with the same reply; 1.5 is taken as 1 and 32767.5 as
    32767. PHP ignores 0 too, and stores the others as sent
    (`action.timer-message.inc:99-104`)."""
    ok(coordinator, "action=settings.write&n-lanes=6")
    assert "<success/>" in message(jar, f"IDENTIFIED&lane_count={lanes}")
    assert timer_state(coordinator)["lanes"] == kept
    assert reported_lanes(event) == (None if kept == 6 else kept)


@pytest.mark.parametrize("lanes", ["1e400", "70000", "-3", "abc"])
def test_an_unusable_lane_count_is_ignored(event, coordinator, jar, lanes):
    """D99 (#307): PHP stores each but `-3` as sent."""
    ok(coordinator, "action=settings.write&n-lanes=6")
    assert "<success/>" in message(jar, f"IDENTIFIED&lane_count={lanes}")
    assert timer_state(coordinator)["lanes"] == 6


@pytest.mark.parametrize("mask", ["-1", "99999999999"])
def test_an_out_of_range_test_mask_still_starts_the_test(event, coordinator, mask):
    ok(coordinator, f"action=timer.test&tt-mask={mask}")
    poll = reply(coordinator.get("/action.php?query=poll.timer.test"))
    assert poll["current-heat"]["roundid"] == -100
