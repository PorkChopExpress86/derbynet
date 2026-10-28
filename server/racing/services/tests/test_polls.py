"""The racing poll payloads' timer and replay states (#31), which the PHP
captures show only as not connected or just connected."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.tests.factories import EventFactory
from kiosks.models import ReplayStatus
from racing.models import TimerStatus
from racing.services import polls

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    return EventFactory()


def timer(event, **fields):
    TimerStatus.objects.filter(event=event).update(**fields)
    return polls.timer_state(event, NOW)


NOW = timezone.now()


def test_a_timer_that_has_not_reported_for_a_minute_is_not_connected(event):
    report = timer(
        event,
        state=TimerStatus.State.CONNECTED,
        last_contact=NOW - timedelta(seconds=61),
    )

    assert (report["state"], report["message"]) == (1, "NOT CONNECTED")
    assert polls.timer_troubled(event, NOW)


def test_a_connected_timer_reports_its_age(event):
    report = timer(
        event,
        state=TimerStatus.State.CONNECTED,
        last_contact=NOW - timedelta(seconds=5),
    )

    assert report["message"] == "CONNECTED (5 sec.)"
    assert report["icon"] == "img/status/ok.png"
    assert not polls.timer_troubled(event, NOW)


def test_a_malfunction_in_force_is_trouble_and_an_expired_one_is_not(event):
    report = timer(
        event,
        state=TimerStatus.State.STAGING,
        last_contact=NOW,
        malfunction_expires_at=NOW + timedelta(seconds=10),
        malfunction_message="Lane 3 gate stuck",
    )
    assert (report["message"], report["icon"]) == (
        "Lane 3 gate stuck",
        "img/status/trouble.png",
    )
    assert polls.timer_troubled(event, NOW)

    report = timer(event, malfunction_expires_at=NOW - timedelta(seconds=1))
    assert report["message"] == "Staging"
    assert not polls.timer_troubled(event, NOW)


def test_a_test_timer_never_times_out(event):
    report = timer(
        event,
        state=TimerStatus.State.RUNNING,
        last_contact=None,
        assume_connected_for_test=True,
    )

    assert (report["last-contact"], report["message"]) == (-1, "Race running")


def test_a_recent_remote_start_offer(event):
    assert timer(event, remote_start_seen_at=NOW - timedelta(seconds=60))[
        "remote-start"
    ]
    assert not timer(event, remote_start_seen_at=NOW - timedelta(seconds=61))[
        "remote-start"
    ]


def test_the_replay_state_follows_its_last_poll(event):
    ReplayStatus.objects.filter(event=event).update(
        state=ReplayStatus.State.CONNECTED,
        reported="1",
        last_contact=NOW - timedelta(seconds=3),
    )
    report = polls.replay_state(event, NOW)
    assert (report["message"], report["connected"]) == ("Recording (3 sec.)", True)

    report = polls.replay_state(event, NOW + timedelta(seconds=8))
    assert (report["state"], report["message"]) == (1, "NOT CONNECTED")
