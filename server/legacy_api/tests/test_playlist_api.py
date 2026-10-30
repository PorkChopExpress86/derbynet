"""The playlist endpoints (#34): `playlist.add`, `playlist.edit`,
`playlist.delete`, `playlist.order` and `playlist.start`, with S18's
permission and the delayed scene switch called from the polls."""

import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.utils import timezone

from accounts.permissions import Perm
from core.models import RaceState
from kiosks.models import Kiosk, Scene
from legacy_api.tests.conftest import log_in, post
from racing.models import PlaylistEntry
from roster.tests.factories import GroupFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def act(client, action_name, **fields):
    body = urlencode({"action": action_name, **fields})
    return json.loads(post(client, body).content)


def test_a_step_lists_with_phps_fields(coordinator, event):
    den = GroupFactory(event=event, name="Den1")
    GroupFactory(event=event, name="Den2")
    awards = Scene.objects.get(event=event, name="Awards")

    first = act(
        coordinator,
        "playlist.add",
        classid=den.pk,
        round=1,
        sceneid_at_finish=awards.pk,
    )
    second = act(
        coordinator, "playlist.add", classid=den.pk, round=2, top=3, bucketed=1
    )

    assert first["outcome"]["summary"] == "success"
    step = PlaylistEntry.objects.get(event=event, seq=1)
    assert first["queue-entry"] == {
        "queueid": step.pk,
        "seq": 1,
        "classid": den.pk,
        "round": 1,
        "bucket_limit": 0,
        "bucketed": 0,
        "n_times_per_lane": -1,
        "sceneid_at_finish": awards.pk,
        "continue_racing": 0,
        "class": "Den1",
        "roundname": "Den1, Round 1",
    }
    assert second["queue-entry"]["seq"] == 2
    assert second["queue-entry"]["bucket_limit"] == 3
    assert second["queue-entry"]["sceneid_at_finish"] == -1


def test_a_step_added_through_the_legacy_api_does_not_wait(coordinator, event):
    """PHP has no operator gate (D12), so its clients' steps run at once."""
    den = GroupFactory(event=event)

    act(coordinator, "playlist.add", classid=den.pk, round=1)

    assert PlaylistEntry.objects.get(event=event).waits_for_operator is False


def test_add_needs_a_class_and_a_round(coordinator, event):
    assert act(coordinator, "playlist.add", round=1)["outcome"]["code"] == "no_classid"
    den = GroupFactory(event=event)
    assert act(coordinator, "playlist.add", classid=den.pk)["outcome"]["code"] == (
        "no_round"
    )
    assert not PlaylistEntry.objects.exists()


def test_edit_changes_the_cut_the_runs_and_what_follows(coordinator, event):
    den = GroupFactory(event=event)
    queueid = act(coordinator, "playlist.add", classid=den.pk, round=1)["queue-entry"][
        "queueid"
    ]
    awards = Scene.objects.get(event=event, name="Awards")

    reply = act(
        coordinator,
        "playlist.edit",
        queueid=queueid,
        top=4,
        bucketed=1,
        n_times_per_lane=2,
        sceneid_at_finish=awards.pk,
        continue_racing=0,
    )

    assert reply["outcome"]["summary"] == "success"
    step = PlaylistEntry.objects.get(pk=queueid)
    assert (step.bucket_limit, step.bucketed, step.n_times_per_lane) == (4, True, 2)
    assert (step.scene_at_finish, step.continue_racing) == (awards, False)


def test_order_and_delete(coordinator, event):
    den = GroupFactory(event=event)
    ids = [
        act(coordinator, "playlist.add", classid=den.pk, round=n)["queue-entry"][
            "queueid"
        ]
        for n in (1, 2, 3)
    ]

    act(
        coordinator,
        "playlist.order",
        queueid_1=ids[2],
        queueid_2=ids[0],
        queueid_3=ids[1],
    )
    act(coordinator, "playlist.delete", queueid=ids[0])

    steps = PlaylistEntry.objects.filter(event=event).order_by("seq")
    assert [(s.pk, s.seq) for s in steps] == [(ids[2], 1), (ids[1], 3)]


def test_a_stale_order_onto_a_taken_position_moves_the_other_step_after(
    coordinator, event
):
    """A tab that missed a delete and an add can name a position another
    step holds. PHP stores both at that position; Django keeps positions
    unique, so the step it does not move goes after the others."""
    den = GroupFactory(event=event)
    ids = [
        act(coordinator, "playlist.add", classid=den.pk, round=n)["queue-entry"][
            "queueid"
        ]
        for n in (1, 2, 3)
    ]

    reply = act(coordinator, "playlist.order", queueid_1=ids[2])

    assert reply["outcome"]["summary"] == "success"
    steps = PlaylistEntry.objects.filter(event=event).order_by("seq")
    assert [s.pk for s in steps] == [ids[2], ids[1], ids[0]]


def test_the_race_crew_cannot_edit_the_playlist(client, event):
    log_in(client, Perm.CONTROL_RACE)
    den = GroupFactory(event=event)

    reply = act(client, "playlist.add", classid=den.pk, round=1)

    assert reply["outcome"]["code"] == "notauthorized"


def test_starting_the_playlist_needs_control_race(client, event):
    """S18: PHP lets anyone start the playlist and turn racing on."""
    reply = act(client, "playlist.start")

    assert reply["outcome"]["code"] == "notauthorized"


def _due_scene(event, name="Awards"):
    Kiosk.objects.create(
        event=event, address="K1", name="Main", page="kiosks/welcome.kiosk"
    )
    RaceState.objects.filter(event=event).update(
        playlist_scene=Scene.objects.get(event=event, name=name),
        playlist_scene_at=timezone.now() - timedelta(seconds=1),
    )


def test_the_coordinator_poll_alone_shows_the_finished_steps_scene(coordinator, event):
    """With no kiosk polling, the scene still changes 10 s after the round
    ends: the race dashboard polls every 2 s."""
    _due_scene(event)

    coordinator.get("/action.php", {"query": "poll.coordinator"})

    assert Kiosk.objects.get(address="K1").page == "kiosks/award-presentations.kiosk"
    state = RaceState.objects.get(event=event)
    assert state.current_scene.name == "Awards"
    assert state.playlist_scene_at is None


def test_the_kiosk_dashboard_poll_shows_the_finished_steps_scene(coordinator, event):
    _due_scene(event)

    reply = json.loads(
        coordinator.get("/action.php", {"query": "poll.kiosk.all"}).content
    )

    assert reply["current-scene"] == Scene.objects.get(event=event, name="Awards").pk


def test_a_kiosk_poll_shows_the_finished_steps_scene(client, event):
    _due_scene(event)

    reply = client.get("/action.php", {"query": "poll.kiosk", "address": "K1"})

    page = json.loads(reply.content)["kiosk-setting"]["page"]
    assert page == "kiosks/award-presentations.kiosk"
