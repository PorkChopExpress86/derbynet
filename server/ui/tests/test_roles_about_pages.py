"""The roles page (D79) and the about page (#48, S30, C35)."""

import pytest
from django.test import Client

from accounts.models import Role
from accounts.services import PasswordRefused, change_password
from core import race_settings
from core.models import Event
from racing.models import TimerStatus
from ui.tests.roles import PASSWORDS

pytestmark = pytest.mark.django_db


def html(response) -> str:
    return response.content.decode()


def logs_in(name: str, password: str) -> bool:
    response = Client().post("/login/", {"name": name, "password": password})
    return response.status_code == 302


# ----- roles -----------------------------------------------------------------------


def test_after_a_password_change_the_old_fails_and_the_new_works(login, event):
    coordinator = login("RaceCoordinator")
    response = coordinator.post(
        "/settings/roles/RaceCrew/password/", {"password": "new-crew-pw"}
    )
    assert "data-saved" in html(response)
    assert not logs_in("RaceCrew", PASSWORDS["RaceCrew"])
    assert logs_in("RaceCrew", "new-crew-pw")
    # The legacy login reads the same role.
    legacy = Client().post(
        "/action.php",
        {"action": "role.login", "name": "RaceCrew", "password": "new-crew-pw"},
    )
    assert "success" in html(legacy)


def test_the_page_lists_roles_permissions_and_defaults(login, event):
    body = html(login("RaceCoordinator").get("/settings/roles/"))
    for name in ("RaceCoordinator", "RaceCrew", "Photo", "Timer"):
        assert f'data-role-card="{name}"' in body
    crew = body.split('data-role-card="RaceCrew"')[1].split("</li>")[0]
    assert "Default password" in crew
    assert "Check in racers" in crew and "Control race" not in crew
    assert (
        "everything"
        in body.split('data-role-card="RaceCoordinator"')[1].split("</li>")[0]
    )
    assert "every event" in body
    anonymous = body.split('data-role-card=""')[1].split("</li>")[0]
    assert "<form" not in anonymous


def test_a_changed_password_is_no_longer_marked_default(login, event):
    coordinator = login("RaceCoordinator")
    coordinator.post("/settings/roles/Photo/password/", {"password": "zoom"})
    photo = html(coordinator.get("/settings/roles/")).split('data-role-card="Photo"')[1]
    assert "Default password" not in photo.split("</li>")[0]


def test_empty_password_is_refused_but_timer_may_keep_none(login, event):
    coordinator = login("RaceCoordinator")
    refused = coordinator.post("/settings/roles/RaceCrew/password/", {"password": ""})
    assert "data-error" in html(refused)
    assert logs_in("RaceCrew", PASSWORDS["RaceCrew"])

    coordinator.post("/settings/roles/Timer/password/", {"password": "tick"})
    assert logs_in("Timer", "tick") and not logs_in("Timer", "")
    coordinator.post("/settings/roles/Timer/password/", {"none": "1"})
    assert Role.objects.get(name="Timer").password_hash == ""


def test_the_anonymous_role_has_no_password_to_change(db):
    with pytest.raises(PasswordRefused) as refused:
        change_password("", "x")
    assert refused.value.code == "anonymous"
    with pytest.raises(PasswordRefused):
        change_password("Nobody", "x")


def test_only_set_up_changes_passwords(login, event):
    crew = login("RaceCrew")
    response = crew.post(
        "/settings/roles/RaceCoordinator/password/",
        {"password": "mine"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 403
    assert logs_in("RaceCoordinator", PASSWORDS["RaceCoordinator"])


# ----- about -----------------------------------------------------------------------


def test_about_shows_no_configuration_dump_to_a_visitor(login, event):
    body = html(Client().get("/about/"))
    assert (
        "data-about-version" in body
        and "data-time-zone" in body
        and "data-schema" in body
    )
    for leak in (
        "phpinfo",
        "SECRET_KEY",
        "DATABASES",
        "sqlite3",
        "data-database",
        "data-health",
    ):
        assert leak not in body, leak


def test_about_shows_the_database_and_checks_to_set_up(login, event):
    body = html(login("RaceCoordinator").get("/about/"))
    assert "data-database" in body
    assert 'data-check="database"' in body
    assert "No problems found." in body
    assert 'data-download="snapshot"' in body


def test_about_shows_the_timer_name_from_timer_human(event):
    """C35: about.php read `timer-humant`, so the name never showed."""
    TimerStatus.objects.filter(event=event).update(
        timer_type="SmartLineDevice", human="SmartLine Timer", ident="v1.2"
    )
    assert race_settings.read(event, "timer-human") == "SmartLine Timer"
    body = html(Client().get("/about/"))
    assert (
        "<span data-timer-type>SmartLineDevice</span>"
        " <span data-timer-human>SmartLine Timer</span> (v1.2)" in body
    )


def test_about_works_with_no_event(db):
    assert not Event.objects.exists()
    response = Client().get("/about/")
    assert response.status_code == 200
    assert "No timer has connected." in html(response)
