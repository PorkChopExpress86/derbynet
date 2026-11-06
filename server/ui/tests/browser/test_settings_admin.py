"""The settings, set-up, roles and about pages in a browser (#48): the
acceptance flows at 390x844 and 1280x800, the settings.php and setup.php
parity boxes, and the screenshots."""

import re

import pytest
from playwright.sync_api import expect

from accounts.models import Role
from core import race_settings
from core.models import Event, RaceSettings
from roster.models import Partition, Racer
from transfer import backups
from ui.tests.browser.conftest import assert_accessible, htmx_post, shoot
from ui.tests.race import add_racers, race_setup, scheduled


@pytest.fixture(autouse=True)
def _data_dir(settings, tmp_path):
    """The automatic snapshots go under the event's folder in the data
    folder, so each test gets a data folder of its own."""
    settings.DATA_DIR = tmp_path


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check is at 390x844")


def open_section(page, key):
    section = page.locator(f'details[data-section="{key}"]')
    if section.get_attribute("open") is None:
        section.locator("summary").click()
    expect(section).to_have_attribute("open", "")
    return section


def saved(page):
    return htmx_post(page, "/settings/save/")


def coordinator(login):
    return login("RaceCoordinator")


# ----- settings --------------------------------------------------------------------


def test_one_field_per_group_saves_from_the_page(login, event):
    """settings.php parity: a field of each section, typed as the page
    draws it, reads back PHP's RaceInfo text."""
    add_racers(event, "Lions", 1, 101)
    page = coordinator(login)
    page.goto("/settings/")

    track = open_section(page, "track")
    with saved(page):
        track.get_by_label("Track length").fill("42")
        track.get_by_label("Track length").press("Enter")
    expect(page.locator("#field-track-length [data-saved]")).to_be_visible()

    racers = open_section(page, "racers")
    with saved(page):
        racers.get_by_text("First name, last initial").click()

    awards = open_section(page, "awards")
    with saved(page):
        awards.get_by_role("switch", name="At most one trophy per racer").click()

    photos = open_section(page, "photos")
    with saved(page):
        photos.get_by_text("Car", exact=True).click()

    rules = open_section(page, "rules")
    with saved(page):
        rules.get_by_text("Fastest heat").click()
    with htmx_post(page, "/playlist/scheduling/method/"):
        rules.get_by_text("Lane rotation").click()

    replay = open_section(page, "replay")
    with saved(page):
        replay.get_by_label("Recorded after the last car finishes").fill("1.5")
        replay.get_by_label("Recorded after the last car finishes").blur()

    stored = race_settings.read_many(
        event,
        [
            "track-length",
            "name-style",
            "one-trophy-per",
            "photos-on-now-racing",
            "scoring",
        ],
    )
    assert stored == {
        "track-length": "42",
        "name-style": "1",
        "one-trophy-per": "1",
        "photos-on-now-racing": "car",
        "scoring": "2",
    }
    row = RaceSettings.for_event(event)
    assert row.rotation_schedule and row.replay_post_roll_ms == 1500


def test_lane_colors_and_unused_lanes(login, event):
    RaceSettings.objects.filter(event=event).update(lane_count=3)
    page = coordinator(login)
    page.goto("/settings/")
    track = open_section(page, "track")
    with htmx_post(page, "/settings/lane-colors/"):
        track.locator('select[data-lane="1"]').select_option("red")
    with htmx_post(page, "/playlist/scheduling/lanes/"):
        track.get_by_role("switch", name=re.compile("Lane 2")).click()
    assert race_settings.read(event, "lane-colors") == "red,none,none"
    assert race_settings.read(event, "unused-lane-mask") == "2"

    with saved(page):
        track.get_by_label("Lanes on the track").fill("4")
        track.get_by_label("Lanes on the track").press("Enter")
    expect(track.locator('select[name="color"]')).to_have_count(4)
    expect(
        track.locator("[data-lane]").filter(has=page.get_by_role("switch"))
    ).to_have_count(4)


def test_the_lane_lock(login, event):
    """settings.php parity: while unraced heats exist the track is marked,
    the lane fields are disabled, and the dashboard is a tap away."""
    scheduled(race_setup(event)["Wolves"], race=False)
    page = coordinator(login)
    page.goto("/settings/")
    track = open_section(page, "track")
    expect(track.locator("[data-lanes-locked]")).to_be_visible()
    expect(track.get_by_label("Lanes on the track")).to_be_disabled()
    expect(track.get_by_role("switch", name=re.compile("Lane 1"))).to_be_disabled()
    expect(track.get_by_role("link", name="race dashboard")).to_have_attribute(
        "href", "/dashboard/"
    )
    shoot(page, "settings-lane-lock")


def test_folder_status_and_check(login, event):
    page = coordinator(login)
    page.goto("/settings/")
    photos = open_section(page, "photos")
    folder = photos.locator("#field-photo-dir")
    folder.get_by_label("Racer photos folder").fill("/etc")
    with saved(page):
        folder.get_by_role("button", name="Check").click()
    folder = photos.locator("#field-photo-dir")
    expect(folder.locator('[data-folder-status="outside"]')).to_be_visible()
    expect(folder.get_by_role("alert")).to_contain_text("pick a folder under")
    expect(folder.locator('[data-folder-status="outside"]')).to_be_visible()
    stored = RaceSettings.for_event(event).photo_directory
    folder.get_by_label("Racer photos folder").fill(stored)
    with saved(page):
        folder.get_by_role("button", name="Check").click()
    expect(photos.locator("#field-photo-dir [data-saved]")).to_be_visible()


def test_weigh_in_in_ounces_shows_at_check_in(login, event):
    """Acceptance: recorded weight in ounces with a 5 oz maximum makes
    check-in ask for a weight in ounces and warn above 5 oz."""
    add_racers(event, "Outlaws", 1, 201, passed=False)
    outlaw = Racer.objects.get(event=event)
    page = coordinator(login)
    page.goto("/settings/")
    section = open_section(page, "weigh-in")
    row = section.locator('[data-weigh-in="Outlaws"]')
    row.get_by_text("Recorded weight").click()
    row.locator('select[name="unit"]').select_option("oz")
    row.get_by_label(re.compile("Maximum weight")).fill("5")
    with htmx_post(page, "/settings/weigh-in/"):
        row.get_by_role("button", name="Save").click()
    expect(section.locator('[data-weigh-in="Outlaws"] [data-saved]')).to_be_visible()
    shoot(page, "settings-weigh-in")

    page.goto("/checkin/")
    racer = page.locator(f'[data-racer="{outlaw.pk}"]')
    racer.get_by_label("Weight, Outlaws1 Racer").fill("5.4")
    racer.get_by_role("button", name="Save weight").click()
    expect(racer.locator("[data-over]")).to_have_text(
        re.compile(r"Over the 5\.0 oz max")
    )
    expect(racer.locator("[data-weight]")).to_have_text("5.4 oz")


def test_a_weigh_in_mode_change_says_it_clears(login, event):
    from roster.services.weigh_in import record_weigh_in, set_partition_weigh_in

    add_racers(event, "Lions", 2, 101, passed=False)
    lions = Partition.objects.get(event=event)
    set_partition_weigh_in(lions, "pass_fail")
    record_weigh_in(Racer.objects.filter(event=event).first(), passed=True)
    page = coordinator(login)
    page.goto("/settings/")
    row = open_section(page, "weigh-in").locator('[data-weigh-in="Lions"]')
    expect(row.locator("[data-clears]")).to_be_hidden()
    row.get_by_text("Recorded weight").click()
    expect(row.locator("[data-clears]")).to_be_visible()
    expect(row.get_by_role("button", name="Save and clear 1 weigh-in")).to_be_visible()
    row.get_by_text("Pass or fail").click()
    expect(row.locator("[data-clears]")).to_be_hidden()


def test_settings_screenshots(login, event):
    add_racers(event, "Lions", 2, 101)
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    page = coordinator(login)
    page.goto("/settings/")
    for key in ("track", "racers", "awards", "photos", "rules", "weigh-in", "replay"):
        section = open_section(page, key)
        section.scroll_into_view_if_needed()
        path = shoot(page, f"settings-{key}", full_page=False)
        assert path.exists()
    assert_accessible(page)


# ----- the set-up wizard -----------------------------------------------------------


@pytest.fixture
def no_event(_database):
    """A fresh install: no event, so the first visitor may set up."""
    assert not Event.objects.exists()


def test_a_fresh_install_reaches_the_dashboard_through_the_wizard(page, no_event):
    page.goto("/setup/")
    expect(page.locator('[data-step="event"]')).to_have_attribute(
        "data-step-status", "unknown"
    )
    shoot(page, "setup-step-1-event")
    page.get_by_label("New event's name").fill("Pack 9 Derby")
    page.get_by_role("button", name="Start a new event").click()
    page.wait_for_url(re.compile(r"/setup/$"))
    expect(page.locator('[data-step="event"]')).to_have_attribute(
        "data-step-status", "ok"
    )
    expect(page.locator('[data-step="login"]')).to_have_attribute(
        "data-step-status", "ok"
    )
    for step in ("schema", "time-zone", "lanes", "photos", "roster", "login"):
        page.locator(f'[data-step="{step}"]').scroll_into_view_if_needed()
        shoot(page, f"setup-step-{step}", full_page=False)

    lanes = page.locator('[data-step="lanes"]')
    with saved(page):
        lanes.get_by_label("Lanes on the track").fill("4")
        lanes.get_by_label("Lanes on the track").press("Enter")
    page.locator('[data-step="roster"]').get_by_role(
        "button", name="Make a fake roster"
    ).click()
    form = page.locator('[data-sheet="setup"] [data-fake-roster]')
    expect(form).to_be_visible()
    form.get_by_role("button", name="Make fake racers").click()
    page.wait_for_url(re.compile(r"/checkin/$"))
    page.goto("/setup/")
    page.locator('[data-step="login"]').get_by_role(
        "link", name="Go to the race dashboard"
    ).click()
    page.wait_for_url(re.compile(r"/dashboard/$"))
    expect(page.locator("main")).to_be_visible()
    event = Event.objects.get()
    assert RaceSettings.for_event(event).lane_count == 4
    assert Racer.objects.filter(event=event).count() > 0


def test_make_fake_roster_on_a_phone(login, event):
    """Acceptance: 6 groups of about 5, every racer checked in. racer.fake
    gives each group 2 more or fewer at random, as PHP does
    (`action.racer.fake.inc:36`), so the count is checked against the rows."""
    phone_only(login("anonymous"))
    page = coordinator(login)
    page.goto("/setup/")
    page.locator('[data-step="roster"]').get_by_role(
        "button", name="Make a fake roster"
    ).click()
    form = page.locator('[data-sheet="setup"] [data-fake-roster]')
    expect(form.get_by_label("Groups")).to_have_value("6")
    expect(form.get_by_label("Racers per group, about")).to_have_value("5")
    expect(form.get_by_role("switch")).to_be_checked()
    shoot(page, "setup-fake-roster", full_page=False)
    form.get_by_role("button", name="Make fake racers").click()
    page.wait_for_url(re.compile(r"/checkin/$"))
    made = Racer.objects.filter(event=event)
    assert 18 <= made.count() <= 42
    assert made.filter(passed_inspection=True).count() == made.count()
    expect(page.locator("[data-racer]")).to_have_count(made.count())


def test_purge_racers_on_a_phone(login, event):
    """Acceptance: the count, a snapshot, then the delete."""
    phone_only(login("anonymous"))
    add_racers(event, "Lions", 42, 101)
    page = coordinator(login)
    page.goto("/setup/")
    data = page.locator('[data-section="data"]')
    data.scroll_into_view_if_needed()
    shoot(page, "setup-data", full_page=False)
    data.get_by_role("button", name="Delete racers…").click()
    sheet = page.locator('[data-sheet="setup"]')
    confirm = sheet.get_by_role("button", name="Delete 42 racers")
    expect(confirm).to_be_visible()
    shoot(page, "setup-purge-confirm", full_page=False)
    with htmx_post(page, "/setup/purge/"):
        confirm.click()
    expect(page.locator("[data-snapshot-toast]")).to_be_visible()
    assert not Racer.objects.filter(event=event).exists()
    taken = backups.snapshots(event)
    assert len(taken) == 1 and taken[0].exists()
    assert taken[0].name.startswith("before-racers-")
    expect(data.locator('[data-purge="racers"] [data-count]')).to_have_text("0 racers")


def test_setup_links_downloads_and_snapshot_confirm(login, event):
    """setup.php parity: the links to the other set-up pages, the
    preferences and snapshot downloads, and the import confirmation."""
    add_racers(event, "Lions", 3, 101)
    page = coordinator(login)
    page.goto("/setup/")
    page.locator('[data-section="data"]').scroll_into_view_if_needed()
    shoot(page, "setup-data", full_page=False)
    links = page.locator("[data-links]")
    for name in (
        "Import roster",
        "Racing groups",
        "Import awards",
        "Settings",
        "Scene editor",
        "Rounds playlist",
        "Fake roster",
    ):
        expect(links.get_by_role("link", name=name, exact=True)).to_be_visible()
    with page.expect_download() as download:
        page.locator(
            '[data-section="preferences"] [data-download="preferences"]'
        ).click()
    assert "supergroup-label" in open(download.value.path()).read()
    with page.expect_download() as download:
        page.locator('[data-download="snapshot"]').click()
    assert "<snapshot>" in open(download.value.path()).read()
    snapshots = page.locator('[data-section="snapshots"]')
    snapshots.locator('input[type="file"]').set_input_files(download.value.path())
    snapshots.get_by_role("button", name="Import snapshot…").click()
    confirm = page.locator('[data-sheet="snapshot-import"]')
    expect(confirm).to_contain_text("lane closures, racer notes, check-in times")
    shoot(page, "setup-snapshot-import", full_page=False)
    with htmx_post(page, "/setup/snapshot/"):
        confirm.get_by_role("button", name="Replace all data").click()
    expect(page.locator("[data-import-report]")).to_be_visible()


def test_preferences_upload_lists_what_applied(login, event, tmp_path):
    prefs = tmp_path / "derbynet.pref"
    prefs.write_text("supergroup-label = Troop\nbogus = 1\n")
    page = coordinator(login)
    page.goto("/setup/")
    section = page.locator('[data-section="preferences"]')
    section.locator('input[type="file"]').set_input_files(prefs)
    with htmx_post(page, "/setup/preferences/"):
        section.get_by_role("button", name="Apply preferences").click()
    report = section.locator("[data-preferences-report]")
    expect(report.locator("[data-applied]")).to_contain_text("supergroup-label = Troop")
    expect(report.get_by_role("alert")).to_contain_text("line 2")


# ----- roles and about -------------------------------------------------------------


def test_roles_page_changes_a_password(login, event):
    page = coordinator(login)
    page.goto("/settings/roles/")
    crew = page.locator('[data-role-card="RaceCrew"]')
    expect(crew).to_contain_text("Default password")
    shoot(page, "roles")
    crew.get_by_label("New password for RaceCrew").fill("pinewood")
    with htmx_post(page, "/settings/roles/RaceCrew/password/"):
        crew.get_by_role("button", name="Change password").click()
    expect(page.locator('[data-role-card="RaceCrew"] [data-saved]')).to_be_visible()
    assert Role.objects.get(name="RaceCrew").check_password("pinewood")


def test_about_page(login, event):
    page = coordinator(login)
    page.goto("/about/")
    expect(page.locator("[data-about-version]")).to_be_visible()
    expect(page.locator("[data-time-zone]")).to_be_visible()
    expect(page.locator("[data-schema]")).to_contain_text("up to date")
    expect(page.locator("[data-timer]")).to_be_visible()
    shoot(page, "about")
    assert_accessible(page)
