"""What the race crew sees of a timer test started during the playlist's
intermission (D145): the test leaves racing off, and the dashboard's
simulated-racing herald with its Stop, the timer test page's Simulate
racing switch and the timer status page's banner still show it running.
With no intermission open they show racing, as PHP's pages do
(`coordinator-poll.js:685`, `timer-test.js:249-255`)."""

import pytest
from django.test import Client

from core.tests.factories import EventFactory
from racing.services import heats, timer
from racing.tests.test_result_changes_rebuild_the_final import (
    state_of_event,
    timed,
)
from ui.tests.roles import PASSWORDS

pytestmark = pytest.mark.django_db


def coordinator() -> Client:
    client = Client()
    response = client.post(
        "/login/",
        {"name": "RaceCoordinator", "password": PASSWORDS["RaceCoordinator"]},
    )
    assert response.status_code == 302
    return client


def shown_running(client) -> dict:
    """Whether each page shows the test running."""
    dashboard = client.get("/dashboard/").content.decode()
    test_page = client.get("/timer/test/").content.decode()
    status = client.get("/timer/").content.decode()
    return {
        "herald": "Simulated racing in progress" in dashboard,
        "stop": 'data-action="stop-test"' in dashboard,
        "switch": 'data-test-running="true"' in test_page,
        "banner": "Simulated racing is on." in status,
    }


@pytest.fixture
def testing_in_the_intermission():
    event = timed(waits=True)[0]
    timer.set_test_mode(event, True)
    now = state_of_event(event)
    assert now.timer_test
    assert not now.racing
    assert now.waiting_entry_id is not None
    return event


def test_every_page_shows_the_test_running_in_the_intermission(
    testing_in_the_intermission,
):
    shown = shown_running(coordinator())
    assert shown["herald"]
    assert shown["stop"]
    assert shown["switch"]
    assert shown["banner"]


def test_racing_off_during_a_test_with_no_intermission_shows_it_stopped():
    event = EventFactory()
    timer.set_test_mode(event, True)
    client = coordinator()
    shown = shown_running(client)
    assert shown["herald"]
    assert shown["switch"]
    assert shown["banner"]
    heats.set_racing(event, False)
    assert state_of_event(event).timer_test
    shown = shown_running(client)
    assert not shown["herald"]
    assert not shown["stop"]
    assert not shown["switch"]
    assert not shown["banner"]
