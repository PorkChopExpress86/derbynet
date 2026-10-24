import pytest
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from accounts.models import Role
from accounts.permissions import (
    Access,
    Perm,
    add_bits,
    allows,
    granted,
    log_in,
    log_out,
    role,
)
from core.tests.factories import EventFactory


def test_the_bits_are_phps():
    assert {p.name: p.value for p in Perm} == {
        "VIEW_RACE_RESULTS": 1,
        "VIEW_AWARDS": 2,
        "CHECK_IN_RACERS": 4,
        "REVERT_CHECK_IN": 8,
        "REGISTER_NEW_RACER": 32,
        "EDIT_RACER": 64,
        "ASSIGN_RACER_IMAGE": 128,
        "JUDGING": 256,
        "CONTROL_RACE": 512,
        "PRESENT_AWARDS": 1024,
        "EDIT_AWARDS": 2048,
        "TIMER_MESSAGE": 4096,
        "PHOTO_UPLOAD": 8192,
        "SET_UP": 32768,
        "ADMINISTRATION": 65536,
    }


def request_with(session):
    request = RequestFactory().get("/")
    request.session = session
    return request


@pytest.fixture
def event(db):
    return EventFactory()


def test_an_anonymous_session_holds_the_anonymous_roles_bits(event):
    assert granted(request_with({})) == Perm.VIEW_RACE_RESULTS
    assert role(request_with({})) is None
    assert role(request_with({"role": ""})) is None


def test_the_anonymous_bits_are_read_live(event):
    Role.objects.filter(name="").update(permissions=Perm.VIEW_AWARDS)

    assert granted(request_with({})) == Perm.VIEW_AWARDS


def test_with_no_roles_an_anonymous_session_holds_nothing(event):
    """PHP grants every bit when config-roles.inc is missing (S32)."""
    Role.objects.all().delete()

    assert granted(request_with({})) == 0


def test_with_no_roles_and_no_event_only_set_up_is_held(db):
    Role.objects.all().delete()

    assert granted(request_with({})) == Perm.SET_UP


def test_before_the_first_event_every_session_holds_set_up(db):
    assert granted(request_with({})) == Perm.VIEW_RACE_RESULTS | Perm.SET_UP
    timer = request_with({"role": "Timer", "permissions": int(Perm.TIMER_MESSAGE)})
    assert granted(timer) == Perm.TIMER_MESSAGE | Perm.SET_UP


def test_set_up_ends_when_an_event_exists(db):
    EventFactory()

    assert not allows(request_with({}), Perm.SET_UP)


@pytest.mark.parametrize(
    ("bits", "requirement", "expected"),
    [
        (0, Perm.CONTROL_RACE, False),
        (int(Perm.CONTROL_RACE), Perm.CONTROL_RACE, True),
        (int(Perm.SET_UP), Perm.CONTROL_RACE, False),
        (-1, Perm.ADMINISTRATION, True),
        (int(Perm.PHOTO_UPLOAD), Perm.CHECK_IN_RACERS | Perm.PHOTO_UPLOAD, True),
        (0, Access.ANONYMOUS, True),
    ],
)
def test_a_requirement_is_met_by_any_of_its_bits(event, bits, requirement, expected):
    assert allows(request_with({"permissions": bits}), requirement) is expected


def test_logged_in_needs_a_role_name_not_bits(event):
    assert not allows(request_with({"permissions": -1}), Access.LOGGED_IN)
    timer = request_with({"role": "Timer", "permissions": 4096})
    assert allows(timer, Access.LOGGED_IN)


@pytest.fixture
def session(db):
    store = SessionStore()
    store.create()
    return store


def test_logging_in_stores_the_role_and_issues_a_new_session_id(event, session):
    before = session.session_key

    log_in(session, Role.objects.get(name="RaceCrew"))

    assert session.session_key != before
    assert granted(request_with(session)) == 8687
    assert role(request_with(session)) == "RaceCrew"


def test_logging_out_returns_to_the_anonymous_role_with_a_new_id(event, session):
    log_in(session, Role.objects.get(name="RaceCoordinator"))
    before = session.session_key

    log_out(session)

    assert session.session_key != before
    assert role(request_with(session)) is None
    assert granted(request_with(session)) == Perm.VIEW_RACE_RESULTS


def test_logging_in_as_the_anonymous_role_logs_out(event, session):
    log_in(session, Role.objects.get(name="RaceCoordinator"))

    log_in(session, Role.objects.get(name=""))

    assert role(request_with(session)) is None


def test_sessions_last_a_race_day_and_polls_never_save_them():
    assert settings.SESSION_COOKIE_AGE == 86400
    assert settings.SESSION_SAVE_EVERY_REQUEST is False


def test_added_bits_join_the_anonymous_roles_with_a_new_id(event, session):
    before = session.session_key

    add_bits(session, Perm.CHECK_IN_RACERS)

    assert session.session_key != before
    assert granted(request_with(session)) == Perm.VIEW_RACE_RESULTS | 4
