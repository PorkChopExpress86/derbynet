"""The events page in a browser (#212): the acceptance flow at 390x844, and
the screenshots of each state at 390x844 and 1280x800."""

import pytest
from playwright.sync_api import expect

from core import events
from core.models import Event, SiteState
from roster.models import Group
from ui.tests.browser.conftest import (
    assert_accessible,
    dismiss_toasts,
    htmx_post,
    shoot,
)
from ui.tests.race import add_racers


@pytest.fixture(autouse=True)
def _data_dir(settings, tmp_path):
    """Each test's event folders go under a data folder of its own."""
    settings.DATA_DIR = tmp_path


def card(page, event_name: str):
    return page.locator("[data-event]").filter(
        has=page.get_by_role("heading", name=event_name, exact=True)
    )


def test_copy_an_event_switch_to_it_and_see_the_old_one_read_only(login, event):
    page = login("RaceCoordinator")
    if page.size != "phone":
        pytest.skip("the acceptance flow is at 390x844")
    add_racers(event, "Lions", 3, 101)
    page.goto("/setup/")
    page.locator('[data-link="events"]').click()
    page.wait_for_url("**/setup/events/")
    expect(
        page.locator("#main").get_by_role("heading", name="Events", exact=True)
    ).to_be_visible()

    form = page.locator('[data-form="new-event"]')
    form.get_by_label("Event name").fill("Pack 42 2027")
    expect(form.locator("[data-parts]")).to_be_hidden()
    form.get_by_label("Copy set-up from").select_option(label=event.name)
    expect(form.locator("[data-parts]")).to_be_visible()
    shoot(page, "events-copy-flow")
    with htmx_post(page, "/setup/events/new/"):
        form.get_by_role("button", name="Create event").click()

    copy = card(page, "Pack 42 2027")
    expect(copy).to_have_attribute("data-states", "editable")
    expect(copy).to_contain_text(f"copied from {event.name}")
    expect(copy.locator("[data-racers]")).to_have_text("0 racers")
    created = Event.objects.get(name="Pack 42 2027")
    assert Group.objects.filter(event=created, name="Lions").exists()

    with htmx_post(page, f"/setup/events/{created.pk}/switch/"):
        copy.get_by_role("button", name="Make active").click()

    expect(card(page, "Pack 42 2027")).to_have_attribute("data-states", "active")
    old = card(page, event.name)
    expect(old).to_have_attribute("data-states", "read-only")
    expect(old.get_by_text("Read-only", exact=True)).to_be_visible()
    expect(old.get_by_role("button", name="Reopen for editing")).to_be_visible()
    expect(page.locator('[data-toast="ok"]').last).to_contain_text(
        f"{event.name} is now read-only"
    )
    assert SiteState.get().active_event == created
    assert Event.objects.get(pk=event.pk).read_only
    shoot(page, "events-after-switch")


def test_events_screenshots(login, event):
    """Each state the page shows: active, read-only, archived and open for
    editing, with the new event form copying from one of them."""
    page = login("RaceCoordinator")
    add_racers(event, "Lions", 2, 101)
    archived = events.create_event("Pack 42 2024")
    events.archive(archived.pk)
    events.create_event("Pack 42 2025")
    events.switch_to(events.create_event("Pack 42 2026", copy_from=event.pk).pk)

    page.goto("/setup/events/")
    expect(card(page, event.name)).to_have_attribute("data-states", "read-only")
    expect(card(page, "Pack 42 2024")).to_have_attribute("data-states", "archived")
    shoot(page, "events-list")
    assert_accessible(page)

    form = page.locator('[data-form="new-event"]')
    form.get_by_label("Copy set-up from").select_option(label="Pack 42 2026")
    expect(form.locator("[data-parts]")).to_be_visible()
    shoot(page, "events-new-copy")
    assert_accessible(page)


def test_reopen_and_archive_from_the_page(login, event):
    page = login("RaceCoordinator")
    new = events.create_event("Next")
    events.switch_to(new.pk)
    page.goto("/setup/events/")

    old = card(page, event.name)
    with htmx_post(page, f"/setup/events/{event.pk}/reopen/"):
        old.get_by_role("button", name="Reopen for editing").click()
    expect(card(page, event.name)).to_have_attribute("data-states", "editable")

    # At 1280x800 the "open for editing" toast covers the lower part of the
    # Archive button, and on CI's fonts the button's centre falls under it.
    dismiss_toasts(page)
    with htmx_post(page, f"/setup/events/{event.pk}/archive/"):
        card(page, event.name).get_by_role("button", name="Archive").click()
    expect(card(page, event.name)).to_have_attribute("data-states", "archived")
    assert Event.objects.get(pk=event.pk).archived_at is not None
