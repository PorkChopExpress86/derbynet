"""`session.write` takes only the check-in page's barcode mode (S1)."""

import json

import pytest

from accounts.permissions import Perm
from legacy_api.tests.conftest import log_in, post

pytestmark = pytest.mark.django_db


def write(client, body):
    return json.loads(post(client, f"action=session.write&{body}").content)


@pytest.fixture
def crew(client, event):
    log_in(client, Perm.CHECK_IN_RACERS, role="RaceCrew")
    return client


def test_the_barcode_mode_is_stored(crew):
    body = write(crew, "session_barcode-action=checkin")

    assert body["outcome"]["code"] == "success"
    assert body["session"] == {"barcode-action": "checkin"}
    assert crew.session["barcode-action"] == "checkin"


def test_writing_the_permissions_changes_nothing(crew):
    body = write(crew, "session_permissions=-1&session_role=RaceCoordinator")

    assert body["session"] == {}
    assert crew.session["permissions"] == Perm.CHECK_IN_RACERS
    assert crew.session["role"] == "RaceCrew"


def test_an_unknown_barcode_mode_is_ignored(crew):
    write(crew, "session_barcode-action=checkin")

    body = write(crew, "session_barcode-action=delete-everything")

    assert body["session"] == {"barcode-action": "checkin"}


def test_a_session_without_check_in_is_refused(client, event):
    body = write(client, "session_barcode-action=checkin")

    assert body["outcome"]["code"] == "notauthorized"
    assert "barcode-action" not in client.session
