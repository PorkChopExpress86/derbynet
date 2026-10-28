"""`manage.py create_test_event` (#63): the one empty event the shell suites run in."""

from io import StringIO

import pytest
from django.core.management import call_command

from core.models import Event, SiteState
from core.tests.factories import EventFactory

pytestmark = pytest.mark.django_db


def run(*args):
    out = StringIO()
    call_command("create_test_event", *args, stdout=out)
    return out.getvalue()


def test_it_creates_one_empty_active_event():
    run()

    event = Event.objects.get()
    assert event.name == "Test Event"
    assert SiteState.get().active_event == event


def test_the_name_can_be_chosen():
    run("--name", "Pinewood Test")

    assert Event.objects.get().name == "Pinewood Test"


def test_a_second_run_changes_nothing():
    run()
    first = Event.objects.get()

    output = run()

    assert Event.objects.get() == first
    assert "already" in output


def test_it_leaves_an_existing_event_alone():
    existing = EventFactory()

    run()

    assert list(Event.objects.all()) == [existing]


def test_the_new_event_can_be_reset():
    from core.services import reset_event

    run()

    reset_event(Event.objects.get())
