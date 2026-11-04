"""The timer test's lane toggle, `racing.services.timer.set_test_lane`
(#50), and the PHP defect it does not copy.

PHP's `timer.test` with `tt-mask` ends the test and starts it twice, so a
second mask change lands on the test heat already staged and the timer is
never told: a php-scratch.sh run (2026-11-04) answered tt-mask=2 with
`<abort/>` and `lane-mask="13" heat="2"`, then tt-mask=0 and tt-mask=8 with
no `<heat-ready>` at all, the stored mask 8 and the heat still 2.
`set_test_mask` keeps that for the legacy endpoint; the page's toggle
stages a new heat for every change."""

import pytest

from core.models import RaceSettings, RaceState
from core.tests.factories import EventFactory
from racing.models import TimerStatus
from racing.services import timer

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    found = EventFactory()
    RaceSettings.objects.filter(event=found).update(lane_count=4)
    return found


def mask(event) -> int:
    return TimerStatus.objects.get(event=event).test_lane_mask


def heat(event) -> int | None:
    return RaceState.objects.get(event=event).current_heat


def test_a_toggle_sets_the_lanes_end_state(event):
    assert timer.set_test_lane(event, 2, occupied=False)
    assert mask(event) == 0b0010
    assert timer.set_test_lane(event, 3, occupied=False)
    assert mask(event) == 0b0110
    assert timer.set_test_lane(event, 2, occupied=True)
    assert mask(event) == 0b0100


def test_the_same_toggle_twice_is_one_change(event):
    timer.set_test_lane(event, 2, occupied=False)
    staged = heat(event)
    assert timer.set_test_lane(event, 2, occupied=False)
    assert (mask(event), heat(event)) == (0b0010, staged)


def test_a_toggle_starts_the_test(event):
    timer.set_test_lane(event, 1, occupied=False)
    state = RaceState.objects.get(event=event)
    assert state.timer_test and state.racing


def test_every_change_stages_a_new_test_heat(event):
    timer.set_test_lane(event, 2, occupied=False)
    first = heat(event)
    timer.set_test_lane(event, 2, occupied=True)
    second = heat(event)
    timer.set_test_lane(event, 4, occupied=False)
    assert first < second < heat(event)


def test_set_test_mask_keeps_phps_stale_heat(event):
    """The legacy endpoint as PHP answers it: the second change keeps the
    heat the first one staged."""
    timer.set_test_mode(event, True)
    timer.set_test_mask(event, 2)
    staged = heat(event)
    timer.set_test_mask(event, 0)
    assert heat(event) == staged


@pytest.mark.parametrize("lane", [0, 5, -1])
def test_a_lane_the_track_lacks_writes_nothing(event, lane):
    assert not timer.set_test_lane(event, lane, occupied=False)
    assert mask(event) == 0
    assert not RaceState.objects.get(event=event).timer_test


def test_bits_past_the_lane_count_are_cleared(event):
    TimerStatus.objects.filter(event=event).update(test_lane_mask=0b110000)
    timer.set_test_lane(event, 1, occupied=False)
    assert mask(event) == 0b0001


def test_a_track_with_no_lane_count_has_lane_one(event):
    RaceSettings.objects.filter(event=event).update(lane_count=0)
    assert timer.set_test_lane(event, 1, occupied=False)
    assert not timer.set_test_lane(event, 2, occupied=False)
