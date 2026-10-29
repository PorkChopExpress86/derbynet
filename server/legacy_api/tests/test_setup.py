"""`setup.nodata` and the file endpoints of the setup page (#36, S25)."""

import json

import pytest
from django.test import override_settings

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE, Perm
from core.models import Event, SiteState
from legacy_api.tests.conftest import log_in, post

pytestmark = pytest.mark.django_db


@pytest.fixture
def data_dir(tmp_path):
    with override_settings(DATA_DIR=tmp_path):
        yield tmp_path


def run(client, body, path="/action.php"):
    return json.loads(post(client, body, path).content)


def query(client, text):
    return json.loads(client.get(f"/action.php?query={text}").content)


def test_a_server_with_no_event_sets_up_without_a_login(client, data_dir):
    assert not Event.objects.exists()

    reply = run(client, "action=setup.nodata&ez-new=Trial")

    assert reply["outcome"]["summary"] == "success", reply
    event = Event.objects.get()
    root = data_dir / "events" / event.slug
    for sub in ("racers", "cars", "videos", "logs", "imagery", "slides"):
        assert (root / sub).is_dir(), sub
    assert SiteState.objects.get(pk=1).active_event == event
    assert "details" in reply
    session = client.session
    assert session[SESSION_ROLE] == "RaceCoordinator"
    assert session[SESSION_PERMISSIONS] & Perm.SET_UP


def test_with_an_event_it_needs_set_up(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    reply = run(client, "action=setup.nodata&ez-new=Another")

    assert reply["outcome"]["code"] == "notauthorized"
    assert Event.objects.count() == 1


def test_a_second_event_becomes_the_active_one(client, event, data_dir):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")

    run(client, "action=setup.nodata&ez-new=Second")

    assert SiteState.objects.get(pk=1).active_event.name == "Second"
    assert Event.objects.count() == 2


def test_a_name_in_use_or_unusable_fails(client, event, data_dir):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")

    assert (
        run(client, f"action=setup.nodata&ez-new={event.name}")["outcome"]["code"]
        == "already_exists"
    )
    assert (
        run(client, "action=setup.nodata&ez-new=..%2Fescape")["outcome"]["code"]
        == "mkdir"
    )
    assert not (data_dir.parent / "escape").exists()


def test_ez_old_opens_an_existing_event(client, event, data_dir):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    run(client, "action=setup.nodata&ez-new=Second")

    reply = run(client, f"action=setup.nodata&ez-old={event.name}")

    assert reply["outcome"]["summary"] == "success"
    assert SiteState.objects.get(pk=1).active_event == event
    assert run(client, "action=setup.nodata&ez-old=Nope")["outcome"]["code"] == (
        "not-recognized"
    )


def test_a_connection_string_is_refused_with_its_own_code(client, event):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")

    reply = run(client, "action=setup.nodata&connection_string=sqlite%3A%2Ftmp%2Fx")

    assert reply["outcome"]["code"] == "connection-string"


def test_locked_settings_refuse_it(client, event):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")

    with override_settings(LOCK_SETTINGS=True):
        reply = run(client, "action=setup.nodata&ez-new=Locked")

    assert reply["outcome"]["code"] == "locked"


def test_the_file_endpoints_need_set_up(client, event, data_dir):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert query(client, "file.directory.nodata&path=/")["outcome"]["code"] == (
        "notauthorized"
    )
    assert query(client, "file.stat&path=/etc/passwd")["outcome"]["code"] == (
        "notauthorized"
    )


def test_a_listing_stays_inside_the_data_folder(client, event, data_dir):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    (data_dir / "events").mkdir()
    (data_dir / "note.txt").write_text("x")
    (data_dir / ".hidden").write_text("x")

    inside = query(client, f"file.directory.nodata&path={data_dir}")
    outside = query(client, "file.directory.nodata&path=/etc")

    assert "outcome" not in inside  # PHP sets none on success
    assert {f["file"] for f in inside["files"]} == {"events", "note.txt"}
    assert inside["base-paths"][-1]["path"] == f"{data_dir}/"
    assert outside["outcome"]["code"] == "outside"
    assert outside["path"] == f"{data_dir}/"
    assert "passwd" not in json.dumps(outside)


def test_a_listing_of_a_file_names_it_as_chosen(client, event, data_dir):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    (data_dir / "note.txt").write_text("x")

    reply = query(client, f"file.directory.nodata&path={data_dir}/note.txt")

    assert reply["chosen"] == "note.txt"


def test_stat_answers_inside_and_refuses_outside(client, event, data_dir):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")

    inside = query(client, f"file.stat&path={data_dir}")
    outside = query(client, "file.stat&path=/etc/passwd")

    assert inside["stat"]["isdir"] and inside["stat"]["writable"]
    assert outside["outcome"]["code"] == "outside" and "stat" not in outside


def test_a_new_folder_only_inside_the_data_folder(client, event, data_dir):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")

    made = run(client, f"action=file.directory.new.nodata&directory={data_dir}/new")
    escaped = run(client, f"action=file.directory.new.nodata&directory={data_dir}/../x")
    elsewhere = run(client, "action=file.directory.new.nodata&directory=/tmp/derby-x")

    assert made["outcome"]["summary"] == "success" and (data_dir / "new").is_dir()
    assert escaped["outcome"]["code"] == "mkdir"
    assert elsewhere["outcome"]["code"] == "mkdir"
    assert not (data_dir.parent / "x").exists()
