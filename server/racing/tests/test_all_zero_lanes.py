"""Q17 of #20, fixed by #65: the all-zero stop reads the lanes the heat
schedules.

PHP's `is_all_zeroes()` (`write-heat-results.inc:7-20`) wants a time made of
only `0` and `.` on every lane up to the lane count, bye lanes included, so a
timer that reports only the lanes in use never trips the stop that
`action.timer-message.inc:150` makes. Django tests the current heat's lanes,
or in a timer test the lanes its mask leaves in, and replays, stops racing and
writes nothing.
"""

import pytest

from core.models import RaceState
from racing.models import TimerStatus
from racing.services.heats import select_heat
from racing.services.results import Outcome, record_timer_result
from racing.services.tests.race import (
    connect_replay,
    current,
    drain,
    fields,
    lanes,
    setup_race,
)

pytestmark = pytest.mark.django_db


def test_zero_times_with_places_on_the_used_lanes_stop_racing():
    """Lanes 3 and 4 are masked. Master's port and PHP recorded this heat,
    the places standing; zero times on both used lanes are now the all-zero
    stop, places or not."""
    replay, round = setup_race(mask=12)
    event = replay.event
    connect_replay(event)
    select_heat(event, round_id=round.id, now_racing=True)
    drain(event)
    recorded = record_timer_result(event, fields("lane1=0&place1=2&lane2=0&place2=1"))
    assert recorded.outcome is Outcome.ALL_ZERO
    assert lanes(round, 1) == {1: (None, None), 2: (None, None)}
    assert current(event) == (round.id, 1, False)
    assert drain(event) == ["REPLAY 4000 2 50", "CANCEL"]


def test_places_alone_on_the_used_lanes_are_recorded():
    replay, round = setup_race(mask=12)
    select_heat(replay.event, round_id=round.id, now_racing=True)
    recorded = record_timer_result(replay.event, fields("place1=2&place2=1"))
    assert recorded.outcome is Outcome.RECORDED
    assert lanes(round, 1) == {1: (None, 2), 2: (None, 1)}
    assert current(replay.event) == (round.id, 2, True)


def timer_test(event, mask):
    RaceState.objects.filter(event=event).update(
        timer_test=True, current_round=None, current_heat=1, racing=True
    )
    TimerStatus.objects.filter(event=event).update(test_lane_mask=mask)


@pytest.mark.parametrize(
    ("mask", "body", "outcome"),
    [
        (0b1100, "lane1=0.000&lane2=0.000", Outcome.ALL_ZERO),
        (0b1100, "lane1=0.000&lane2=3.2", Outcome.RECORDED),
        (0, "lane1=0.000&lane2=0.000", Outcome.RECORDED),
        (0, "lane1=0&lane2=0&lane3=0&lane4=0", Outcome.ALL_ZERO),
    ],
)
def test_a_timer_test_reads_the_lanes_its_mask_leaves_in(mask, body, outcome):
    """With lanes 3 and 4 left out of the test, zero times on lanes 1 and 2
    stop it; with every lane in, lanes 3 and 4 need a zero time too."""
    replay, _ = setup_race()
    timer_test(replay.event, mask)
    assert record_timer_result(replay.event, fields(body)).outcome is outcome
    assert RaceState.objects.get(event=replay.event).racing is (
        outcome is Outcome.RECORDED
    )


def test_zero_times_with_no_current_heat_schedule_no_lane():
    """No lane is scheduled, so the result is not all zero, and the timer
    gets the no-current-round refusal with racing left on."""
    replay, _ = setup_race()
    RaceState.objects.filter(event=replay.event).update(
        current_round=None, current_heat=None, racing=True
    )
    recorded = record_timer_result(
        replay.event, fields("lane1=0&lane2=0&lane3=0&lane4=0")
    )
    assert recorded.outcome is Outcome.NO_CURRENT_ROUND
    assert RaceState.objects.get(event=replay.event).racing is True
