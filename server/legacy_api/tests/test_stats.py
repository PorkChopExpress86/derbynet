"""`query=stats` (#36, S3, D16). PHP answers anonymously and returns every
RaceInfo row, `ballot_password` and `token-salt` included."""

import json

import pytest

from accounts.permissions import Perm
from awards.tests.factories import AwardFactory, BallotAwardFactory, VoteFactory
from core.models import RaceSettings
from legacy_api.tests.conftest import log_in
from racing.tests.factories import HeatLaneFactory, RoundFactory
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db


def stats(client):
    return json.loads(client.get("/action.php?query=stats").content)


def test_an_anonymous_caller_is_refused(client, event):
    RaceSettings.objects.filter(event=event).update(ballot_password="secret")

    reply = stats(client)

    assert reply["outcome"]["code"] == "notauthorized"
    assert "secret" not in json.dumps(reply)
    assert "race_info" not in reply and "racers" not in reply


def test_a_login_without_set_up_gets_the_counts_and_no_settings(client, event):
    log_in(client, Perm.CHECK_IN_RACERS, role="RaceCrew")
    RaceSettings.objects.filter(event=event).update(ballot_password="secret")
    group = GroupFactory(event=event)
    round = RoundFactory(event=event, group=group)
    HeatLaneFactory(round=round, heat=1, lane=1, racer__passed_inspection=False)
    racer = RacerFactory(event=event, group=round.group, passed_inspection=True)
    RacerFactory(event=event, group=round.group, headshot="a.jpg", car_photo="b.jpg")

    reply = stats(client)

    assert reply["outcome"]["summary"] == "success"
    assert "race_info" not in reply
    assert "secret" not in json.dumps(reply)
    assert reply["racers"] == {"total": 3, "passed": 1, "racerphoto": 1, "carphoto": 1}
    assert reply["heats"] == {"scheduled": 1, "raced": 0}
    assert reply["latest_heat_run"] is None
    assert reply["voting"] == {
        "n_ballot_awards": 0,
        "n_ballot_unawarded": 0,
        "nvotes": 0,
    }
    assert set(reply["timestamp"]) == {"ymd", "time"}
    assert racer.pk


def test_set_up_gets_the_settings_without_the_secrets_it_should_not_see(client, event):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    RaceSettings.objects.filter(event=event).update(lane_count=4)

    reply = stats(client)

    assert reply["race_info"]["lane_count"] == 4
    assert "token-salt" not in reply["race_info"]


def test_ballot_counts(client, event):
    log_in(client, Perm.CHECK_IN_RACERS, role="RaceCrew")
    award = AwardFactory(event=event)
    BallotAwardFactory(event=event, award=award)
    vote = VoteFactory(event=event, award=award)

    reply = stats(client)

    assert reply["voting"] == {
        "n_ballot_awards": 1,
        "n_ballot_unawarded": 1,
        "nvotes": 1,
    }
    assert vote.pk
