"""A browser on another site cannot post to `/action.php` (S34)."""

import json

import pytest
from django.test import override_settings

from accounts.permissions import Perm
from core.models import ActionLog
from legacy_api.tests.conftest import log_in
from legacy_api.tests.golden import exchange

pytestmark = pytest.mark.django_db


def post(client, body, **headers):
    return client.post(
        "/action.php",
        body,
        content_type="application/x-www-form-urlencoded",
        headers=headers,
    )


@pytest.fixture
def coordinator(client, event, endpoint):
    endpoint("action", "probe.write", permission=Perm.CONTROL_RACE)
    log_in(client, -1, role="RaceCoordinator")
    return client


def outcome(response):
    return json.loads(response.content)["outcome"]["code"]


def test_a_foreign_origin_is_refused_and_not_logged(coordinator):
    response = post(coordinator, "action=probe.write", Origin="http://evil.example")

    assert response.status_code == 200
    assert json.loads(response.content) == {
        "action": {"action": "probe.write"},
        "outcome": {
            "summary": "failure",
            "code": "notauthorized",
            "description": "Not authorized -- please see race coordinator.",
        },
    }
    assert not ActionLog.objects.exists()


def test_the_same_post_with_no_origin_succeeds(coordinator):
    response = post(coordinator, "action=probe.write")

    assert outcome(response) == "in-progress"
    assert ActionLog.objects.count() == 1


def test_the_servers_own_origin_succeeds(coordinator):
    response = post(coordinator, "action=probe.write", Origin="http://testserver")

    assert outcome(response) == "in-progress"


@override_settings(CSRF_TRUSTED_ORIGINS=["https://derby.example"])
def test_a_trusted_origin_succeeds(coordinator):
    response = post(coordinator, "action=probe.write", Origin="https://derby.example")

    assert outcome(response) == "in-progress"


def test_a_null_origin_is_refused(coordinator):
    response = post(coordinator, "action=probe.write", Origin="null")

    assert outcome(response) == "notauthorized"


def test_another_scheme_is_another_origin(coordinator):
    response = post(coordinator, "action=probe.write", Origin="https://testserver")

    assert outcome(response) == "notauthorized"


def test_another_port_is_another_origin(coordinator):
    response = post(coordinator, "action=probe.write", Origin="http://testserver:81")

    assert outcome(response) == "notauthorized"


def test_a_foreign_login_is_refused(client, event):
    response = post(
        client,
        "action=role.login&name=RaceCoordinator&password=doyourbest",
        Origin="http://evil.example",
    )

    assert json.loads(response.content)["action"]["password"] == "..."
    assert outcome(response) == "notauthorized"
    assert "role" not in client.session


def test_a_foreign_xml_action_gets_the_xml_refusal(client, event, endpoint):
    endpoint("action", "timer-message", permission=Perm.TIMER_MESSAGE)
    log_in(client, Perm.TIMER_MESSAGE, role="Timer")
    php = exchange("envelope/dispatcher", "xml-action-timer-message-without-login")

    response = post(
        client, "action=timer-message&message=HELLO", Origin="http://evil.example"
    )

    assert response["Content-Type"] == php.content_type
    assert response.content == php.body


def test_the_refusal_comes_before_the_setup_redirect(client, db):
    response = post(client, "action=probe.write", Origin="http://evil.example")

    assert response.status_code == 200
    assert outcome(response) == "notauthorized"


def test_a_query_with_an_origin_is_answered(client, event):
    response = client.get(
        "/action.php?query=role.list", headers={"Origin": "http://evil.example"}
    )

    assert "roles" in json.loads(response.content)
