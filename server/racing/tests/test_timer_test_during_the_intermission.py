"""A timer test started during the playlist's intermission (#515, D145).
The test leaves racing off and the intermission open, while it runs and
after it ends, and while the intermission is open the timer's messages
treat the test as racing: a `<heat-ready>` stages each test heat and
FINISHED records it. Outside an intermission the test still turns racing
on, as PHP's `timer_test_start` does (`timer-test.inc:28`;
`test_timer_test_lanes.py`), and the messages read racing alone, as PHP's
do (`action.timer-message.inc:142-146,234,287`)."""

import pytest
from django.utils import timezone

from core.models import RaceSettings, RaceState
from core.tests.factories import EventFactory
from racing.models import PlaylistEntry
from racing.services import heats, polls, replay, timer
from racing.services.playlist import skip_waiting_step
from racing.services.results import record_manual_result
from racing.services.tests.race import fields
from racing.tests.test_result_changes_rebuild_the_final import (
    state_of_event,
    timed,
)

pytestmark = pytest.mark.django_db

START = {
    "set_test_mode": lambda event: timer.set_test_mode(event, True),
    "set_test_mask": lambda event: timer.set_test_mask(event, 2),
    "set_test_lane": lambda event: timer.set_test_lane(event, 2, occupied=False),
}


@pytest.fixture
def intermission():
    """Scouts round 1 raced through the timer, whose last result opens the
    intermission with the final's heat 1 current and racing off."""
    event, round1, final, entry, finalists_for = timed(waits=True)
    RaceState.objects.filter(event=event).update(refused_results=2)
    return event, round1, final, entry


def send(event, message, **lanes):
    return timer.handle_message(event, {"message": message, **lanes})


def connect(event):
    send(event, "HELLO")
    send(event, "IDENTIFIED", lane_count="4", timer="TestTimer")


def staged(event):
    """The timer test heat is staged: the timer got its `<heat-ready>`."""
    heat = state_of_event(event).current_heat
    reply = send(event, "HEARTBEAT")
    assert 'roundid="-100"' in reply
    assert f'heat="{heat}"' in reply
    assert "abort" not in reply


def stopped(event):
    """The timer is told to stop and gets no new heat, and a result is
    refused, as with racing off outside a test."""
    reply = send(event, "HEARTBEAT")
    assert "<abort" in reply
    assert "heat-ready" not in reply
    finished = send(event, "FINISHED", lane1="2.5", lane2="2.4", lane3="2.6")
    assert "<failure code='notracing'>" in finished
    assert "heat-ready" not in finished
    assert polls.timer_test(event)["results"] == []


@pytest.mark.parametrize("start", START)
def test_a_timer_test_leaves_racing_off_and_the_intermission_open(intermission, start):
    event, round1, final, entry = intermission
    START[start](event)
    now = state_of_event(event)
    assert now.timer_test
    assert not now.racing
    assert now.refused_results == 0
    assert now.waiting_entry_id == entry.id
    assert now.ended_round_id == round1.id


def test_the_end_of_the_test_leaves_them_so(intermission):
    event, round1, final, entry = intermission
    timer.set_test_mode(event, True)
    timer.set_test_mode(event, False)
    now = state_of_event(event)
    assert not now.timer_test
    assert not now.racing
    assert now.current_round_id == final.id
    assert now.current_heat == 1
    assert now.waiting_entry_id == entry.id
    assert now.ended_round_id == round1.id


def test_the_timer_stages_and_records_the_test_with_racing_off(intermission):
    event, round1, final, entry = intermission
    connect(event)
    timer.set_test_mode(event, True)
    heat = state_of_event(event).current_heat
    staged(event)
    assert "abort" not in send(event, "STARTED")
    finished = send(event, "FINISHED", lane1="2.5", lane2="2.4", lane3="2.6")
    assert "notracing" not in finished
    assert f'heat="{heat + 1}"' in finished
    assert polls.timer_test(event)["heat"] == heat
    places = {r["lane"]: r["place"] for r in polls.timer_test(event)["results"]}
    assert places == {1: 2, 2: 1, 3: 3}
    now = state_of_event(event)
    assert now.timer_test
    assert not now.racing
    assert now.refused_results == 0
    assert now.waiting_entry_id == entry.id


def test_the_replay_gets_no_start_for_the_next_test_heat(intermission):
    """With racing off, the result's move to the next test heat is the
    racing-off `Advance` row, which sends CANCEL and no START (D145)."""
    event, round1, final, entry = intermission
    replay.poll(event.id, "", timezone.now())
    connect(event)
    timer.set_test_mode(event, True)
    staged(event)
    send(event, "STARTED")
    settings = RaceSettings.objects.get(event=event)
    assert replay.poll(event.id, "", timezone.now()) == [replay.race_starts(settings)]
    send(event, "FINISHED", lane1="2.5", lane2="2.4", lane3="2.6")
    first, *rest = replay.poll(event.id, "", timezone.now())
    assert first == replay.replay(settings)
    assert rest == [replay.CANCEL]


def test_skip_during_the_test_stops_the_timer(intermission):
    """Skip with no step after the final closes the intermission and leaves
    racing off and the test current, so the test no longer counts as
    racing. A step after it would be selected, which ends the test."""
    event, round1, final, entry = intermission
    PlaylistEntry.objects.filter(event=event, seq__gt=entry.seq).delete()
    connect(event)
    timer.set_test_mode(event, True)
    staged(event)
    assert skip_waiting_step(event, None) is None
    now = state_of_event(event)
    assert now.timer_test
    assert not now.racing
    assert now.waiting_entry_id is None
    stopped(event)


def test_racing_off_during_a_test_with_no_intermission_stops_the_timer():
    event = EventFactory()
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    connect(event)
    timer.set_test_mode(event, True)
    assert state_of_event(event).racing
    staged(event)
    heats.set_racing(event, False)
    now = state_of_event(event)
    assert now.timer_test
    assert not now.racing
    stopped(event)


def test_a_test_with_racing_on_sends_the_replay_start_for_its_next_heat():
    """Outside an intermission racing is on, so the next test heat gets
    START, as PHP's `advance_heat` sends it only while racing
    (`autoadvance.inc:143-155`)."""
    event = EventFactory()
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    replay.poll(event.id, "", timezone.now())
    connect(event)
    timer.set_test_mode(event, True)
    heat = state_of_event(event).current_heat
    staged(event)
    send(event, "STARTED")
    replay.poll(event.id, "", timezone.now())
    send(event, "FINISHED", lane1="2.5", lane2="2.4", lane3="2.6")
    settings = RaceSettings.objects.get(event=event)
    first, second, *rest = replay.poll(event.id, "", timezone.now())
    assert first == replay.replay(settings)
    assert second == replay.CANCEL
    assert rest == [replay.start("Timer Test", 1, heat + 1)]


def test_a_manual_result_in_the_test_moves_to_the_next_test_heat(intermission):
    """The test works during the intermission as outside it (D145): a result
    written by hand moves the test on, as the `manual result in a timer
    test, racing` row does with racing on."""
    event, round1, final, entry = intermission
    timer.set_test_mode(event, True)
    heat = state_of_event(event).current_heat
    record_manual_result(event, fields("lane1=2.5&lane2=2.4"))
    now = state_of_event(event)
    assert now.timer_test
    assert now.current_heat == heat + 1
    assert not now.racing
    assert polls.timer_test(event)["heat"] == heat
