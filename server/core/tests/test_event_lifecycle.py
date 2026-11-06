"""The event lifecycle of #9 that #212 builds: Copy from, switch, archive and
Reopen for editing (`core.events`)."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE
from awards.listing import AD_HOC
from awards.models import Award, AwardPartition, BallotAward
from core import events
from core.models import Event, RaceSettings, RaceState, SiteState, event_scoped_models
from core.services import EventReadOnly
from core.tests.populated import populated_event
from kiosks.models import Kiosk, Scene, SceneKiosk
from racing.models import HeatLane, Round, TimerStatus
from roster.models import (
    CarRange,
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Racer,
    Subgroup,
)
from roster.services.groups import rename_group
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db


def _settings(event):
    row = RaceSettings.objects.get(event=event)
    out = {}
    for field in RaceSettings._meta.concrete_fields:
        if field.primary_key or field.name == "event":
            continue
        if field.name in events.EVENT_FOLDERS:
            continue
        value = getattr(row, field.name)
        out[field.name] = value.name if hasattr(value, "name") else value
    return out


def _names(model, event, *fields):
    return sorted(
        tuple(str(v) for v in row)
        for row in model.objects.filter(event=event).values_list(*fields)
    )


@pytest.fixture
def source(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    event = populated_event()
    winner = Racer.objects.filter(event=event).first()
    Award.objects.create(
        event=event, name="Shiny", award_type_id=AD_HOC, racer=winner, sort=9
    )
    Kiosk.objects.filter(event=event).update(name="Lobby", page="kiosks/flag.kiosk")
    folder = Path(RaceSettings.objects.get(event=event).photo_directory or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "racer.jpg").write_bytes(b"photo")
    Racer.objects.filter(pk=winner.pk).update(headshot="racer.jpg")
    return event


def test_a_copy_brings_the_setup_and_no_racer_result_or_photo(source):
    copy = events.create_event("Next year", copy_from=source.pk)

    assert copy.copied_from == source
    assert _settings(copy) == _settings(source)
    assert RaceSettings.objects.get(event=copy).full_field_group.event == copy
    assert RaceSettings.objects.get(event=copy).racing_scene.event == copy
    assert _names(Group, copy, "name", "sort_order", "durable", "n_trophies") == (
        _names(Group, source, "name", "sort_order", "durable", "n_trophies")
    )
    assert _names(Subgroup, copy, "group__name", "name", "sort_order") == (
        _names(Subgroup, source, "group__name", "name", "sort_order")
    )
    assert _names(GroupConstituent, copy, "aggregate__name", "constituent__name") == (
        _names(GroupConstituent, source, "aggregate__name", "constituent__name")
    )
    assert _names(
        GroupConstituentSubgroup, copy, "aggregate__name", "subgroup__name"
    ) == _names(GroupConstituentSubgroup, source, "aggregate__name", "subgroup__name")
    assert _names(Partition, copy, "name", "subgroup__name", "sort_order") == (
        _names(Partition, source, "name", "subgroup__name", "sort_order")
    )
    assert _names(CarRange, copy, "partition__name", "first", "last") == (
        _names(CarRange, source, "partition__name", "first", "last")
    )
    definitions = Award.objects.filter(event=source).exclude(award_type_id=AD_HOC)
    assert _names(
        Award, copy, "name", "award_type_id", "group__name", "subgroup__name", "sort"
    ) == sorted(
        tuple(str(v) for v in row)
        for row in definitions.values_list(
            "name", "award_type_id", "group__name", "subgroup__name", "sort"
        )
    )
    assert not Award.objects.filter(event=copy, racer__isnull=False).exists()
    assert not Award.objects.filter(event=copy, award_type_id=AD_HOC).exists()
    assert _names(BallotAward, copy, "award__name", "depth") == (
        _names(BallotAward, source, "award__name", "depth")
    )
    assert _names(AwardPartition, copy, "award__name", "partition__name") == (
        _names(AwardPartition, source, "award__name", "partition__name")
    )
    assert _names(Kiosk, copy, "address", "name", "page") == [
        (address, name, "")
        for address, name, _ in _names(Kiosk, source, "address", "name", "page")
    ]
    assert _names(Scene, copy, "name", "sort_order") == (
        _names(Scene, source, "name", "sort_order")
    )
    assert _names(SceneKiosk, copy, "scene__name", "kiosk_name", "page") == (
        _names(SceneKiosk, source, "scene__name", "kiosk_name", "page")
    )

    assert not Racer.objects.filter(event=copy).exists()
    assert not HeatLane.objects.filter(event=copy).exists()
    assert not Round.objects.filter(event=copy).exists()
    photos = Path(RaceSettings.objects.get(event=copy).photo_directory)
    assert photos != Path(RaceSettings.objects.get(event=source).photo_directory)
    assert list(photos.iterdir()) == []


def test_a_copy_leaves_the_source_unchanged(source):
    before = {
        model._meta.label: model.objects.filter(event=source).count()
        for model in event_scoped_models()
    }

    events.create_event("Next year", copy_from=source.pk)

    assert before == {
        model._meta.label: model.objects.filter(event=source).count()
        for model in event_scoped_models()
    }


def test_every_event_scoped_model_is_copied_or_says_why_not():
    labels = {model._meta.label_lower for model in event_scoped_models()}

    assert set(events.COPIED) | set(events.NOT_COPIED) == labels
    assert not set(events.COPIED) & set(events.NOT_COPIED)


def test_a_copy_takes_only_the_parts_asked_for(source):
    copy = events.create_event(
        "Kiosks only", copy_from=source.pk, parts=[events.Part.KIOSKS]
    )

    assert Kiosk.objects.filter(event=copy).count() == 1
    assert not Group.objects.filter(event=copy).exists()
    assert not Award.objects.filter(event=copy).exists()
    # The six scenes of a new event, not the source's.
    assert Scene.objects.filter(event=copy).count() == 6
    assert RaceSettings.objects.get(event=copy).full_field_rule == "standard"


def test_award_definitions_need_their_classes(source):
    with pytest.raises(events.EventRefused) as refused:
        events.create_event("X", copy_from=source.pk, parts=[events.Part.AWARDS])

    assert refused.value.code == "copy-awards-needs-classes"
    assert not Event.objects.filter(name="X").exists()


def test_a_copy_of_a_settings_group_falls_back_to_standard_without_classes(source):
    copy = events.create_event(
        "Settings only", copy_from=source.pk, parts=[events.Part.SETTINGS]
    )

    row = RaceSettings.objects.get(event=copy)
    assert (row.full_field_rule, row.full_field_group) == ("standard", None)


def test_a_new_event_has_its_folders_and_is_not_active(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    first = events.create_event("First")

    second = events.create_event("Second")

    assert SiteState.get().active_event == first
    for sub in events.SUBFOLDERS:
        assert (tmp_path / "events" / second.slug / sub).is_dir()


@pytest.mark.parametrize("name", ["", "  ", "a/b", "..", "x" * 201])
def test_a_name_is_refused(name):
    with pytest.raises(events.EventRefused):
        events.create_event(name)


def test_a_taken_name_is_refused():
    events.create_event("Pack 1")

    with pytest.raises(events.EventRefused) as refused:
        events.create_event("Pack 1")
    assert refused.value.code == "already_exists"


def test_a_switch_makes_the_replaced_event_read_only():
    old = events.create_event("Old")
    new = events.create_event("New")

    switched = events.switch_to(new.pk)

    assert switched.replaced == old
    assert SiteState.get().active_event == new
    assert Event.objects.get(pk=old.pk).read_only
    assert not Event.objects.get(pk=new.pk).read_only


def test_a_switch_back_leaves_a_past_event_read_only():
    old = events.create_event("Old")
    new = events.create_event("New")
    events.switch_to(new.pk)

    events.switch_to(old.pk)

    assert SiteState.get().active_event == old
    assert Event.objects.get(pk=old.pk).read_only
    assert Event.objects.get(pk=new.pk).read_only


def test_a_switch_is_refused_while_racing():
    old = events.create_event("Old")
    new = events.create_event("New")
    RaceState.objects.filter(event=old).update(racing=True)

    with pytest.raises(events.EventRefused) as refused:
        events.switch_to(new.pk)

    assert refused.value.code == "racing"
    assert SiteState.get().active_event == old
    assert not Event.objects.get(pk=old.pk).read_only


def test_a_read_only_event_refuses_a_write_service_until_reopened():
    old = events.create_event("Old")
    group = Group.objects.create(event=old, name="Cubs", sort_order=1)
    events.switch_to(events.create_event("New").pk)

    with pytest.raises(EventReadOnly):
        rename_group(group, "Lions")
    assert Group.objects.get(pk=group.pk).name == "Cubs"

    events.reopen(old.pk)
    rename_group(group, "Lions")
    assert Group.objects.get(pk=group.pk).name == "Lions"


def test_archive_makes_an_event_read_only_and_reopen_unarchives_it():
    old = events.create_event("Old")
    events.switch_to(events.create_event("New").pk)
    events.reopen(old.pk)

    archived = events.archive(old.pk)
    assert archived.read_only and archived.archived_at is not None

    reopened = events.reopen(old.pk)
    assert not reopened.read_only and reopened.archived_at is None


def test_the_active_event_is_not_archived():
    active = events.create_event("Active")

    with pytest.raises(events.EventRefused) as refused:
        events.archive(active.pk)

    assert refused.value.code == "active"
    assert Event.objects.get(pk=active.pk).archived_at is None


def test_reopen_leaves_the_active_event_alone():
    old = events.create_event("Old")
    new = events.create_event("New")
    events.switch_to(new.pk)

    events.reopen(old.pk)

    assert SiteState.get().active_event == new
    assert not Event.objects.get(pk=new.pk).read_only


def test_the_listing_shows_each_events_state_and_racers():
    old = events.create_event("Old")
    RacerFactory(event=old, group__event=old, subgroup__group__event=old)
    new = events.create_event("New")
    events.switch_to(new.pk)
    archived = events.create_event("Archived")
    events.archive(archived.pk)

    listed = {row.event.name: row for row in events.listing()}

    assert listed["Old"].states == [events.State.READ_ONLY]
    assert listed["Old"].racers == 1
    assert listed["New"].states == [events.State.ACTIVE]
    assert listed["New"].racers == 0
    assert listed["Archived"].states == [events.State.ARCHIVED]


# ----- field clients across a switch (#212, Risks) -----


def _timer(client, message, **fields):
    response = client.post(
        "/action.php", {"action": "timer-message", "message": message, **fields}
    )
    root = ET.fromstring(response.content)
    return root.find(".//failure") is None


def test_a_running_timer_and_kiosk_follow_a_switch(client):
    """derby-timer.jar and a kiosk send no event id. Across a switch, the
    timer's next heartbeat is answered, by the new event, and the kiosk's
    next poll shows the new event's page."""
    old = events.create_event("Old")
    new = events.create_event("New")
    session = client.session
    session[SESSION_ROLE] = "Timer"
    session[SESSION_PERMISSIONS] = -1
    session.save()
    assert _timer(client, "HELLO")
    assert _timer(client, "IDENTIFIED", lane_count="4", timer="FastTrack")
    client.get("/action.php", {"query": "poll.kiosk", "address": "K1"})
    assert TimerStatus.objects.get(event=old).last_contact is not None
    assert Kiosk.objects.filter(event=old, address="K1").exists()
    Kiosk.objects.filter(event=new, address="K1").delete()
    Kiosk.objects.create(event=new, address="K1", page="kiosks/flag.kiosk")

    events.switch_to(new.pk)

    assert _timer(client, "HEARTBEAT", confirmed="1")
    assert TimerStatus.objects.get(event=new).last_contact is not None
    kiosk = client.get("/action.php", {"query": "poll.kiosk", "address": "K1"})
    assert kiosk.status_code == 200
    assert b"flag.kiosk" in kiosk.content
    assert Kiosk.objects.get(event=new, address="K1").last_contact is not None
