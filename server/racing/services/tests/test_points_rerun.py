"""Points racing in the middle of a round: `testing/test-points-racing.sh`
checked each racer's heats and points after every run of heat 3, with ties
and a rerun between them. No server writes a standings sheet for an unfinished
round, so `export-json.php` cannot show them, and this replays the same heats
through the services.

A heat's cars sit in lanes 1 to 4. With four lanes, first place scores 4
points, and cars that tie share the better place.
"""

import pytest

from core.models import RaceSettings
from racing.models import HeatLane, Round
from racing.services.heats import rerun, select_heat
from racing.services.results import record_timer_result
from racing.services.standings import standings_snapshot
from racing.services.tests.race import fields
from roster.models import Racer
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db

CHART = {
    1: (101, 121, 141, 111),
    2: (111, 131, 101, 121),
    3: (121, 141, 111, 131),
}

# The timer's FINISHED for heats 1 and 2 reports places, heat 3 reports times.
HEAT_1 = "place1=2&place2=3&place3=4&place4=1"
HEAT_2 = "place1=1&place2=4&place3=2&place4=3"

# Heat 3 run three ways, each followed by the (heats, points) of cars 121, 141,
# 111 and 131.
TIE_FOR_LAST = (
    "lane1=9.9999&lane2=3.2&lane3=3.1&lane4=9.9999",
    {121: (3, 6), 141: (2, 4), 111: (3, 12), 131: (2, 3)},
)
TIE_FOR_FIRST = (
    "lane1=3.3&lane2=3.1&lane3=3.1&lane4=3.4",
    {121: (3, 6), 141: (2, 5), 111: (3, 12), 131: (2, 2)},
)
FOUR_GOOD_TIMES = (
    "lane1=3.3&lane2=3.2&lane3=3.1&lane4=3.4",
    {121: (3, 6), 141: (2, 4), 111: (3, 12), 131: (2, 2)},
)


@pytest.fixture
def race():
    replay = Replay()
    for car in (101, 111, 121, 131, 141):
        replay.post(
            f"action=racer.import&firstname=First{car}&lastname=Last{car}"
            f"&carnumber={car}&partition=Lions"
        )
    replay.post("action=racer.bulk&who=all&what=checkin")
    replay.post("action=settings.write&n-lanes=4")
    event = replay.event
    RaceSettings.objects.filter(event=event).update(use_points=True)
    round = Round.objects.get(event=event, group__name="Lions", number=1)
    HeatLane.objects.filter(round=round).delete()
    for heat, cars in CHART.items():
        for lane, car in enumerate(cars, start=1):
            HeatLane.objects.create(
                event=event,
                round=round,
                heat=heat,
                lane=lane,
                racer=Racer.objects.get(event=event, car_number=car),
            )
    select_heat(event, round_id=round.id, now_racing=True)
    return event


def heats_and_points(event, expected):
    rows = {
        row.racer.car_number: row for row in standings_snapshot(event).standings.rows
    }
    return {car: (rows[car].summary.base, rows[car].summary.avg) for car in expected}


def test_points_after_each_run_of_heat_3(race):
    for body in (HEAT_1, HEAT_2):
        record_timer_result(race, fields(body))
    for body, expected in (TIE_FOR_LAST, TIE_FOR_FIRST, FOUR_GOOD_TIMES):
        record_timer_result(race, fields(body))
        assert heats_and_points(race, expected) == expected
        assert rerun(race, "last") is None
