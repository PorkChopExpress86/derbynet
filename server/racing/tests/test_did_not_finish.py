"""D42: a car the timer reports no time for did not finish. It gets
`DID_NOT_FINISH` seconds, the heat counts as run, and racing moves on, so a
car that does not finish in a round's last heat never stops the playlist."""

import pytest

from core.models import RaceState
from racing.engine.results import DID_NOT_FINISH
from racing.models import HeatLane, TimerStatus
from racing.services.heats import select_heat
from racing.services.playlist import start_playlist
from racing.services.results import Outcome, record_timer_result
from racing.services.tests.race import current, fields, lanes, setup_race
from racing.tests.test_race_day import state, two_step_playlist

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("last_lane", ["unposted", "3.2", "---"])
def test_a_car_that_does_not_finish_the_last_heat_moves_the_playlist_on(last_lane):
    replay, finalists_for = two_step_playlist(waits=False, continue_racing=True)
    round1 = start_playlist(replay.event, finalists_for)
    last = HeatLane.objects.filter(round=round1).order_by("-heat").first().heat
    while state(replay).current_round_id == round1.id and state(replay).racing:
        heat = state(replay).current_heat
        lanes = list(
            HeatLane.objects.filter(round=round1, heat=heat)
            .order_by("lane")
            .values_list("lane", flat=True)
        )
        posted = [f"lane{lane}=3.{lane}{heat % 10}" for lane in lanes]
        if heat == last and last_lane != "3.2":
            posted[-1] = f"lane{lanes[-1]}=---" if last_lane == "---" else ""
        recorded = record_timer_result(replay.event, fields("&".join(posted)))
        assert recorded.outcome is Outcome.RECORDED
    now = state(replay)
    assert now.current_round.number == 2 and now.current_heat == 1 and now.racing
    times = dict(
        HeatLane.objects.filter(round=round1, heat=last).values_list(
            "lane", "finish_time"
        )
    )
    places = dict(
        HeatLane.objects.filter(round=round1, heat=last).values_list(
            "lane", "finish_place"
        )
    )
    if last_lane != "3.2":
        assert times[lanes[-1]] == DID_NOT_FINISH
        assert places[lanes[-1]] == len(lanes), "the car that did not finish is last"


@pytest.mark.parametrize("body", ["lane1=0&lane2=0", "lane1=0.000&lane2=---"])
def test_no_positive_time_on_the_used_lanes_is_a_false_start(body):
    """Lanes 3 and 4 are masked. With no real time, no car finished, so the
    heat is the all-zero stop and not two cars that did not finish."""
    replay, round = setup_race(mask=12)
    select_heat(replay.event, round_id=round.id, now_racing=True)
    assert record_timer_result(replay.event, fields(body)).outcome is (Outcome.ALL_ZERO)
    assert lanes(round, 1) == {1: (None, None), 2: (None, None)}
    assert current(replay.event) == (round.id, 1, False)


def test_a_zero_time_beside_real_times_did_not_finish():
    replay, round = setup_race()
    select_heat(replay.event, round_id=round.id, now_racing=True)
    record_timer_result(
        replay.event, fields("lane1=3.1&lane2=0.000&lane3=3.3&lane4=3.4")
    )
    assert lanes(round, 1)[2] == (DID_NOT_FINISH, 4)


def test_a_timer_test_records_its_times():
    """The false-start rule reads the current heat's lanes, and a timer test
    has none."""
    replay, round = setup_race()
    RaceState.objects.filter(event=replay.event).update(
        timer_test=True, current_round=None, current_heat=1, racing=True
    )
    recorded = record_timer_result(replay.event, fields("lane1=3.1&lane2=3.2"))
    assert recorded.outcome is Outcome.RECORDED
    status = TimerStatus.objects.get(event=replay.event)
    assert status.test_results == {"1": {"time": "3.1"}, "2": {"time": "3.2"}}


def test_places_with_zero_times_are_recorded_as_php_records_them():
    """A heat with places is no false start: the places stand, and with no
    time above zero no car gets the did-not-finish time."""
    replay, round = setup_race(mask=12)
    select_heat(replay.event, round_id=round.id, now_racing=True)
    recorded = record_timer_result(
        replay.event, fields("lane1=0&place1=2&lane2=0&place2=1")
    )
    assert recorded.outcome is Outcome.RECORDED
    assert lanes(round, 1) == {1: (None, 2), 2: (None, 1)}
    assert current(replay.event) == (round.id, 2, True)
