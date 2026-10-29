"""`result.import` (#36)."""

import json

import pytest

from accounts.permissions import Perm
from legacy_api.tests.conftest import log_in, post
from racing.models import HeatLane, RosterEntry, Round
from roster.models import Group, Partition, Racer, Subgroup

pytestmark = pytest.mark.django_db

LINE = (
    "action=result.import&class=Tiger&round=1&heat=1&lane=1&firstname=Matthew"
    "&lastname=Glennon&carnumber=105&finishtime=3.936&finishplace=3"
    "&completed=2015-03-08%2018:35:44"
)


@pytest.fixture
def coordinator(client, event):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    return event


def run(client, body=LINE):
    return json.loads(post(client, body).content)["outcome"]


def test_it_needs_set_up(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert run(client)["code"] == "notauthorized"


def test_a_line_creates_the_group_racer_round_and_result(client, coordinator):
    assert run(client)["summary"] == "success"

    group = Group.objects.get(event=coordinator, name="Tiger")
    racer = Racer.objects.get(event=coordinator, car_number=105)
    assert (racer.group, racer.passed_inspection, racer.exclude) == (group, True, False)
    assert Subgroup.objects.filter(group=group).count() == 1
    assert Partition.objects.filter(event=coordinator, name="Tiger").count() == 1
    round = Round.objects.get(event=coordinator, group=group, number=1)
    lane = HeatLane.objects.get(round=round)
    assert (lane.heat, lane.lane, lane.finish_time, lane.finish_place) == (
        1,
        1,
        3.936,
        3,
    )
    assert lane.completed.strftime("%Y-%m-%d %H:%M:%S") == "2015-03-08 18:35:44"
    assert RosterEntry.objects.filter(round=round, racer=racer).exists()


def test_importing_a_line_twice_changes_nothing(client, coordinator):
    run(client)
    run(client)

    assert HeatLane.objects.count() == 1
    assert Racer.objects.filter(event=coordinator).count() == 1
    assert RosterEntry.objects.count() == 1


def test_a_second_racer_and_heat_reuse_the_group(client, coordinator):
    run(client)
    other = (
        LINE.replace("Matthew", "Burt")
        .replace("105", "303")
        .replace("lane=1", "lane=3")
    )
    run(client, other)

    assert Group.objects.filter(event=coordinator, name="Tiger").count() == 1
    assert HeatLane.objects.count() == 2


def test_a_bad_number_fails_with_unable(client, coordinator):
    outcome = run(client, LINE.replace("heat=1", "heat=x"))

    assert outcome["code"] == "unable"
    assert not HeatLane.objects.exists()


def test_two_racers_in_one_lane_fail_and_leave_the_first(client, coordinator):
    run(client)
    clash = LINE.replace("Matthew", "Burt").replace("105", "303")

    assert run(client, clash)["code"] == "unable"
    assert HeatLane.objects.count() == 1
