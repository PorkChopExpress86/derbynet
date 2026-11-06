"""The set-up page (#48): the first-run wizard, the health checks, the data
section's purges behind an automatic snapshot (D78), the preferences upload
report, the snapshot import, and the fake roster sheet."""

from pathlib import Path

import pytest
from django.test import Client, override_settings

from accounts.permissions import SESSION_ROLE
from core.models import Event, RaceSettings, SiteState
from roster.models import Racer
from transfer import backups, snapshot
from ui.tests.race import add_racers, race_setup, scheduled

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parents[2] / "transfer" / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _data_dir(settings, tmp_path):
    """The automatic snapshots go under the event's folder in the data
    folder, so each test gets a data folder of its own."""
    settings.DATA_DIR = tmp_path


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


def html(response) -> str:
    return response.content.decode()


def hx(client, url, data=None, **extra):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", **extra)


# ----- the first run ---------------------------------------------------------------


def test_a_fresh_install_reaches_the_dashboard_through_the_wizard(db):
    """No event, no login: the wizard creates the event, logs in as the
    post-setup role, and its last step links to the race dashboard."""
    visitor = Client()
    page = visitor.get("/setup/")
    assert page.status_code == 200
    assert 'data-step="event" data-step-status="unknown"' in html(page)
    assert 'data-step="lanes"' not in html(page)

    created = visitor.post("/setup/event/new/", {"name": "Pack 9 Derby"})
    assert created.status_code == 302 and created["Location"] == "/setup/"
    event = Event.objects.get(name="Pack 9 Derby")
    assert SiteState.get().active_event == event
    assert visitor.session[SESSION_ROLE] == "RaceCoordinator"

    page = html(visitor.get("/setup/"))
    for step in ("event", "schema", "time-zone", "login"):
        assert f'data-step="{step}" data-step-status="ok"' in page, step
    assert 'data-step="lanes" data-step-status="unknown"' in page
    visitor.post("/settings/save/", {"key": "n-lanes", "value": "4"})
    assert RaceSettings.for_event(event).lane_count == 4
    assert visitor.get("/dashboard/").status_code == 200


def test_an_event_name_that_is_not_a_folder_name_is_refused(db):
    response = Client().post("/setup/event/new/", {"name": "a/b"})
    assert response.status_code == 422
    assert "not a usable folder name" in html(response)
    assert not Event.objects.exists()


def test_opening_another_event_keeps_the_login(coordinator, event):
    from core.tests.factories import EventFactory

    other = EventFactory(name="Last Year")
    coordinator.post("/setup/event/open/", {"name": other.slug})
    assert SiteState.get().active_event == other
    assert coordinator.session[SESSION_ROLE] == "RaceCoordinator"


def test_the_wizard_shows_the_time_zone_from_the_environment(coordinator, monkeypatch):
    monkeypatch.setenv("DERBYNET_TIME_ZONE", "America/Chicago")
    with override_settings(TIME_ZONE="America/Chicago"):
        body = html(coordinator.get("/setup/"))
    assert 'data-time-zone="America/Chicago"' in body
    assert "(from DERBYNET_TIME_ZONE)" in body


def test_the_crew_cannot_open_setup(login, event):
    assert login("RaceCrew").get("/setup/").status_code == 302


# ----- health checks ---------------------------------------------------------------


def _check(body: str, key: str) -> str:
    return body.split(f'data-check="{key}" data-check-status="')[1].split('"')[0]


def test_health_checks_report_the_photo_folders(coordinator, event, tmp_path, settings):
    settings.DATA_DIR = tmp_path
    racers = tmp_path / "racers"
    racers.mkdir()
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(racers), car_photo_directory=str(tmp_path / "missing")
    )
    body = html(coordinator.get("/setup/"))
    assert _check(body, "photo-dir") == "ok"
    assert _check(body, "car-photo-dir") == "trouble"
    racers.chmod(0o555)
    try:
        assert _check(html(coordinator.get("/setup/")), "photo-dir") == "readonly"
    finally:
        racers.chmod(0o755)


def test_health_checks_report_the_database(coordinator, event):
    assert _check(html(coordinator.get("/setup/")), "database") == "ok"


def test_health_checks_compare_the_upload_limit(coordinator, event, settings):
    assert _check(html(coordinator.get("/setup/")), "upload") == "ok"
    settings.PHOTO_UPLOAD_MAX_BYTES = 8_000_000
    body = html(coordinator.get("/setup/"))
    assert _check(body, "upload") == "trouble"
    assert "Photos up to 8 MB are accepted" in body
    assert "the photo stand sends up to 30 MB" in body


def test_health_checks_explain_an_unwritable_config(
    coordinator, event, settings, tmp_path
):
    settings.CONFIG_DIR = tmp_path / "gone"
    body = html(coordinator.get("/setup/"))
    assert _check(body, "config") == "trouble"
    assert "DERBYNET_CONFIG_DIR" in body and "mount a writable volume" in body


# ----- the data section ------------------------------------------------------------


def test_purge_racers_confirms_with_the_count(coordinator, event):
    add_racers(event, "Lions", 3, 101)
    body = html(coordinator.get("/setup/purge/", {"what": "racers"}))
    assert "Delete 3 racers" in body
    assert "snapshot of the event is saved first" in body


def test_purge_takes_a_snapshot_then_deletes(coordinator, event):
    add_racers(event, "Lions", 3, 101)
    response = hx(coordinator, "/setup/purge/", {"what": "racers"})
    assert response.status_code == 200
    assert not Racer.objects.filter(event=event).exists()
    taken = backups.snapshots(event)
    assert len(taken) == 1 and taken[0].name.startswith("before-racers-")
    assert "data-snapshot-download" in html(response)
    saved = taken[0].read_text()
    assert saved.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<snapshot>')
    assert '"firstname"' in saved
    download = coordinator.get(f"/setup/snapshots/{taken[0].name}")
    assert download.status_code == 200
    assert b"".join(download.streaming_content).decode() == saved


def test_each_purge_keeps_every_snapshot(coordinator, event):
    for what in ("results", "schedules", "awards", "reinitialize"):
        hx(coordinator, "/setup/purge/", {"what": what})
    names = sorted(p.name.split("-")[1] for p in backups.snapshots(event))
    assert names == ["awards", "reinitialize", "results", "schedules"]


def test_a_saved_snapshot_restores_the_purged_racers(coordinator, event):
    add_racers(event, "Lions", 3, 101)
    hx(coordinator, "/setup/purge/", {"what": "racers"})
    content = backups.snapshots(event)[0].read_bytes()
    snapshot.put(event, content)
    restored = SiteState.get().active_event
    assert Racer.objects.filter(event=restored).count() == 3


def test_a_snapshot_that_cannot_be_written_stops_the_purge(
    coordinator, event, monkeypatch
):
    add_racers(event, "Lions", 2, 101)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail)
    response = hx(coordinator, "/setup/purge/", {"what": "racers"})
    assert response.status_code == 422
    assert "disk full" in html(response)
    assert Racer.objects.filter(event=event).count() == 2


def test_an_unknown_purge_is_refused(coordinator, event):
    assert hx(coordinator, "/setup/purge/", {"what": "everything"}).status_code == 422
    assert coordinator.get("/setup/purge/", {"what": "everything"}).status_code == 404


def test_snapshot_files_are_only_saved_snapshots(coordinator, event):
    assert coordinator.get("/setup/snapshots/..%2F..%2Fsecret_key").status_code == 404
    assert coordinator.get("/setup/snapshots/nope.xml").status_code == 404


# ----- preferences and snapshots ---------------------------------------------------


def _upload(name, content: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type="text/plain")


def test_a_preferences_upload_lists_what_it_applied(coordinator, event):
    content = (
        b"supergroup-label = Troop\nuse-points\nGROUP = Lions\nENDGROUP\n"
        b"bogus-key = 1\n"
    )
    response = coordinator.post(
        "/setup/preferences/", {"prefs": _upload("p.pref", content)}
    )
    body = html(response)
    assert "Applied 4 lines" in body
    assert "supergroup-label = Troop" in body
    assert "Preferences file syntax, line 5" in body
    assert RaceSettings.for_event(event).supergroup_label == "Troop"


def test_the_snapshot_import_snapshots_first_and_replaces(coordinator, event):
    add_racers(event, "Lions", 2, 101)
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n' + snapshot.get(event)
    ).encode()
    add_racers(event, "Tigers", 3, 201)
    response = coordinator.post(
        "/setup/snapshot/",
        {"snapshot": _upload("s.xml", content)},
        HTTP_HX_REQUEST="true",
    )
    body = html(response)
    assert "data-import-report" in body, body
    restored = SiteState.get().active_event
    assert Racer.objects.filter(event=restored).count() == 2
    taken = backups.find_snapshot(
        body.split('class="font-mono underline underline-offset-2">')[1].split("<")[0]
    )
    assert taken is not None and taken.name.startswith("before-import-")
    assert coordinator.get(f"/setup/snapshots/{taken.name}").status_code == 200


def test_a_bad_snapshot_is_refused_and_changes_nothing(coordinator, event):
    add_racers(event, "Lions", 2, 101)
    response = coordinator.post(
        "/setup/snapshot/",
        {"snapshot": _upload("s.xml", b"<nope/>")},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 422
    assert Racer.objects.filter(event=SiteState.get().active_event).count() == 2


def test_the_page_names_what_a_snapshot_leaves_out(coordinator, event):
    body = html(coordinator.get("/setup/"))
    confirm = body.split("data-snapshot-confirm")[1]
    for lost in ("lane closures", "racer notes", "check-in times"):
        assert lost in confirm
    assert "all of the data" in confirm


def test_the_page_links_the_other_setup_pages(coordinator, event):
    body = html(coordinator.get("/setup/"))
    for url in (
        "/import/roster/",
        "/groups/",
        "/import/awards/",
        "/settings/",
        "/kiosks/scenes/",
        "/playlist/",
        "/setup/fake-roster/",
        "/settings/roles/",
    ):
        assert f'href="{url}"' in body, url
    for download in ("snapshot", "snapshot-clean", "preferences"):
        assert f'data-download="{download}"' in body


# ----- the fake roster -------------------------------------------------------------


def test_fake_roster_makes_checked_in_racers(coordinator, event):
    response = hx(
        coordinator,
        "/setup/fake-roster/",
        {"ngroups": "6", "racers_per_group": "5", "check_in": ["0", "1"]},
    )
    assert response["HX-Redirect"] == "/checkin/"
    racers = Racer.objects.filter(event=event)
    assert 6 * 3 <= racers.count() <= 6 * 7
    assert not racers.filter(passed_inspection=False).exists()


@pytest.mark.parametrize(
    ("groups", "per"), [("0", "5"), ("11", "5"), ("6", "1"), ("6", "21")]
)
def test_fake_roster_ranges(coordinator, event, groups, per):
    response = hx(
        coordinator, "/setup/fake-roster/", {"ngroups": groups, "racers_per_group": per}
    )
    assert response.status_code == 422
    assert not Racer.objects.filter(event=event).exists()


def test_fake_roster_page_and_sheet(coordinator, event):
    assert "data-fake-roster" in html(coordinator.get("/setup/fake-roster/"))
    sheet = coordinator.get("/setup/fake-roster/", HTTP_HX_REQUEST="true")
    assert "<html" not in html(sheet) and "data-fake-roster" in html(sheet)


def test_lanes_lock_on_the_wizard_too(coordinator, event):
    scheduled(race_setup(event)["Wolves"], race=False)
    body = html(coordinator.get("/setup/"))
    step = body.split('data-step="lanes"')[1].split("</li>")[0]
    assert "data-lane-locked" in step


def test_c56_the_crew_is_refused_at_the_fake_roster(login, event):
    """C56: the page needs SET_UP, as racer.fake does."""
    crew = login("RaceCrew")
    assert crew.get("/setup/fake-roster/").status_code == 302
    refused = hx(crew, "/setup/fake-roster/", {"ngroups": "2", "racers_per_group": "3"})
    assert refused.status_code == 403
    assert not Racer.objects.filter(event=event).exists()


def test_a_preferences_file_php_wrote_gives_phps_settings(coordinator, event):
    """Acceptance: `php_written.pref` is what PHP's preferences.php wrote,
    and `php_written_settings.json` PHP's settings in that event. Its
    `racing_scene = 4` names PHP's fourth scene, Racing, by id, so the line
    names this event's Racing scene instead."""
    import json

    from core import race_settings
    from kiosks.models import Scene
    from roster.models import Group, GroupConstituentSubgroup, Subgroup

    data = Path(__file__).parent / "data"
    racing = Scene.objects.get(event=event, name="Racing")
    text = (data / "php_written.pref").read_text()
    text = text.replace("racing_scene = 4", f"racing_scene = {racing.pk}")
    response = coordinator.post(
        "/setup/preferences/", {"prefs": _upload("derbynet.pref", text.encode())}
    )
    assert "The file applied." in html(response), html(response)
    expected = json.loads((data / "php_written_settings.json").read_text())["settings"]
    assert race_settings.read_many(event, list(expected)) == expected
    assert RaceSettings.for_event(event).racing_scene == racing
    aggregates = GroupConstituentSubgroup.objects.filter(event=event)
    assert aggregates.values("aggregate").distinct().count() == 6
    assert Group.objects.filter(event=event).count() == 3 + 6
    assert (
        Subgroup.objects.filter(event=event, group__name="Lions & Tigers").count() == 4
    )
