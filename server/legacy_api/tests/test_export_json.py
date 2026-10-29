"""`export-json.php` (#36). The Standings and speed-award sheets are checked
against PHP's captures in `racing/tests/test_workbook_parity.py`."""

import json

import pytest

from accounts.models import Role
from accounts.permissions import Perm
from awards.tests.factories import AwardFactory
from legacy_api.tests.conftest import log_in
from racing.tests.factories import HeatLaneFactory, RoundFactory
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db


def sheets(client, path="/export-json.php"):
    response = client.get(path)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    return response, dict(json.loads(response.content))


def test_an_anonymous_request_is_sent_to_the_login_page(client, event):
    # The seeded anonymous role may read results, as in PHP; a site that took
    # that bit away sends visitors to the login page.
    Role.objects.filter(name="").update(permissions=0)

    response = client.get("/export-json.php")

    assert (response.status_code, response["Location"]) == (302, "login.php")


def test_a_role_without_view_race_results_is_sent_to_the_login_page(client, event):
    log_in(client, Perm.CHECK_IN_RACERS)

    assert client.get("/export-json.php").status_code == 302


def test_the_sheets_come_in_php_order_and_the_prefixed_path_answers(client, event):
    log_in(client, Perm.VIEW_RACE_RESULTS)

    response, workbook = sheets(client, "/derbynet/export-json.php")

    names = list(workbook)
    assert (names[:2], names[-1]) == (["Roster", "Results"], "Awards")
    assert response.content.endswith(b"]\n")


def test_text_stays_text_and_numbers_stay_numbers(client, event):
    log_in(client, Perm.VIEW_RACE_RESULTS)
    group = GroupFactory(event=event)
    round = RoundFactory(event=event, group=group)
    lane = HeatLaneFactory(
        round=round,
        heat=1,
        lane=1,
        finish_time=3.0,
        finish_place=1,
        racer__car_number=101,
        racer__car_name="111",
        racer__passed_inspection=True,
        racer__note="1e3",
    )

    response, workbook = sheets(client)

    roster = workbook["Roster"]
    assert roster[0] == [
        "First Name",
        "Last Name",
        "From",
        "Car Number",
        "Car Name",
        "Excluded?",
    ]
    assert roster[1][2:] == ["1e3", 101, "111", 0]
    results = workbook["Results"]
    assert results[1][:4] == [group.name, round.number, 1, 1]
    assert results[1][8:11] == [3.0, 227.3, 1]
    assert b'"111"' in response.content
    assert lane.pk


def test_racerid_adds_a_column(client, event):
    log_in(client, Perm.VIEW_RACE_RESULTS)
    racer = RacerFactory(event=event, passed_inspection=True)

    _, workbook = sheets(client, "/export-json.php?racerid")

    assert workbook["Roster"][0][0] == "RacerID"
    assert workbook["Roster"][1][0] == racer.pk


def test_judged_awards_follow_the_speed_awards(client, event):
    log_in(client, Perm.VIEW_RACE_RESULTS)
    AwardFactory(event=event, name="Best Paint")

    _, workbook = sheets(client)

    assert [row[0] for row in workbook["Awards"][1:]] == ["Best Paint"]
    assert workbook["Awards"][1][2:] == [None, None, None, None]
