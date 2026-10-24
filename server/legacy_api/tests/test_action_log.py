import pytest

from accounts.permissions import Perm
from core.models import ActionLog
from legacy_api.tests.conftest import post

pytestmark = pytest.mark.django_db


def logged():
    return [row.request for row in ActionLog.objects.order_by("id")]


def test_an_unknown_action_is_logged_with_the_password_masked(client, event):
    post(client, "action=no.such&password=secret&message[recipient]=AA")

    assert logged() == [
        {"action": "no.such", "password": "...", "message": {"recipient": "AA"}}
    ]
    assert ActionLog.objects.get().event == event


def test_the_log_keeps_the_strings_the_client_sent(client, event):
    post(client, "action=no.such&n=007")

    assert logged() == [{"action": "no.such", "n": "007"}]


def test_a_refused_action_is_logged_as_php_logs_it(client, event, endpoint):
    endpoint("action", "probe.race", permission=Perm.CONTROL_RACE)

    post(client, "action=probe.race")

    assert logged() == [{"action": "probe.race"}]


def test_a_timer_heartbeat_is_not_logged_but_other_timer_messages_are(client, event):
    post(client, "action=timer-message&message=HEARTBEAT")
    post(client, "action=timer-message&message=HELLO")

    assert logged() == [{"action": "timer-message", "message": "HELLO"}]


@pytest.mark.parametrize(
    "name",
    ["replay.message", "message.send", "message.retrieve", "role.login", "vote.cast"],
)
def test_polling_and_secret_actions_are_not_logged(client, event, name):
    post(client, f"action={name}&password=x")

    assert logged() == []


def test_the_misspelled_php_name_is_logged_like_any_unknown_action(client, event):
    # C22: PHP excluded `replay-message` and so logged every replay.message.
    post(client, "action=replay-message")

    assert logged() == [{"action": "replay-message"}]


def test_a_nodata_action_is_not_logged(client, event):
    post(client, "action=no.such.nodata")

    assert logged() == []


def test_a_query_is_not_logged(client, event):
    client.get("/action.php?query=no.such")

    assert logged() == []
