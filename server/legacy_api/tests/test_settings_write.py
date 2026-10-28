"""`settings.write` (#63): the declarative table, PHP's failure codes, and the
`-checkbox` rule."""

import json
from urllib.parse import urlencode

import pytest
from django.test import override_settings

from accounts.permissions import Perm
from core import race_settings
from core.legacy_keys import REGISTRY
from core.models import ActionLog, RaceSettings
from core.settings_form import FORM_SETTINGS
from kiosks.models import Scene
from legacy_api.tests.conftest import log_in, post
from racing.tests.factories import HeatLaneFactory

pytestmark = pytest.mark.django_db

# Each form key to a value it posts and the legacy text that reads back.
SAMPLES = {
    "ballot_password": ("pw", "pw"),
    "car-numbering": ("200+201", "200+201"),
    "car-photo-dir": ("/tmp/cars", "/tmp/cars"),
    "do-use-subgroups": ("1", "1"),
    "finish-formatting": ("%5.4f", "%5.4f"),
    "images-dir": ("Cars", "Cars"),
    "lane-colors": ("red,blue", "red,blue"),
    "log-dir": ("/tmp/logs", "/tmp/logs"),
    "max-runs-per-car": ("3", "3"),
    "n-den-trophies": ("2", "2"),
    "n-lanes": ("4", "4"),
    "n-pack-trophies": ("5", "5"),
    "n-rank-trophies": ("1", "1"),
    "name-style": ("2", "2"),
    "now-racing-linger-ms": ("5000", "5000"),
    "one-trophy-per": ("1", "1"),
    "partition-label": ("Patrol", "Patrol"),
    "photo-dir": ("/tmp/photos", "/tmp/photos"),
    "photos-on-now-racing": ("car", "car"),
    "racing_scene": ("SCENE", "SCENE"),
    "replay-num-showings": ("3", "3"),
    "replay-rate": ("75", "75"),
    "replay-skipback": ("3500", "3500"),
    "reverse-lanes": ("1", "1"),
    "rotation-schedule": ("1", "1"),
    "scoring": ("1", "1"),
    "show-car-photos-on-deck": ("1", "1"),
    "show-car-photos-rr": ("1", "1"),
    "show-racer-photos-rr": ("1", "1"),
    "supergroup-label": ("Troop", "Troop"),
    "timer-send-logs": ("1", "1"),
    "track-length": ("42", "42"),
    "unused-lane-mask": ("2", "2"),
    "update-period": ("1000", "1000"),
    "upload-videos": ("1", "1"),
    "use-master-sched": ("1", "1"),
    "use-points": ("1", "1"),
    "use-xbs": ("1", "1"),
    "video-dir": ("/tmp/videos", "/tmp/videos"),
    "warn-no-timer": ("1", "1"),
    "xbs-award": ("Scout Pick", "Scout Pick"),
}


@pytest.fixture
def coordinator(client, event):
    log_in(client, Perm.SET_UP)
    return client


def write(client, **fields):
    body = urlencode({"action": "settings.write", **fields})
    return json.loads(post(client, body).content)


def checkbox_body(form_key, on):
    return {f"{form_key}-checkbox": "1", **({form_key: "1"} if on else {})}


def test_the_samples_cover_the_whole_table():
    assert set(SAMPLES) == set(FORM_SETTINGS)


def test_every_table_entry_names_a_registered_key():
    assert {s.setting for s in FORM_SETTINGS.values()} <= set(REGISTRY)


@pytest.mark.parametrize("form_key", sorted(SAMPLES))
def test_every_key_in_the_table_writes_and_reads_back(coordinator, event, form_key):
    posted, expected = SAMPLES[form_key]
    if expected == "SCENE":
        posted = expected = str(Scene.objects.filter(event=event).first().pk)
    setting = FORM_SETTINGS[form_key]
    if setting.kind.value == "checkbox":
        fields = checkbox_body(form_key, on=True)
        written = [f"{form_key}-checkbox"]
    else:
        fields = {form_key: posted}
        written = [form_key]

    body = write(coordinator, **fields)

    assert body["outcome"]["code"] == "success"
    assert body["written"] == written
    assert race_settings.read(event, setting.setting) == expected


def test_a_checkbox_is_written_only_with_its_hidden_field(coordinator, event):
    RaceSettings.objects.filter(event=event).update(use_points=True)

    body = write(coordinator, **{"use-points": "1"})

    # PHP counts the field as unrecognized and leaves the setting alone.
    assert body["outcome"]["code"] == "unrecognized"
    assert race_settings.read(event, "use-points") == "1"
    assert body["written"] == []


def test_a_checkbox_field_without_the_value_clears_the_setting(coordinator, event):
    RaceSettings.objects.filter(event=event).update(use_points=True)

    body = write(coordinator, **checkbox_body("use-points", on=False))

    assert body["outcome"]["code"] == "success"
    assert race_settings.read(event, "use-points") == "0"


def test_an_unknown_field_is_unrecognized_but_known_ones_are_written(
    coordinator, event
):
    body = write(coordinator, **{"n-lanes": "6", "nonesuch": "1", "other": "2"})

    assert body["outcome"] == {
        "summary": "failure",
        "code": "unrecognized",
        "description": "2 unrecognized setting(s)",
    }
    assert body["written"] == ["n-lanes"]
    assert race_settings.read(event, "lane_count") == "6"


@pytest.mark.parametrize(
    ("form_key", "posted", "stored"),
    [
        ("n-lanes", "5x", "5"),
        ("n-lanes", "abc", "0"),
        ("max-runs-per-car", "2.9", "2"),
        ("replay-rate", "abc", "0"),
        ("replay-rate", "80.0", "80"),
    ],
)
def test_numbers_are_read_as_php_reads_them(
    coordinator, event, form_key, posted, stored
):
    body = write(coordinator, **{form_key: posted})

    assert body["outcome"]["code"] == "success"
    assert race_settings.read(event, FORM_SETTINGS[form_key].setting) == stored


@pytest.mark.parametrize(
    ("form_key", "posted"),
    [
        ("n-lanes", "-3"),
        ("n-lanes", "70000"),
        ("lane-colors", "x" * 201),
        ("racing_scene", "999999"),
        ("name-style", "left"),
    ],
)
def test_a_value_the_field_cannot_hold_is_not_written(
    coordinator, event, form_key, posted
):
    before = race_settings.read(event, FORM_SETTINGS[form_key].setting)

    body = write(coordinator, **{form_key: posted})

    assert body["outcome"]["code"] == "not-written"
    assert body["outcome"]["description"].startswith("Value for ")
    assert race_settings.read(event, FORM_SETTINGS[form_key].setting) == before


def test_later_fields_are_written_after_one_is_not_written(coordinator, event):
    body = write(coordinator, **{"n-lanes": "-3", "partition-label": "Patrol"})

    assert body["outcome"]["code"] == "not-written"
    assert race_settings.read(event, "partition-label") == "Patrol"


@pytest.mark.parametrize("form_key", ["n-lanes", "unused-lane-mask"])
def test_lane_changes_are_refused_while_heats_are_unraced(coordinator, event, form_key):
    HeatLaneFactory(round__group__event=event, event=event)
    before = race_settings.read(event, "lane_count")

    body = write(coordinator, **{form_key: "5", "partition-label": "Patrol"})

    assert body["outcome"]["code"] == "already-scheduled"
    assert body["written"] == []
    assert race_settings.read(event, "lane_count") == before
    assert race_settings.read(event, "partition-label") != "Patrol"


def test_posting_the_current_lane_count_is_not_a_change(coordinator, event):
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    HeatLaneFactory(round__group__event=event, event=event)

    # PHP compares numeric text as numbers, so 04 is not a change.
    body = write(coordinator, **{"n-lanes": "04"})

    assert body["outcome"]["code"] == "success"


def test_lane_changes_are_allowed_once_every_heat_is_raced(coordinator, event):
    from django.utils import timezone

    HeatLaneFactory(round__group__event=event, event=event, completed=timezone.now())

    assert write(coordinator, **{"n-lanes": "5"})["outcome"]["code"] == "success"


@override_settings(LOCK_SETTINGS=True)
@pytest.mark.parametrize("form_key", ["photo-dir", "car-photo-dir", "video-dir"])
def test_the_directories_are_locked_when_the_install_locks_settings(
    coordinator, event, form_key
):
    before = race_settings.read(event, FORM_SETTINGS[form_key].setting)

    body = write(coordinator, **{form_key: "/tmp/x", "partition-label": "Patrol"})

    assert body["outcome"]["code"] == "locked"
    assert body["written"] == []
    assert race_settings.read(event, FORM_SETTINGS[form_key].setting) == before


@override_settings(LOCK_SETTINGS=True)
def test_the_log_directory_stays_open_when_settings_are_locked(coordinator, event):
    assert write(coordinator, **{"log-dir": "/tmp/l"})["outcome"]["code"] == "success"


def test_the_directories_are_open_when_settings_are_not_locked(coordinator, event):
    assert write(coordinator, **{"photo-dir": "/tmp/p"})["outcome"]["code"] == "success"


def test_without_set_up_nothing_is_written(client, event):
    log_in(client, Perm.CONTROL_RACE)

    body = write(client, **{"partition-label": "Patrol"})

    assert body["outcome"]["code"] == "notauthorized"
    assert race_settings.read(event, "partition-label") != "Patrol"


def test_the_action_log_keeps_the_posted_parameter_names(coordinator, event):
    write(coordinator, **{"n-lanes": "4", **checkbox_body("use-points", on=True)})

    entry = ActionLog.objects.filter(event=event).latest("pk")
    assert entry.request == {
        "action": "settings.write",
        "n-lanes": "4",
        "use-points-checkbox": "1",
        "use-points": "1",
    }
