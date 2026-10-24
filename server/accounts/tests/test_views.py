import time
from unittest import mock

import pytest
from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.test import Client, RequestFactory

from accounts.middleware import RoleSessionMiddleware
from accounts.permissions import Perm
from accounts.views import make_mcheckin_token, page_requires
from core.tests.factories import EventFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    return EventFactory()


def session_cookie(client):
    return client.cookies[settings.SESSION_COOKIE_NAME].value


def log_in(client, name, password, **extra):
    return client.post("/login/", {"name": name, "password": password, **extra})


def test_the_login_page_lists_the_interactive_roles(client, event):
    page = client.get("/login/").content.decode()

    assert "<option>RaceCrew</option>" in page
    assert "<option>RaceCoordinator</option>" in page
    assert "<option>Timer</option>" not in page
    assert "<option></option>" not in page


def test_all_lists_the_roles_for_machines_too(client, event):
    page = client.get("/login/?all").content.decode()

    assert "<option>Timer</option>" in page
    assert "<option>Photo</option>" in page


def test_a_login_returns_to_the_page_that_asked(client, event):
    response = log_in(client, "RaceCrew", "murphy", next="/somewhere/?x=1")

    assert response.status_code == 302
    assert response["Location"] == "/somewhere/?x=1"
    assert client.session["role"] == "RaceCrew"


def test_a_login_never_returns_to_another_site(client, event):
    response = log_in(client, "RaceCrew", "murphy", next="https://evil.example/")

    assert response["Location"] == "/login/"


def test_a_wrong_password_shows_the_form_again(client, event):
    response = log_in(client, "RaceCrew", "wrong")

    assert response.status_code == 401
    assert "Incorrect password." in response.content.decode()
    assert "role" not in client.session


def test_the_anonymous_role_cannot_log_in_by_page(client, event):
    response = log_in(client, "", "")

    assert response.status_code == 401
    assert "role" not in client.session


def test_the_login_page_checks_csrf(event):
    client = Client(enforce_csrf_checks=True)

    assert log_in(client, "RaceCrew", "murphy").status_code == 403


def test_a_page_login_issues_a_new_session_id(client, event):
    client.get("/login/")
    session = client.session
    session.save()
    planted = session.session_key

    log_in(client, "RaceCrew", "murphy")

    assert session_cookie(client) != planted


def test_logging_out_returns_to_the_login_page(client, event):
    log_in(client, "RaceCrew", "murphy")

    response = client.post("/logout/")

    assert response["Location"] == "/login/"
    assert "role" not in client.session
    assert client.get("/logout/").status_code == 405


def test_logging_out_forgets_the_barcode_mode_and_the_old_session(client, event):
    log_in(client, "RaceCrew", "murphy")
    client.post(
        "/action.php",
        "action=session.write&session_barcode-action=checkin",
        content_type="application/x-www-form-urlencoded",
    )
    assert client.session["barcode-action"] == "checkin"
    old = Client()
    old.cookies[settings.SESSION_COOKIE_NAME] = session_cookie(client)

    client.post("/logout/")

    assert dict(client.session.items()) == {}
    assert dict(old.session.items()) == {}


def view(request):
    return HttpResponse("ok")


def get(path, session):
    request = RequestFactory().get(path)
    request.session = session
    return request


def test_page_requires_sends_a_visitor_to_the_login_page(event):
    response = page_requires(Perm.CONTROL_RACE)(view)(get("/dashboard/?a=1", {}))

    assert response.status_code == 302
    assert response["Location"] == "/login/?next=%2Fdashboard%2F%3Fa%3D1"


def test_page_requires_lets_the_role_through(event):
    session = {"role": "RaceCoordinator", "permissions": -1}

    response = page_requires(Perm.CONTROL_RACE)(view)(get("/dashboard/", session))

    assert response.content == b"ok"


def test_the_middleware_sets_the_role_and_bits(event):
    seen = {}

    def capture(request):
        seen["role"], seen["permissions"] = request.role, request.permissions
        return HttpResponse()

    middleware = RoleSessionMiddleware(capture)
    middleware(get("/", {}))
    assert seen == {"role": None, "permissions": Perm.VIEW_RACE_RESULTS}
    middleware(get("/", {"role": "Timer", "permissions": 4096}))
    assert seen == {"role": "Timer", "permissions": 4096}


MCHECKIN = Perm.CHECK_IN_RACERS | Perm.REVERT_CHECK_IN | Perm.PHOTO_UPLOAD


def test_a_check_in_link_grants_check_in_and_photos(client, event):
    response = client.get(f"/mcheckin.php?tok={make_mcheckin_token()}")

    assert response.status_code == 200
    assert client.session["permissions"] == Perm.VIEW_RACE_RESULTS | MCHECKIN
    assert "role" not in client.session


def test_a_check_in_link_keeps_the_logged_in_role(client, event):
    log_in(client, "Photo", "flashbulb")

    client.get(f"/mcheckin.php?tok={make_mcheckin_token()}")

    assert client.session["role"] == "Photo"
    assert client.session["permissions"] == Perm.PHOTO_UPLOAD | MCHECKIN


def test_a_check_in_link_issues_a_new_session_id(client, event):
    client.get("/login/")
    session = client.session
    session.save()
    planted = session.session_key

    client.get(f"/mcheckin.php?tok={make_mcheckin_token()}")

    assert session_cookie(client) != planted


def test_a_check_in_link_expires_after_an_hour(client, event):
    issued = time.time()
    token = make_mcheckin_token()

    with mock.patch("django.core.signing.time.time", return_value=issued + 3599):
        assert client.get(f"/mcheckin.php?tok={token}").status_code == 200
    client.cookies.clear()
    with mock.patch("django.core.signing.time.time", return_value=issued + 3602):
        response = client.get(f"/mcheckin.php?tok={token}")

    assert response.status_code == 302
    assert response["Location"].startswith("/login/?next=")
    assert "permissions" not in client.session


@pytest.mark.parametrize(
    "tamper",
    [
        lambda token: token[:-1] + ("A" if token[-1] != "A" else "B"),
        lambda token: "other" + token[len("mcheckin") :],
        lambda token: "",
        lambda token: "28713521.abc123",
    ],
    ids=["signature", "value", "empty", "php-token"],
)
def test_a_tampered_check_in_link_grants_nothing(client, event, tamper):
    response = client.get(f"/mcheckin.php?tok={tamper(make_mcheckin_token())}")

    assert response.status_code == 302
    assert "permissions" not in client.session


@pytest.mark.parametrize(
    "signer",
    [signing.TimestampSigner(), signing.TimestampSigner(salt="derbynet.other")],
    ids=["default-salt", "other-salt"],
)
def test_another_salt_does_not_make_a_check_in_link(client, event, signer):
    """Django signs other values with the default salt, so a signature made
    for another purpose must not open check-in."""
    token = signer.sign("mcheckin")

    assert client.get(f"/mcheckin.php?tok={token}").status_code == 302
    assert "permissions" not in client.session
