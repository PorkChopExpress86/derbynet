import pytest
from django.test import Client

from accounts.permissions import Perm
from legacy_api.tests.conftest import post
from legacy_api.tests.golden import exchange

pytestmark = pytest.mark.django_db


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)


def test_an_action_posted_without_a_csrf_token_is_answered(csrf_client, event):
    response = post(csrf_client, "action=no.such")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json; charset=utf-8"


def test_a_timer_message_without_a_token_gets_the_php_response(
    csrf_client, event, endpoint
):
    endpoint("action", "timer-message", permission=Perm.TIMER_MESSAGE)
    php = exchange("envelope/dispatcher", "xml-action-timer-message-without-login")

    response = post(csrf_client, "action=timer-message&message=HELLO")

    assert response.status_code == php.status
    assert response["Content-Type"] == php.content_type
    assert response.content == php.body


def test_the_new_ui_still_checks_csrf(csrf_client, db):
    assert csrf_client.post("/health/").status_code == 403
