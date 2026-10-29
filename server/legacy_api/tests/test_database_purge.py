"""`database.purge` (#36)."""

import json

import pytest

from accounts.permissions import Perm
from awards.models import Award
from awards.tests.factories import AwardFactory
from core.models import RaceState
from legacy_api.tests.conftest import log_in, post
from racing.models import HeatLane
from racing.tests.factories import HeatLaneFactory, RoundFactory
from roster.models import Group, Racer
from roster.tests.factories import GroupFactory

pytestmark = pytest.mark.django_db


def purge(client, **fields):
    body = "&".join(["action=database.purge", *(f"{k}={v}" for k, v in fields.items())])
    return json.loads(post(client, body).content)


@pytest.fixture
def raced(event, client):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    group = GroupFactory(event=event)
    round = RoundFactory(event=event, group=group)
    lane = HeatLaneFactory(round=round, heat=1, lane=1, finish_time=3.1, finish_place=1)
    return round, lane


def test_it_needs_set_up(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert purge(client, purge="awards")["outcome"]["code"] == "notauthorized"


def test_an_unknown_mode_fails(client, raced):
    reply = purge(client, purge="everything")

    assert reply["outcome"]["code"] == "not-recognized"


def test_results_clear_times_and_keep_the_chart(client, raced):
    round, lane = raced

    reply = purge(client, purge="results")

    assert reply["outcome"]["summary"] == "success"
    lane.refresh_from_db()
    assert (lane.finish_time, lane.finish_place) == (None, None)


def test_results_of_one_round_leave_the_others(client, raced, event):
    round, lane = raced
    other = HeatLaneFactory(
        round=RoundFactory(event=event, group=round.group, number=2),
        heat=1,
        lane=1,
        finish_time=2.0,
        finish_place=1,
    )

    purge(client, purge="results", roundid=round.pk)

    lane.refresh_from_db()
    other.refresh_from_db()
    assert lane.finish_time is None and other.finish_time == 2.0


def test_results_cancel_the_running_round(client, raced, event):
    round, _ = raced
    RaceState.objects.filter(event=event).update(
        current_round=round, current_heat=1, racing=True
    )

    purge(client, purge="results")

    state = RaceState.objects.get(event=event)
    assert state.current_round_id is None


def test_schedules_delete_the_chart(client, raced):
    purge(client, purge="schedules")

    assert not HeatLane.objects.exists()


def test_racers_delete_racers_and_their_chart_and_keep_groups(client, raced, event):
    round, lane = raced
    award = AwardFactory(event=event, racer=lane.racer)

    reply = purge(client, purge="racers")

    assert reply["outcome"]["summary"] == "success"
    assert not Racer.objects.filter(event=event).exists()
    assert not HeatLane.objects.exists()
    assert Group.objects.filter(pk=round.group_id).exists()
    award.refresh_from_db()
    assert award.racer_id is None


def test_fake_racers_take_their_groups_with_them(client, raced, event):
    round, _ = raced
    RaceState.objects.filter(event=event).update(fake_racers=True)

    purge(client, purge="racers")

    assert not Group.objects.filter(pk=round.group_id).exists()
    assert not RaceState.objects.get(event=event).fake_racers


def test_awards_are_deleted(client, raced, event):
    AwardFactory(event=event)

    reply = purge(client, purge="awards")

    assert reply["outcome"]["summary"] == "success"
    assert not Award.objects.filter(event=event).exists()
    assert "details" in reply
