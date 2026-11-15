"""The message relay and the replay queue (#34): `message.send`,
`message.retrieve`, `replay.message`, `replay.test` and `replay.trigger`,
behind the replay login of D16."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from django.utils import timezone

from accounts.permissions import Perm
from kiosks.models import ReplayStatus, SignalMessage
from legacy_api.tests.conftest import log_in, post
from racing.services import replay

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def act(client, body):
    return json.loads(post(client, body).content)


def send(client, **message):
    fields = {f"message[{key}]": value for key, value in message.items()}
    return act(client, urlencode({"action": "message.send", **fields}))


def retrieve(client, recipient):
    reply = act(client, f"action=message.retrieve&recipient={recipient}")
    return reply["messages"]


def test_a_directed_message_is_delivered_once(coordinator):
    reply = send(coordinator, recipient="AA", key="first-value")

    assert reply["outcome"]["summary"] == "success"
    assert retrieve(coordinator, "BB") == []
    assert retrieve(coordinator, "AA") == [{"recipient": "AA", "key": "first-value"}]
    assert retrieve(coordinator, "AA") == []


def test_a_broadcast_reaches_everyone_until_it_expires(coordinator, event):
    send(coordinator, type="solicitation", **{"from": "viewer-1"})

    assert retrieve(coordinator, "AA") == [{"type": "solicitation", "from": "viewer-1"}]
    assert len(retrieve(coordinator, "BB")) == 1
    SignalMessage.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert retrieve(coordinator, "AA") == []
    assert not SignalMessage.objects.exists()


def test_directed_messages_expire_after_15_seconds(coordinator):
    before = timezone.now()
    send(coordinator, recipient="AA")
    send(coordinator, type="broadcast")

    expiry = {
        m.recipient: (m.expires_at - before).total_seconds()
        for m in SignalMessage.objects.all()
    }

    assert 14 < expiry["AA"] <= 16
    assert 0 < expiry[""] <= 2


def test_the_text_form_is_decoded_when_read(coordinator):
    """PHP stores `message` as sent and json_decode()s it, null if not JSON."""
    act(coordinator, 'action=message.send&recipient=AA&message={"a":1}')
    act(coordinator, "action=message.send&recipient=AA&message=hello")
    # PHP decodes 1e999 as INF, which no JSON reply or column holds.
    act(coordinator, "action=message.send&recipient=AA&message=1e999")

    assert retrieve(coordinator, "AA") == [{"a": 1}, None, None]


def test_an_oversized_message_is_refused(coordinator):
    reply = send(coordinator, recipient="AA", sdp="x" * (64 * 1024))

    assert reply["outcome"]["code"] == "too-large"
    assert not SignalMessage.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        "action=message.send&message%5Brecipient%5D=AA",
        "action=message.retrieve&recipient=AA",
        "action=replay.message&status=0",
        "action=replay.test",
        "action=replay.trigger",
    ],
)
def test_the_photo_role_cannot_relay_or_run_replay(client, event, body):
    """D16: replay, the camera and their signaling need CONTROL_RACE."""
    log_in(client, Perm.PHOTO_UPLOAD | Perm.TIMER_MESSAGE, role="Photo")

    reply = act(client, body)

    assert reply["outcome"]["code"] == "notauthorized"


def test_an_anonymous_replay_trigger_is_refused_in_json(client, event):
    """C21: PHP's refusal writes XML into the JSON reply."""
    response = post(client, "action=replay.trigger")

    assert response["Content-Type"].startswith("application/json")
    assert json.loads(response.content)["outcome"]["code"] == "notauthorized"


def poll(client, status="0"):
    return act(client, f"action=replay.message&status={status}&finished-replay=0")[
        "replay"
    ]


def test_the_first_poll_is_greeted_and_marks_the_replay_connected(coordinator, event):
    assert poll(coordinator, "-2") == ["HELLO"]

    state = ReplayStatus.objects.get(event=event)
    assert state.state == ReplayStatus.State.CONNECTED
    assert state.reported == "-2"
    assert poll(coordinator) == []


def test_a_replay_silent_for_10_seconds_is_greeted_again(coordinator, event):
    poll(coordinator)
    ReplayStatus.objects.filter(event=event).update(
        last_contact=timezone.now() - timedelta(seconds=11)
    )

    assert poll(coordinator) == ["HELLO"]


def test_trigger_and_test_queue_their_commands_while_connected(coordinator, event):
    act(coordinator, "action=replay.trigger")
    poll(coordinator)

    assert act(coordinator, "action=replay.trigger")["outcome"]["code"] == "success"
    act(coordinator, "action=replay.test")

    assert poll(coordinator) == ["REPLAY 4000 2 50", "TEST 4000 2 50"]


def test_the_queue_is_per_event(coordinator, event):
    from core.tests.factories import EventFactory

    other = EventFactory()
    ReplayStatus.objects.filter(event=other).update(
        state=ReplayStatus.State.CONNECTED, last_contact=timezone.now()
    )
    replay.send(other.id, "CANCEL")
    poll(coordinator)

    assert poll(coordinator) == []


# ----- the contact timeout and age, in whole seconds (#341) -----

SECOND = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
"""A whole second. #341's probe, whose output is in its PR, made its
contacts at a fraction past one."""
MICROSECOND = timedelta(microseconds=1)


@pytest.fixture
def clock(monkeypatch):
    """`timezone.now()` for the server, set by the test before each request."""
    now = [SECOND]
    monkeypatch.setattr(timezone, "now", lambda: now[0])
    return now


def replay_state(client) -> dict:
    found = json.loads(client.get("/action.php?query=poll&values=replay-state").content)
    return found["replay-state"]


CONNECTED = (True, "Ready (10 sec.)", ["TEST 4000 2 50"])
LAPSED = (False, "NOT CONNECTED", ["HELLO"])


def read_at(client, clock, contact: timedelta, read: timedelta):
    """A replay client's poll at `contact`, then at `read` the coordinator's
    replay state, `replay.test` and the client's next poll: (connected, the
    status message, the commands that poll gets)."""
    clock[0] = contact
    assert poll(client) == ["HELLO"]
    clock[0] = read
    found = replay_state(client)
    act(client, "action=replay.test")
    return found["connected"], found["message"], poll(client)


@pytest.mark.parametrize(
    ("contact", "read", "expected"),
    [
        (0.8, timedelta(seconds=10), CONNECTED),
        (0.8, timedelta(seconds=10.9), CONNECTED),
        (0.8, timedelta(seconds=11) - MICROSECOND, CONNECTED),
        (0.8, timedelta(seconds=11), LAPSED),
        (0, timedelta(seconds=10.5), CONNECTED),
        (0, timedelta(seconds=11) - MICROSECOND, CONNECTED),
        (0, timedelta(seconds=11), LAPSED),
    ],
    ids=[
        "0.8-read-10",
        "0.8-read-10.9",
        "0.8-read-11-less-1us",
        "0.8-read-11",
        "0-read-10.5",
        "0-read-11-less-1us",
        "0-read-11",
    ],
)
def test_the_replay_timeout_counts_whole_seconds(
    coordinator, event, clock, contact, read, expected
):
    """#341: PHP's `$last_contact + REPLAY_TIMEOUT_LIMIT < time()`
    (`replay.inc:35`) compares whole seconds, so a contact at SECOND or
    0.8 s past it is connected until 11 s past SECOND, and not 1 microsecond
    longer. Each read is the coordinator's replay state, then `replay.test`
    (`send_replay_message()`, `replay.inc:59-60`), then the client's next
    poll, which gets the TEST command while connected and `HELLO` alone once
    not (`action.replay.message.inc:12-24`), as #341's PHP probe showed."""
    found = read_at(
        coordinator, clock, SECOND + timedelta(seconds=contact), SECOND + read
    )

    assert found == expected


@pytest.mark.parametrize(
    ("early", "expected"),
    [(timedelta(0), LAPSED), (MICROSECOND, CONNECTED)],
    ids=["at-10.2-s", "1us-before"],
)
def test_a_contact_lapses_10_2_seconds_after_it(
    coordinator, event, clock, early, expected
):
    """#341: a contact 0.8 s past a whole second first reads as lapsed
    10.2 s after it, 11 s past its whole second (`replay.lapsed`), and not
    1 microsecond before."""
    contact = SECOND + timedelta(seconds=0.8)
    lapses = contact + timedelta(seconds=10.2)
    assert lapses == SECOND + timedelta(seconds=11)

    assert read_at(coordinator, clock, contact, lapses - early) == expected


def test_the_replay_age_counts_whole_seconds(coordinator, event, clock):
    """#341: the age is PHP's `time() - $replay['last_contact']`
    (`json-replay.inc:11`). A contact 0.8 s past SECOND, read 5.2 s past it,
    is 5 sec. old, though only 4.4 s have passed, as #341's PHP probe
    showed; 1 microsecond before 5 s past SECOND it is 4 sec. old."""
    clock[0] = SECOND + timedelta(seconds=0.8)
    poll(coordinator)
    clock[0] = SECOND + timedelta(seconds=5.2)
    assert (clock[0] - (SECOND + timedelta(seconds=0.8))).total_seconds() == (
        pytest.approx(4.4)
    )

    assert replay_state(coordinator)["message"] == "Ready (5 sec.)"
    clock[0] = SECOND + timedelta(seconds=5) - MICROSECOND
    assert replay_state(coordinator)["message"] == "Ready (4 sec.)"
