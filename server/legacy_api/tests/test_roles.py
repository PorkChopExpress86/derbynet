"""`role.login`, `role.list`, and the session they leave (#16). The byte-for-byte
responses are in test_golden.py."""

import json

import pytest
from django.conf import settings

from accounts.models import Role
from accounts.permissions import Access, Perm
from legacy_api.tests.conftest import post

pytestmark = pytest.mark.django_db


def login(client, body):
    return json.loads(post(client, f"action=role.login&{body}").content)


def session_cookie(client):
    cookie = client.cookies.get(settings.SESSION_COOKIE_NAME)
    return cookie.value if cookie else None


def fixed_session(client):
    """A session id the client holds before it logs in, as an attacker who
    planted the cookie would know it."""
    session = client.session
    session["planted"] = 1
    session.save()
    return session.session_key


def test_a_login_issues_a_new_session_id(client, event):
    planted = fixed_session(client)

    body = login(client, "name=RaceCrew&password=murphy")

    assert body["outcome"]["code"] == "success"
    assert session_cookie(client) != planted
    assert client.session["role"] == "RaceCrew"
    assert client.session["permissions"] == 8687


def test_a_logout_issues_a_new_session_id(client, event):
    login(client, "name=RaceCrew&password=murphy")
    before = session_cookie(client)

    login(client, "")

    assert session_cookie(client) != before
    assert "role" not in client.session
    assert "permissions" not in client.session


def test_a_wrong_password_changes_nothing(client, event):
    planted = fixed_session(client)

    body = login(client, "name=RaceCoordinator&password=doyourbes")

    assert body["outcome"]["code"] == "login"
    assert session_cookie(client) == planted
    assert "role" not in client.session


def test_php_loose_equality_does_not_log_in(client, event):
    """PHP's `==` takes `1e1` for the password `10` (S31)."""
    crew = Role.objects.get(name="RaceCrew")
    crew.set_password("10")
    crew.save()

    assert login(client, "name=RaceCrew&password=1e1")["outcome"]["code"] == "login"
    assert login(client, "name=RaceCrew&password=10")["outcome"]["code"] == "success"


def test_an_array_password_is_incorrect(client, event):
    body = login(client, "name=RaceCrew&password[]=murphy")

    assert body["outcome"]["code"] == "login"


def test_an_array_name_is_no_such_role(client, event):
    body = login(client, "name[]=RaceCrew&password=murphy")

    assert body["outcome"] == {
        "summary": "failure",
        "code": "role",
        "description": "No such role: ",
    }


@pytest.mark.parametrize("name", ["RaceCrew%00", "Race%00Crew", "%00"])
def test_a_nul_in_the_name_is_no_such_role(client, event, name):
    """Postgres refuses a NUL in a query string, which would be a 500."""
    body = login(client, f"name={name}&password=murphy")

    assert body["outcome"]["code"] == "role"
    assert "role" not in client.session


def test_the_timer_logs_in_as_a_role_without_naming_it(client, event, endpoint):
    """The response leaves `role` out for an empty password, as PHP does. The
    session holds the role and Timer's bits, but a login anyone can make does
    not count as logged in."""
    endpoint("query", "probe.logged-in", permission=Access.LOGGED_IN)
    endpoint("query", "probe.timer", permission=Perm.TIMER_MESSAGE)
    endpoint("query", "probe.results", permission=Perm.VIEW_RACE_RESULTS)

    body = login(client, "name=Timer&password=")

    assert "role" not in body
    assert client.session["role"] == "Timer"
    assert client.session["permissions"] == Perm.TIMER_MESSAGE
    assert json.loads(client.get("/action.php?query=probe.timer").content) == {}
    for refused in ("probe.logged-in", "probe.results"):
        body = json.loads(client.get(f"/action.php?query={refused}").content)
        assert body["outcome"]["code"] == "notauthorized"


def test_a_role_with_a_password_counts_as_logged_in(client, event, endpoint):
    endpoint("query", "probe.logged-in", permission=Access.LOGGED_IN)

    login(client, "name=RaceCrew&password=murphy")

    assert json.loads(client.get("/action.php?query=probe.logged-in").content) == {}


def test_a_login_grants_the_roles_bits_to_handlers(client, event, endpoint):
    endpoint("query", "probe.photo", permission=Perm.PHOTO_UPLOAD)
    refused = json.loads(client.get("/action.php?query=probe.photo").content)

    login(client, "name=Photo&password=flashbulb")

    assert refused["outcome"]["code"] == "notauthorized"
    assert json.loads(client.get("/action.php?query=probe.photo").content) == {}


def test_the_role_list_follows_position(client, event):
    Role.objects.filter(name="Timer").update(position=99)

    body = json.loads(client.get("/action.php?query=role.list").content)

    assert [r["name"] for r in body["roles"]][-1] == "Timer"
