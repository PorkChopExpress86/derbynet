"""The settings page's views (#48): every field of the settings.php table
saves and reads back PHP's RaceInfo text, the lane lock, the folders and
the settings lock, the replay settings through the replay service, the
weigh-in section, and the drop-slowest key."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from core import race_settings, settings_form
from core.models import RaceSettings
from racing.services import replay
from roster.models import Partition, Racer
from roster.services import weigh_in
from ui.tests.db import no_db_writes
from ui.tests.race import add_racers, race_setup, scheduled

pytestmark = pytest.mark.django_db

PHP = json.loads((Path(__file__).parent / "data" / "settings_php.json").read_text())


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


def save(client, key, value):
    return client.post(
        "/settings/save/", {"key": key, "value": value}, HTTP_HX_REQUEST="true"
    )


def html(response) -> str:
    return response.content.decode()


def _post(text: str) -> dict:
    return dict(pair.split("=", 1) for pair in text.split("&"))


@pytest.mark.parametrize(
    ("key", "value", "post", "legacy", "php"),
    [tuple(c) for c in PHP["cases"]],
    ids=[f"{c[0]}={c[1]}" for c in PHP["cases"]],
)
def test_each_field_saves_what_php_stores(
    coordinator, event, key, value, post, legacy, php
):
    response = save(coordinator, key, value)

    assert response.status_code == 200
    assert "data-saved" in html(response), html(response)
    assert race_settings.read(event, legacy) == php


@pytest.mark.parametrize(
    ("key", "value", "post", "legacy", "php"),
    [tuple(c) for c in PHP["cases"]],
    ids=[f"{c[0]}={c[1]}" for c in PHP["cases"]],
)
def test_php_post_stores_the_same_text(event, key, value, post, legacy, php):
    """The settings.js post itself, through settings.write's service, as a
    check on the recorded PHP text."""
    settings_form.apply_form(event, {"action": "settings.write", **_post(post)})
    assert race_settings.read(event, legacy) == php


def test_car_numbering_saves_both_halves(coordinator, event):
    save(coordinator, "car-numbering-from", "1")
    save(coordinator, "car-numbering-by-partition", "0")
    assert race_settings.read(event, "car-numbering") == PHP["car-numbering"][1]
    save(coordinator, "car-numbering-by-partition", "1")
    assert race_settings.read(event, "car-numbering") == "100+1"


def test_lane_colors_save_one_per_lane(coordinator, event):
    RaceSettings.objects.filter(event=event).update(lane_count=3)
    response = coordinator.post(
        "/settings/lane-colors/", {"color": ["red", "none", "blue"]}
    )
    assert "data-saved" in html(response)
    assert race_settings.read(event, "lane-colors") == PHP["lane-colors"][1]
    coordinator.post("/settings/lane-colors/", {"color": ["none"] * 3})
    assert race_settings.read(event, "lane-colors") == ""
    refused = coordinator.post("/settings/lane-colors/", {"color": ["plaid"]})
    assert "data-error" in html(refused)


def test_lanes_in_use_save_through_the_scheduling_partial(coordinator, event):
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    response = coordinator.post(
        "/playlist/scheduling/lanes/",
        {"part": "lanes", "lanes": "4", "used": ["2", "4"]},
    )
    assert 'id="scheduling-lanes"' in html(response)
    assert 'name="method"' not in html(response)
    assert race_settings.read(event, "unused-lane-mask") == PHP["unused-lane-mask"][1]


def test_scheduling_method_saves_through_the_partial(coordinator, event):
    response = coordinator.post(
        "/playlist/scheduling/method/", {"part": "method", "method": "abbreviated"}
    )
    assert 'id="scheduling-method"' in html(response)
    assert 'name="used"' not in html(response)
    stored = race_settings.read_many(event, ["max-runs-per-car", "rotation-schedule"])
    assert stored == PHP["scheduling-method"][1]


def test_the_page_shows_every_field_and_writes_nothing(coordinator, event):
    with no_db_writes():
        response = coordinator.get("/settings/")
    body = html(response)
    assert response.status_code == 200
    for case in PHP["cases"]:
        assert f'data-field="{case[0]}"' in body
    for key in ("car-numbering-from", "replay-post-roll", "replay-max-clip"):
        assert f'data-field="{key}"' in body
    for section in (
        "track",
        "racers",
        "awards",
        "photos",
        "rules",
        "weigh-in",
        "replay",
    ):
        assert f'data-section="{section}"' in body
    assert 'id="scheduling-lanes"' in body and 'id="scheduling-method"' in body
    assert 'data-time-zone="' in body


def test_numbers_out_of_range_are_refused(coordinator, event):
    response = save(coordinator, "track-length", "1000")
    assert "data-error" in html(response)
    assert race_settings.read(event, "track-length") == "40"
    response = save(coordinator, "n-den-trophies", "two")
    assert "type a number" in html(response)


def test_lane_fields_lock_while_unraced_heats_exist(coordinator, event):
    scheduled(race_setup(event)["Wolves"], race=False)
    body = html(coordinator.get("/settings/"))
    assert "data-lanes-locked" in body
    assert 'href="/dashboard/"' in body
    field = body.split('id="field-n-lanes"')[1].split('id="track-lanes"')[0]
    assert " disabled" in field
    lanes = body.split('id="scheduling-lanes"')[1]
    assert 'name="used" value="1" checked disabled' in lanes

    response = save(coordinator, "n-lanes", "6")
    assert "Remove unraced schedules before changing lane counts." in html(response)
    assert race_settings.read(event, "lane_count") == "4"


def test_a_lane_count_change_sends_the_lanes_and_colors(coordinator, event):
    body = html(save(coordinator, "n-lanes", "3"))
    assert 'id="track-lanes" hx-swap-oob="innerHTML"' in body
    assert body.count('name="color"') == 3


def test_folders_stay_under_the_data_folder(coordinator, event, tmp_path):
    inside = Path(settings.DATA_DIR) / "events" / "wt48-photos"
    inside.mkdir(parents=True, exist_ok=True)
    response = save(coordinator, "photo-dir", str(inside))
    assert 'data-folder-status="ok"' in html(response)
    assert race_settings.read(event, "photo-directory") == str(inside)

    response = save(coordinator, "photo-dir", str(tmp_path))
    assert "pick a folder under" in html(response)
    assert race_settings.read(event, "photo-directory") == str(inside)

    assert 'data-folder-status="outside"' in html(response)
    assert f'value="{tmp_path}"' in html(response)

    missing = save(coordinator, "car-photo-dir", str(inside / "nope"))
    assert 'data-folder-status="missing"' in html(missing)


@override_settings(LOCK_SETTINGS=True)
def test_locked_settings_hide_folder_editing(coordinator, event):
    body = html(coordinator.get("/settings/"))
    assert "data-settings-locked" in body
    assert 'id="set-photo-dir"' not in body
    assert body.count("data-locked-folder") == 3
    response = save(coordinator, "video-dir", str(settings.DATA_DIR))
    assert "Directory settings are locked" in html(response)


def test_replay_saves_all_five_and_the_replay_service_reads_them(coordinator, event):
    for key, value in (
        ("replay-rate", "25"),
        ("replay-num-showings", "3"),
        ("replay-skipback", "3.5"),
        ("replay-post-roll", "1.5"),
        ("replay-max-clip", "8"),
    ):
        assert "data-saved" in html(save(coordinator, key, value)), key
    read = replay.page_settings(RaceSettings.for_event(event))
    assert read["rate"] == 25
    assert read["showings"] == 3
    assert read["skipback_ms"] == 3500
    assert read["post_roll_ms"] == 1500
    assert read["max_clip_ms"] == 8000
    assert replay.clip_length_ms(RaceSettings.for_event(event)) == 5000


def test_the_page_has_no_ice_server_editor(coordinator):
    """D76's replay_ice_servers waits on the owner (#48 report)."""
    assert "ice" not in html(coordinator.get("/settings/")).lower().replace(
        "device", ""
    )


def test_drop_slowest_is_no_setting(event):
    """The importer turns a legacy `drop-slowest` into `scoring=1` once
    (`transfer/tests/test_import_rules.py::test_drop_slowest_becomes_scoring_1_
    unless_scoring_is_set`), so no stored key is left for a page load to
    migrate as settings.php does, and settings.write cannot store one."""
    result = settings_form.apply_form(
        event, {"action": "settings.write", "drop-slowest": "1"}
    )
    assert result.failure[0] == "unrecognized"
    assert race_settings.read(event, "drop-slowest") is None
    assert "drop-slowest" not in race_settings.all_keys(event)


# ----- weigh-in ------------------------------------------------------------------


def _partition(event, name="Lions"):
    add_racers(event, name, 2, 101, passed=False)
    return Partition.objects.get(event=event, name=name)


def test_weigh_in_saves_and_shows_the_maximum_in_the_unit(coordinator, event):
    lions = _partition(event)
    response = coordinator.post(
        f"/settings/weigh-in/{lions.pk}/",
        {"mode": "recorded", "unit": "oz", "max": "5"},
    )
    lions.refresh_from_db()
    assert lions.weigh_in_mode == "recorded" and lions.weight_unit == "oz"
    assert lions.max_weight_grams == Decimal("141.75")
    assert "data-saved" in html(response)
    assert 'name="max" value="5.0"' in html(response)


def test_weigh_in_refusal_shows_inline(coordinator, event):
    lions = _partition(event)
    response = coordinator.post(
        f"/settings/weigh-in/{lions.pk}/", {"mode": "recorded", "unit": "", "max": ""}
    )
    assert "Pick the unit weights are entered in." in html(response)
    lions.refresh_from_db()
    assert lions.weigh_in_mode == "none"


def test_weigh_in_row_says_a_mode_change_clears_weigh_ins(coordinator, event):
    lions = _partition(event)
    weigh_in.set_partition_weigh_in(lions, "pass_fail")
    racer = Racer.objects.filter(partition=lions).first()
    weigh_in.record_weigh_in(racer, passed=True)
    body = html(coordinator.get("/settings/"))
    row = body.split(f'id="weigh-in-{lions.pk}"')[1].split("</li>")[0]
    assert "weighed: 1" in row
    assert "clears the 1 weigh-in already recorded for Lions" in row
    assert "Save and clear 1 weigh-in" in row

    coordinator.post(f"/settings/weigh-in/{lions.pk}/", {"mode": "none"})
    racer.refresh_from_db()
    assert racer.weigh_in_passed is None


def test_weigh_in_of_another_event_is_404(coordinator, event):
    from core.tests.factories import EventFactory

    other = EventFactory(name="Other")
    add_racers(other, "Lions", 1, 101)
    theirs = Partition.objects.get(event=other)
    assert (
        coordinator.post(
            f"/settings/weigh-in/{theirs.pk}/", {"mode": "none"}
        ).status_code
        == 404
    )


def test_crew_cannot_open_settings(login, event):
    crew = login("RaceCrew")
    assert crew.get("/settings/").status_code == 302
    assert save(crew, "track-length", "1").status_code == 403
