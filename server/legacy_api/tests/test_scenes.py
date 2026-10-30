"""The scene endpoints (#34): `scene.add`, `scene.delete`, `scene.setkiosk`,
`scene.apply` and `query=scene.list`, with the current scene of C19."""

import json
from urllib.parse import urlencode

import pytest

from accounts.permissions import Perm
from core.models import RaceSettings, RaceState
from core.tests.factories import EventFactory
from kiosks.models import Scene
from legacy_api.tests.conftest import log_in, post
from racing.tests.factories import PlaylistEntryFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def act(client, action_name, **fields):
    body = urlencode({"action": action_name, **fields})
    return json.loads(post(client, body).content)


def scene_list(client):
    return json.loads(client.get("/action.php", {"query": "scene.list"}).content)


def scene_id(event, name):
    return Scene.objects.get(event=event, name=name).pk


def poll(client, address):
    reply = client.get("/action.php", {"query": "poll.kiosk", "address": address})
    return json.loads(reply.content)["kiosk-setting"]


def test_the_list_holds_the_six_legacy_scenes(coordinator):
    scenes = scene_list(coordinator)["scenes"]

    assert [s["name"] for s in scenes] == [
        "Welcome",
        "Opening",
        "Check-In",
        "Racing",
        "Awards",
        "Wrap-Up",
    ]
    # scene.list sorts by kiosk name alone, unlike all_scenes().
    assert scenes[1]["kiosks"] == [
        {"kiosk_name": name, "page": "kiosks/flag.kiosk", "parameters": "{}"}
        for name in ("Aux1", "Aux2", "Main")
    ]


def test_the_list_needs_a_login(client, event):
    """D5: only kiosk data stays anonymous."""
    assert scene_list(client)["outcome"]["code"] == "notauthorized"


def test_a_scene_kiosk_keeps_the_parameter_text(coordinator, event):
    awards = scene_id(event, "Awards")
    reply = act(
        coordinator,
        "scene.setkiosk",
        sceneid=awards,
        kiosk_name="Main",
        page="kiosks/award-presentations.kiosk",
        params="{confetti:false}",
    )

    assert reply["outcome"]["summary"] == "success"
    [awards_scene] = [s for s in reply["all-scenes"] if s["sceneid"] == awards]
    assert awards_scene["kiosks"][0]["parameters"] == "{confetti:false}"
    assert scene_list(coordinator)["scenes"][4]["kiosks"][0]["parameters"] == (
        "{confetti:false}"
    )


def test_setkiosk_with_no_page_removes_the_kiosk_from_the_scene(coordinator, event):
    act(
        coordinator,
        "scene.setkiosk",
        sceneid=scene_id(event, "Opening"),
        kiosk_name="Aux1",
    )

    kiosks = scene_list(coordinator)["scenes"][1]["kiosks"]

    assert [k["kiosk_name"] for k in kiosks] == ["Aux2", "Main"]


def test_a_new_scene_goes_last_and_a_name_is_used_once(coordinator, event):
    reply = act(coordinator, "scene.add", name="Lunch")

    assert reply["outcome"]["summary"] == "success"
    lunch = Scene.objects.get(event=event, name="Lunch")
    assert reply["scene-id"] == lunch.pk
    assert lunch.sort_order == 7
    duplicate = act(coordinator, "scene.add", name="Lunch")
    assert duplicate["outcome"]["code"] == "duplicate-name"


def test_deleting_a_scene_clears_what_names_it(coordinator, event):
    racing = Scene.objects.get(event=event, name="Racing")
    RaceSettings.objects.filter(event=event).update(racing_scene=racing)
    step = PlaylistEntryFactory(group__event=event, scene_at_finish=racing)
    act(coordinator, "scene.apply", sceneid=racing.pk)

    reply = act(coordinator, "scene.delete", sceneid=racing.pk)

    assert reply["outcome"]["summary"] == "success"
    assert not Scene.objects.filter(pk=racing.pk).exists()
    step.refresh_from_db()
    assert step.scene_at_finish is None
    assert RaceSettings.objects.get(event=event).racing_scene is None
    assert RaceState.objects.get(event=event).current_scene is None


def test_applying_a_scene_moves_named_kiosks_and_reports_it(coordinator, event):
    """C19: PHP writes current_scene but reads current-scene, so its
    dashboard never sees the scene."""
    poll(coordinator, "K1")
    act(coordinator, "kiosk.assign", address="K1", name="Main")
    racing = scene_id(event, "Racing")

    reply = act(coordinator, "scene.apply", sceneid=racing)

    assert reply["outcome"]["summary"] == "success"
    assert reply["current-scene"] == racing
    assert poll(coordinator, "K1")["page"] == "kiosks/now-racing.kiosk"
    dashboard = json.loads(
        coordinator.get("/action.php", {"query": "poll.kiosk.all"}).content
    )
    assert dashboard["current-scene"] == racing


def test_a_scene_can_be_applied_by_name(coordinator, event):
    reply = act(coordinator, "scene.apply", name="Awards")

    assert reply["current-scene"] == scene_id(event, "Awards")


def test_a_page_assigned_by_hand_means_no_scene_is_current(coordinator, event):
    act(coordinator, "scene.apply", name="Awards")
    poll(coordinator, "K1")

    reply = act(coordinator, "kiosk.assign", address="K1", page="kiosks/flag.kiosk")

    assert reply["current-scene"] == ""


def test_apply_with_no_scene_fails(coordinator):
    assert act(coordinator, "scene.apply")["outcome"]["code"] == "no-scene"


def test_a_negative_id_applies_no_scene(coordinator, event):
    act(coordinator, "scene.apply", name="Awards")

    assert act(coordinator, "scene.apply", sceneid=-1)["current-scene"] == ""


def test_the_race_crew_cannot_edit_scenes(client, event):
    log_in(client, Perm.CONTROL_RACE)

    reply = act(client, "scene.add", name="Lunch")

    assert reply["outcome"]["code"] == "notauthorized"


def test_another_events_scene_is_neither_applied_nor_deleted(coordinator, event):
    other = EventFactory()
    theirs = Scene.objects.filter(event=other).first()

    assert act(coordinator, "scene.apply", sceneid=theirs.pk)["current-scene"] == ""
    act(coordinator, "scene.delete", sceneid=theirs.pk)
    assert Scene.objects.filter(pk=theirs.pk).exists()
