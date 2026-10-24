import pytest

from awards.models import AwardType
from core.models import Event, RaceSettings, RaceState, SiteState
from core.services import new_event
from core.tests.db import refused
from kiosks.models import ReplayStatus, Scene, SceneKiosk
from racing.models import TimerStatus
from roster.models import Group
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db

# website/sql/sqlite/award-type-values.inc
EXPECTED_AWARD_TYPES = [
    (1, "Design General"),
    (2, "Speed General"),
    (3, "Other"),
    (4, "Design Trophy"),
    (5, "Speed Trophy"),
    (6, "Ad Hoc"),
]

# website/sql/sqlite/scene-tables.inc: make_scene(sceneid, name, ...) sets
# sortorder to sceneid.
EXPECTED_SCENES = [
    ("Welcome", 1),
    ("Opening", 2),
    ("Check-In", 3),
    ("Racing", 4),
    ("Awards", 5),
    ("Wrap-Up", 6),
]
EXPECTED_SCENE_KIOSKS = {
    ("Welcome", "Main", "kiosks/welcome.kiosk"),
    ("Opening", "Main", "kiosks/flag.kiosk"),
    ("Opening", "Aux1", "kiosks/flag.kiosk"),
    ("Opening", "Aux2", "kiosks/flag.kiosk"),
    ("Check-In", "Main", "kiosks/slideshow.kiosk"),
    ("Check-In", "Aux1", "kiosks/please-check-in.kiosk"),
    ("Check-In", "Aux2", "kiosks/welcome.kiosk"),
    ("Racing", "Main", "kiosks/now-racing.kiosk"),
    ("Racing", "Aux1", "kiosks/ondeck.kiosk"),
    ("Racing", "Aux2", "kiosks/results-by-racer.kiosk"),
    ("Awards", "Main", "kiosks/award-presentations.kiosk"),
    ("Wrap-Up", "Main", "kiosks/standings.kiosk"),
    ("Wrap-Up", "Aux1", "kiosks/derbynet.kiosk"),
    ("Wrap-Up", "Aux2", "local/kiosks/feedback.kiosk"),
}


def test_migrate_and_new_event_seed_the_legacy_award_types_and_scenes_exactly():
    event = new_event("Pack 1 Derby")

    assert list(AwardType.objects.order_by("id").values_list("id", "name")) == (
        EXPECTED_AWARD_TYPES
    )
    assert (
        list(
            Scene.objects.filter(event=event)
            .order_by("sort_order")
            .values_list("name", "sort_order")
        )
        == EXPECTED_SCENES
    )
    scene_kiosks = SceneKiosk.objects.filter(event=event)
    assert {(sk.scene.name, sk.kiosk_name, sk.page) for sk in scene_kiosks} == (
        EXPECTED_SCENE_KIOSKS
    )
    assert scene_kiosks.count() == len(EXPECTED_SCENE_KIOSKS)
    assert all(sk.params == {} for sk in scene_kiosks)


def test_a_new_award_type_after_the_seed_gets_a_fresh_id():
    # Without the sequence reset, Postgres hands out id 1 again.
    created = AwardType.objects.create(name="Custom")
    assert created.id > 6


def test_new_event_creates_each_per_event_row_once_with_the_legacy_defaults():
    event = new_event("Pack 1 Derby")

    settings = RaceSettings.for_event(event)
    # website/sql/sqlite/schema.inc writes these five on a new database.
    assert settings.photos_on_now_racing == "head"
    assert settings.show_cars_on_deck
    assert settings.show_racer_photos_rr
    assert settings.show_car_photos_rr
    assert settings.upload_videos
    assert settings.photo_directory.endswith(f"events/{event.slug}/racers")
    assert settings.car_photo_directory.endswith(f"events/{event.slug}/cars")
    assert settings.video_directory.endswith(f"events/{event.slug}/videos")
    assert settings.logs_directory.endswith(f"events/{event.slug}/logs")

    state = RaceState.for_event(event)
    assert (state.current_round, state.racing, state.refused_results) == (
        None,
        False,
        0,
    )
    assert TimerStatus.for_event(event).state == TimerStatus.State.NOT_CONNECTED
    assert ReplayStatus.for_event(event).state == ReplayStatus.State.NOT_CONNECTED
    for model in (RaceSettings, RaceState, TimerStatus, ReplayStatus):
        assert model.objects.filter(event=event).count() == 1


def test_the_first_event_becomes_active_and_later_events_do_not_switch():
    first = new_event("Derby")
    second = new_event("Derby")

    assert SiteState.get().active_event == first
    assert first.slug == "derby"
    assert second.slug == "derby-2"


def test_a_failed_new_event_leaves_nothing_behind(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("replay row")

    monkeypatch.setattr(ReplayStatus.objects, "create", broken)
    with pytest.raises(RuntimeError):
        new_event("Derby")
    assert not Event.objects.exists()
    assert not Scene.objects.exists()


def test_two_events_hold_the_same_group_names_and_racers():
    for name in ("Spring Derby", "Fall Derby"):
        event = new_event(name)
        bears = GroupFactory(event=event, name="Bears", sort_order=1)
        RacerFactory(group=bears, first_name="Ann", last_name="Lee", car_number=101)

    assert Group.objects.filter(name="Bears").count() == 2


def test_one_event_refuses_a_second_group_with_the_same_name_in_any_case():
    event = new_event("Derby")
    GroupFactory(event=event, name="Bears")

    with refused():
        GroupFactory(event=event, name="bears")
