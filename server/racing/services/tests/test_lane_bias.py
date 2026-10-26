import pytest

from core.models import RaceSettings
from core.tests.factories import EventFactory
from racing.engine.standings.lane_bias import lane_bias_analysis
from racing.services.lane_bias import lane_bias
from racing.tests.factories import HeatLaneFactory, RoundFactory
from roster.tests.factories import GroupFactory

pytestmark = pytest.mark.django_db


def test_lane_bias_reads_each_lanes_timed_results_in_result_order():
    event = EventFactory()
    RaceSettings.objects.filter(event=event).update(lane_count=3)
    round = RoundFactory(group=GroupFactory(event=event))
    times = {
        (1, 1): 3.02,
        (1, 2): 3.11,
        (1, 3): 3.25,
        (2, 1): 3.07,
        (2, 2): 9.99,
        (2, 3): 3.31,
        (3, 1): 3.04,
        (3, 2): 3.18,
        (3, 3): 3.29,
    }
    for (heat, lane), time in reversed(times.items()):
        HeatLaneFactory(round=round, heat=heat, lane=lane, finish_time=time)
    HeatLaneFactory(round=round, heat=4, lane=1, finish_time=0.0, finish_place=1)
    HeatLaneFactory(round=round, heat=4, lane=2, finish_time=None, finish_place=2)
    HeatLaneFactory(round=round, heat=4, lane=3)
    HeatLaneFactory(round=round, heat=1, lane=4, finish_time=2.5)
    other = RoundFactory()
    RaceSettings.objects.filter(event=other.event).update(lane_count=3)
    HeatLaneFactory(round=other, heat=1, lane=1, finish_time=1.0)

    in_id_order = [
        [time for (_, lane), time in reversed(times.items()) if lane == n]
        for n in (1, 2, 3)
    ]
    expected = lane_bias_analysis(in_id_order)
    assert expected is not None
    assert lane_bias(event) == expected
