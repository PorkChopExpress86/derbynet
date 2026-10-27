"""D42: a car the timer reports no time for did not finish. It gets
`DID_NOT_FINISH` seconds, the heat counts as run, and racing moves on, so a
car that does not finish in a round's last heat never stops the playlist."""

import pytest

from racing.engine.results import DID_NOT_FINISH
from racing.models import HeatLane
from racing.services.playlist import start_playlist
from racing.services.results import Outcome, record_timer_result
from racing.services.tests.race import fields
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
