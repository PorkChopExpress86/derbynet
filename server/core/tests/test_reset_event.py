"""`reset_event()`: the `schema` script of `database.execute` (#63)."""

import pytest

from core.invariants import check_invariants
from core.models import (
    ActionLog,
    Event,
    PerEvent,
    RaceSettings,
    RaceState,
    event_scoped_models,
)
from core.services import reset_event
from core.tests.factories import EventFactory
from core.tests.populated import populated_event
from kiosks.models import Scene

pytestmark = pytest.mark.django_db

KEPT = {
    "photo_directory": "/data/photos",
    "car_photo_directory": "/data/cars",
    "video_directory": "/data/videos",
    "logs_directory": "/data/logs",
    "lane_count": 4,
    "websocket_url": "ws://example/ws",
    "ws_trigger_port": "8400",
}


def counts(event):
    return {
        model._meta.label_lower: model.objects.filter(event=event).count()
        for model in event_scoped_models()
    }


def test_every_table_is_empty_after_a_reset_except_the_fresh_event_rows():
    event = populated_event()

    reset_event(event)

    assert counts(event) == counts(EventFactory())
    assert check_invariants(event) == {}


def test_the_action_log_is_emptied_as_php_drops_its_history_table():
    event = populated_event()
    other = populated_event()
    assert ActionLog.objects.filter(event=event).exists()
    kept = ActionLog.objects.filter(event=other).count()

    reset_event(event)

    assert not ActionLog.objects.filter(event=event).exists()
    assert ActionLog.objects.filter(event=other).count() == kept


def test_the_preserved_settings_survive_and_the_rest_return_to_defaults():
    event = populated_event()
    RaceSettings.objects.filter(event=event).update(
        **KEPT, use_points=True, partition_label="Patrol", scoring=1
    )
    RaceState.objects.filter(event=event).update(racing=True, fake_racers=True)

    reset_event(event)

    settings = RaceSettings.for_event(event)
    for name, value in KEPT.items():
        assert getattr(settings, name) == value
    assert (settings.use_points, settings.partition_label) == (False, "Den")
    assert settings.scoring == 0
    state = RaceState.for_event(event)
    assert (state.racing, state.fake_racers) == (False, False)


def test_the_six_legacy_scenes_come_back():
    event = populated_event()

    reset_event(event)

    names = list(
        Scene.objects.filter(event=event)
        .order_by("sort_order")
        .values_list("name", flat=True)
    )
    assert names == ["Welcome", "Opening", "Check-In", "Racing", "Awards", "Wrap-Up"]


def test_the_legacy_scenes_keep_their_ids_across_a_reset():
    """PHP's schema script recreates the scenes as ids 1 to 6, and the shell
    suites name them by id (`test-scenes.sh`, `test-playlist.sh`,
    `test-preferences.sh`). A reset puts the six back in the rows the event
    started with, so a fresh database keeps 1 to 6 however often it is reset."""
    event = populated_event()
    first = list(
        Scene.objects.filter(event=event).order_by("sort_order").values_list("pk")
    )
    Scene.objects.create(event=event, name="Extra", sort_order=7)
    Scene.objects.filter(pk=first[1][0]).update(name="Renamed", sort_order=9)

    reset_event(event)
    reset_event(event)

    after = list(
        Scene.objects.filter(event=event).order_by("sort_order").values_list("pk")
    )
    assert after == first


def test_another_event_is_untouched():
    event, other = populated_event(), populated_event()
    before = counts(other)

    reset_event(event)

    assert counts(other) == before
    assert Event.objects.filter(pk=other.pk).exists()


def test_the_per_event_rows_are_the_only_ones_recreated():
    assert {m.__name__ for m in event_scoped_models() if issubclass(m, PerEvent)} == {
        "RaceSettings",
        "RaceState",
        "TimerStatus",
        "ReplayStatus",
    }
