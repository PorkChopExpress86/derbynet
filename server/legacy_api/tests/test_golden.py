"""Django's `/action.php` against the PHP captures, byte for byte.

Each exchange is replayed and compared on status, `Content-Type`, `Location`,
`Cache-Control` and body. `Set-Cookie` is not compared: PHP starts a session
on every request, and Django sends a cookie only when the session changes.

The exchanges that reach a handler not ported yet register a stub under the
PHP name with the permission #17 gives it. A refused request never runs the
stub, so the response is the dispatcher's alone.
"""

import re

import pytest

from accounts.permissions import Perm
from core.tests.factories import EventFactory
from legacy_api.tests.golden import replay, transcript

pytestmark = pytest.mark.django_db


def _never_runs(ctx):
    raise AssertionError("an anonymous request reached a refusing stub")


STUBS = {
    "kiosk.reload": dict(kind="action", permission=Perm.CONTROL_RACE),
}

# Exchanges this ticket does not reproduce, and why.
NOT_COMPARED = {
    # PHP answers 500 with an empty body. Django answers `Unrecognized query: `.
    "query-with-array-parameter": "PHP defect, see test_dispatcher",
}


def _cases(name):
    return [
        pytest.param(name, ex, id=f"{ex.number}-{ex.name}")
        for ex in transcript(name)
        if ex.name not in NOT_COMPARED
    ]


def assert_same(response, ex):
    assert response.status_code == ex.status
    assert response.get("Content-Type") == ex.content_type
    assert response.get("Location") == ex.location
    assert response.get("Cache-Control") == ex.cache_control
    assert response.content == ex.body


@pytest.fixture
def stubs(endpoint):
    for name, fields in STUBS.items():
        fields = dict(fields)
        endpoint(fields.pop("kind"), name, _never_runs, **fields)


@pytest.mark.parametrize(("name", "ex"), _cases("envelope/before-setup"))
def test_before_any_event_exists(client, name, ex):
    assert_same(replay(client, ex), ex)


@pytest.mark.parametrize(("name", "ex"), _cases("envelope/dispatcher"))
def test_the_dispatcher_envelope(client, stubs, name, ex):
    EventFactory()

    assert_same(replay(client, ex), ex)


def test_the_dispatcher_transcript_holds_the_json_refusals():
    names = {ex.name for ex in transcript("envelope/dispatcher")}

    assert {"json-notauthorized", "json-permission-failure"} <= names


def test_a_query_that_writes_nothing_is_an_empty_object(client):
    [ex] = [
        ex
        for ex in transcript("poll/race-day-polls")
        if ex.name == "08-before-awards award.current"
    ]
    EventFactory()

    assert_same(replay(client, ex), ex)


TIMECHECK = re.compile(rb'"timecheck": "([^"]*)"')
PHP_ISO8601 = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}")


def test_logging_in_by_role(client):
    """One client replays the transcript in order, so each exchange runs with
    the session the ones before it left. Only the `timecheck` value differs."""
    EventFactory()

    for ex in transcript("login/role-login"):
        response = replay(client, ex)
        for stamp in TIMECHECK.findall(response.content):
            assert PHP_ISO8601.fullmatch(stamp), ex.name
        response.content = TIMECHECK.sub(
            b'"timecheck": "<timecheck>"', response.content
        )
        assert_same(response, ex)
