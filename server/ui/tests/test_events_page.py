"""The events page (#212): list, create with Copy from, switch, archive,
Reopen for editing and the export download; and a past event's refusal as
a toast on any page (`ui.middleware.EventReadOnlyToast`)."""

import io
import zipfile

import pytest

from core import events
from core.models import Event, RaceState, SiteState
from roster.models import Group, Racer
from roster.tests.factories import RacerFactory
from transfer.export import DATABASE

pytestmark = pytest.mark.django_db


def html(response) -> str:
    return response.content.decode()


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


def test_the_page_lists_each_event_with_its_date_state_and_racers(coordinator, event):
    RacerFactory(event=event, group__event=event, subgroup__group__event=event)
    older = events.create_event("Last year")
    events.archive(older.pk)

    page = html(coordinator.get("/setup/events/"))

    assert f'data-event="{event.slug}" data-states="active"' in page
    assert 'data-event="last-year" data-states="archived"' in page
    assert "1 racer<" in page
    assert event.created_at.strftime("%Y") in page


def test_the_setup_page_links_the_events_page(coordinator):
    assert 'href="/setup/events/"' in html(coordinator.get("/setup/"))


def test_create_copies_the_parts_chosen(coordinator, event):
    Group.objects.create(event=event, name="Cubs", sort_order=1)
    RacerFactory(event=event, group__event=event, subgroup__group__event=event)

    response = coordinator.post(
        "/setup/events/new/",
        {"name": "Pack 42 2027", "copy_from": str(event.pk), "part": ["groups"]},
    )

    assert response.status_code == 200
    copy = Event.objects.get(name="Pack 42 2027")
    assert copy.copied_from == event
    assert Group.objects.filter(event=copy, name="Cubs").exists()
    assert not Racer.objects.filter(event=copy).exists()
    assert "copied from" in html(response)
    assert 'data-toast="ok"' in html(response)
    assert SiteState.get().active_event == event


@pytest.mark.parametrize(
    "fields",
    [
        {"name": ""},
        {"name": "a/b"},
        {"name": "X", "copy_from": "zzz"},
        {"name": "X", "copy_from": "999999"},
        {"name": "X", "copy_from": "1", "part": ["awards"]},
    ],
)
def test_create_refuses_with_a_toast(coordinator, event, fields):
    fields = {k: (str(event.pk) if v == "1" else v) for k, v in fields.items()}

    response = coordinator.post("/setup/events/new/", fields)

    assert response.status_code == 422
    assert 'data-toast="error"' in html(response)
    assert Event.objects.count() == 1


def test_switch_makes_the_old_event_read_only(coordinator, event):
    new = events.create_event("New")

    response = coordinator.post(f"/setup/events/{new.pk}/switch/")

    assert response.status_code == 200
    assert SiteState.get().active_event == new
    assert Event.objects.get(pk=event.pk).read_only
    page = html(response)
    assert f'data-event="{event.slug}" data-states="read-only"' in page
    assert f"{event.name} is now read-only" in page


def test_switch_is_refused_while_racing(coordinator, event):
    new = events.create_event("New")
    RaceState.objects.filter(event=event).update(racing=True)

    response = coordinator.post(f"/setup/events/{new.pk}/switch/")

    assert response.status_code == 422
    assert SiteState.get().active_event == event


def test_archive_and_reopen(coordinator, event):
    old = events.create_event("Old")

    archived = coordinator.post(f"/setup/events/{old.pk}/archive/")
    assert archived.status_code == 200
    assert Event.objects.get(pk=old.pk).archived_at is not None

    again = coordinator.post(f"/setup/events/{old.pk}/archive/")
    assert again.status_code == 200

    reopened = coordinator.post(f"/setup/events/{old.pk}/reopen/")
    assert reopened.status_code == 200
    assert not Event.objects.get(pk=old.pk).read_only
    assert Event.objects.get(pk=old.pk).archived_at is None


def test_the_active_event_cannot_be_archived(coordinator, event):
    response = coordinator.post(f"/setup/events/{event.pk}/archive/")

    assert response.status_code == 422
    assert Event.objects.get(pk=event.pk).archived_at is None


def test_an_unknown_event_is_not_found(coordinator, event):
    assert coordinator.post("/setup/events/999999/switch/").status_code == 422
    assert coordinator.get("/setup/events/999999/export.zip").status_code == 404


def test_the_export_downloads_a_zip(coordinator, event):
    response = coordinator.get(f"/setup/events/{event.pk}/export.zip")

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert f"{event.slug}.zip" in response["Content-Disposition"]
    body = b"".join(response.streaming_content)
    assert DATABASE in zipfile.ZipFile(io.BytesIO(body)).namelist()


@pytest.mark.parametrize("role", ["RaceCrew", "anonymous"])
def test_the_page_needs_set_up(login, event, role):
    client = login(role)
    new = events.create_event("New")

    assert client.get("/setup/events/").status_code in (302, 403)
    assert client.post(f"/setup/events/{new.pk}/switch/").status_code in (302, 403)
    assert client.get(f"/setup/events/{event.pk}/export.zip").status_code in (
        302,
        403,
    )
    assert SiteState.get().active_event == event


def test_a_page_write_to_a_read_only_event_is_an_error_toast(coordinator, event):
    racer = RacerFactory(event=event, group__event=event, subgroup__group__event=event)
    Event.objects.filter(pk=event.pk).update(read_only=True)

    response = coordinator.post(
        f"/checkin/racer/{racer.pk}/pass/", {"passed": "1"}, HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 422
    assert 'data-toast="error"' in html(response)
    assert "read-only" in html(response)
    assert not Racer.objects.get(pk=racer.pk).passed_inspection
