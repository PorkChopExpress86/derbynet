"""`query=settings.list` (#63, S2). PHP answers it anonymously, with every
RaceInfo row including `ballot_password` and `token-salt`."""

import json

import pytest

from accounts.permissions import Perm
from core.models import RaceSettings, RaceState
from legacy_api.tests.conftest import log_in

pytestmark = pytest.mark.django_db


def settings_list(client, query=""):
    response = client.get(f"/action.php?query=settings.list{query}")
    return json.loads(response.content)["settings"]


def test_an_anonymous_list_has_an_empty_connection_and_no_keys(client, event):
    RaceSettings.objects.filter(event=event).update(ballot_password="secret")

    settings = settings_list(client)

    assert settings == {"db-connection": ""}


def test_an_anonymous_key_request_returns_nothing(client, event):
    RaceState.objects.filter(event=event).update(balloting_open=True)

    assert settings_list(client, "&key=balloting") == {"db-connection": ""}


def test_a_caller_without_set_up_gets_no_keys(client, event):
    log_in(client, Perm.CONTROL_RACE)

    assert settings_list(client, "&key=lane_count") == {"db-connection": ""}


def test_set_up_lists_only_the_requested_keys_in_legacy_text(client, event):
    log_in(client, Perm.SET_UP)
    RaceSettings.objects.filter(event=event).update(lane_count=4, use_points=True)

    settings = settings_list(client, "&key=lane_count,use-points,reverse-lanes")

    assert settings == {
        "db-connection": "",
        # The encoder turns numeric text into numbers, as PHP's does.
        "lane_count": 4,
        "use-points": 1,
        "reverse-lanes": 0,
    }


def test_an_unknown_or_dropped_key_reads_as_null(client, event):
    log_in(client, Perm.SET_UP)

    settings = settings_list(client, "&key=nonesuch,token-salt")

    assert settings == {"db-connection": "", "nonesuch": None, "token-salt": None}


def test_set_up_with_no_key_lists_every_setting(client, event):
    log_in(client, Perm.SET_UP)
    RaceSettings.objects.filter(event=event).update(ballot_password="secret")

    settings = settings_list(client)

    assert settings["db-connection"] == ""
    assert settings["ballot_password"] == "secret"
    assert settings["lane_count"] == 0
    assert settings["balloting"] == "closed"
    assert "token-salt" not in settings


@pytest.mark.parametrize("key", ["tt-lane1", "tt-place2", "replay_queue", "tt-"])
def test_keys_with_no_single_stored_value_read_as_null(client, event, key):
    log_in(client, Perm.SET_UP)

    response = client.get(f"/action.php?query=settings.list&key={key}")

    body = json.loads(response.content)
    assert body["settings"] == {"db-connection": "", key: None}
