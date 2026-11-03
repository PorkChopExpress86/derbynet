"""The playlist page's services (#44): queue, change, remove with Undo and
reorder a step, each rule checked under the `RaceState` lock.

The parity test makes the same steps through the page's services and
through the legacy `playlist.*` endpoints, the port of `website/ajax/
action.playlist.*.inc` that #34 compared with PHP, posting the fields
`website/js/playlist.js` posts, and finds the same `Playlist` rows.
"""

import json
from urllib.parse import urlencode

import pytest

from core.models import RaceState
from kiosks.models import Scene
from legacy_api.tests.conftest import log_in, post
from racing.models import PlaylistEntry, Round
from racing.services import playlist
from racing.services.playlist import (
    NEXT_ROUND,
    STOP,
    Failure,
    PlaylistFailure,
    StepChoice,
)
from racing.services.rounds import write_roster
from racing.tests.factories import PlaylistEntryFactory
from roster.tests.factories import GroupConstituentFactory, GroupFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def event(db):
    from core.tests.factories import EventFactory

    return EventFactory()


def rows(event):
    """The `Playlist` rows by seq, ids left out."""
    return list(
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list(
            "seq",
            "group__name",
            "round_number",
            "bucket_limit",
            "bucketed",
            "n_times_per_lane",
            "scene_at_finish__name",
            "continue_racing",
        )
    )


def refused(code, call, *args):
    with pytest.raises(PlaylistFailure) as caught:
        call(*args)
    assert caught.value.code == code
    return caught.value


# ----- the same rows as PHP's endpoints -------------------------------------------


def _legacy_steps(client, event, wolves, bears, awards):
    def act(name, **fields):
        reply = json.loads(post(client, urlencode({"action": name, **fields})).content)
        assert reply["outcome"]["summary"] == "success", reply
        return reply

    # `add_round_to_queue()`: "And then" is a scene, the next round, or stop.
    act(
        "playlist.add",
        classid=wolves.pk,
        round=1,
        top=0,
        bucketed=0,
        n_times_per_lane=1,
        continue_racing=1,
        sceneid_at_finish=awards.pk,
    )
    act(
        "playlist.add",
        classid=bears.pk,
        round=1,
        top=0,
        bucketed=0,
        n_times_per_lane=2,
        continue_racing=1,
        sceneid_at_finish=-1,
    )
    final = act(
        "playlist.add",
        classid=wolves.pk,
        round=2,
        top=4,
        bucketed=1,
        n_times_per_lane=1,
        continue_racing=0,
        sceneid_at_finish=0,
    )["queue-entry"]["queueid"]
    # `on_playlist_entry_update()` posts every field.
    act(
        "playlist.edit",
        queueid=final,
        top=3,
        bucketed=0,
        n_times_per_lane=2,
        sceneid_at_finish=-1,
        continue_racing=1,
    )
    first, second, third = (
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list("pk", flat=True)
    )
    act("playlist.order", queueid_1=third, queueid_2=first, queueid_3=second)
    act("playlist.delete", queueid=second)


def _page_steps(event, wolves, bears, awards):
    playlist.queue_step(event, wolves.pk, 1, StepChoice(runs=1, then=awards.pk))
    playlist.queue_step(event, bears.pk, 1, StepChoice(runs=2, then=NEXT_ROUND))
    final = playlist.queue_step(
        event, wolves.pk, 2, StepChoice(top=4, bucketed=True, runs=1, then=STOP)
    )
    playlist.change_step(
        event, final.pk, StepChoice(top=3, bucketed=False, runs=2, then=NEXT_ROUND)
    )
    first, second, third = (
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list("pk", flat=True)
    )
    playlist.reorder_steps(event, [third, first, second])
    playlist.remove_step(event, second)


def test_the_page_makes_the_same_rows_as_phps_endpoints(client, event):
    from core.tests.factories import EventFactory

    def setup(e):
        wolves = GroupFactory(event=e, name="Wolves")
        bears = GroupFactory(event=e, name="Bears")
        return wolves, bears, Scene.objects.get(event=e, name="Awards")

    _page_steps(event, *setup(event))
    page_rows = rows(event)

    other = EventFactory()
    from core.models import SiteState

    SiteState.objects.update_or_create(pk=1, defaults={"active_event": other})
    log_in(client, -1, role="RaceCoordinator")
    _legacy_steps(client, other, *setup(other))

    assert page_rows == rows(other)
    assert page_rows == [
        (1, "Wolves", 2, 3, False, 2, None, True),
        (2, "Wolves", 1, 0, False, 1, "Awards", True),
    ]


# ----- queueing a step -------------------------------------------------------------


def test_a_final_needs_its_top_n(event):
    """D43: a final asks how many racers go to it, and keeps the number."""
    wolves = GroupFactory(event=event)

    refused(Failure.NEEDS_TOP, playlist.queue_step, event, wolves.pk, 2, StepChoice())
    step = playlist.queue_step(event, wolves.pk, 2, StepChoice(top=4))

    assert (step.bucket_limit, step.round_number) == (4, 2)


def test_an_aggregates_first_round_needs_its_top_n(event):
    wolves, bears = GroupFactory(event=event), GroupFactory(event=event)
    both = GroupFactory(event=event, name="Both")
    for g in (wolves, bears):
        GroupConstituentFactory(event=event, aggregate=both, constituent=g)

    refused(Failure.NEEDS_TOP, playlist.queue_step, event, both.pk, 1, StepChoice())
    assert playlist.queue_step(event, both.pk, 1, StepChoice(top=8)).bucket_limit == 8


def test_a_round_with_its_roster_built_needs_no_top_n(event):
    wolves = GroupFactory(event=event)
    round = Round.objects.create(event=event, group=wolves, number=2)
    from roster.tests.factories import RacerFactory

    write_roster(round, [RacerFactory(event=event, group=wolves)])

    step = playlist.queue_step(event, wolves.pk, 2, StepChoice())

    assert step.bucket_limit == 0


def test_and_then_is_stop_the_next_round_or_a_scene(event):
    wolves = GroupFactory(event=event)
    awards = Scene.objects.get(event=event, name="Awards")

    for then in (STOP, NEXT_ROUND, awards.pk):
        playlist.queue_step(event, wolves.pk, 1, StepChoice(then=then))

    assert [(r[6], r[7]) for r in rows(event)] == [
        (None, False),
        (None, True),
        ("Awards", True),
    ]


@pytest.mark.parametrize("runs", [0, 7, -1])
def test_runs_per_lane_is_1_to_6(event, runs):
    wolves = GroupFactory(event=event)
    choice = StepChoice(runs=runs)
    refused(Failure.BAD_RUNS, playlist.queue_step, event, wolves.pk, 1, choice)
    assert not PlaylistEntry.objects.exists()


def test_a_group_or_scene_of_another_event_is_refused(event):
    theirs = GroupFactory()
    mine = GroupFactory(event=event)
    their_scene = Scene.objects.filter(event=theirs.event).first()

    choice = StepChoice()
    refused(Failure.NO_SUCH_GROUP, playlist.queue_step, event, theirs.pk, 1, choice)
    refused(
        Failure.NO_SUCH_SCENE,
        playlist.queue_step,
        event,
        mine.pk,
        1,
        StepChoice(then=their_scene.pk),
    )
    assert not PlaylistEntry.objects.exists()


def test_a_queued_step_goes_last_and_keeps_the_wait_switch(event):
    wolves = GroupFactory(event=event)
    PlaylistEntryFactory(group=wolves, seq=5)

    step = playlist.queue_step(event, wolves.pk, 1, StepChoice(waits=False))

    assert (step.seq, step.waits_for_operator) == (6, False)


# ----- changing a step -------------------------------------------------------------


def test_a_change_with_no_top_keeps_the_stored_cut(event):
    wolves = GroupFactory(event=event)
    step = playlist.queue_step(event, wolves.pk, 2, StepChoice(top=4, bucketed=True))

    playlist.change_step(event, step.pk, StepChoice(runs=3, then=NEXT_ROUND))

    step.refresh_from_db()
    assert (step.bucket_limit, step.bucketed, step.n_times_per_lane) == (4, True, 3)
    assert step.continue_racing


def test_a_change_cannot_clear_a_finals_top_n(event):
    wolves = GroupFactory(event=event)
    step = playlist.queue_step(event, wolves.pk, 2, StepChoice(top=4))

    refused(Failure.NEEDS_TOP, playlist.change_step, event, step.pk, StepChoice(top=0))
    step.refresh_from_db()
    assert step.bucket_limit == 4


def test_a_step_of_another_event_or_gone_is_refused(event):
    theirs = PlaylistEntryFactory()

    refused(Failure.NO_SUCH_STEP, playlist.change_step, event, theirs.pk, StepChoice())
    refused(Failure.NO_SUCH_STEP, playlist.set_step_waits, event, theirs.pk, False)
    refused(Failure.NO_SUCH_STEP, playlist.remove_step, event, theirs.pk)
    assert PlaylistEntry.objects.filter(pk=theirs.pk).exists()


def test_the_wait_switch(event):
    step = PlaylistEntryFactory(group=GroupFactory(event=event))

    playlist.set_step_waits(event, step.pk, False)
    step.refresh_from_db()
    assert not step.waits_for_operator


# ----- removing, Undo and reordering -----------------------------------------------


def test_undo_puts_a_removed_step_back_where_it_was(event):
    wolves = GroupFactory(event=event)
    awards = Scene.objects.get(event=event, name="Awards")
    for n in (1, 2, 3):
        playlist.queue_step(
            event,
            wolves.pk,
            n,
            StepChoice(top=n, runs=n, then=awards.pk if n == 2 else STOP),
        )
    before = rows(event)
    middle = PlaylistEntry.objects.get(event=event, round_number=2)

    removed = playlist.remove_step(event, middle.pk)
    assert [r[2] for r in rows(event)] == [1, 3]
    playlist.restore_step(event, removed)

    assert rows(event) == before


def test_removing_the_waiting_step_ends_the_intermission(event):
    step = PlaylistEntryFactory(group=GroupFactory(event=event))
    RaceState.objects.filter(event=event).update(waiting_entry=step)

    playlist.remove_step(event, step.pk)

    assert RaceState.objects.get(event=event).waiting_entry is None


def test_a_stale_order_is_refused(event):
    wolves = GroupFactory(event=event)
    first, second = (PlaylistEntryFactory(group=wolves, seq=s) for s in (1, 2))

    refused(Failure.STALE_ORDER, playlist.reorder_steps, event, [second.pk])
    playlist.reorder_steps(event, [second.pk, first.pk])

    assert [r[0] for r in rows(event)] == [1, 2]
    assert list(
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list("pk", flat=True)
    ) == [second.pk, first.pk]


def test_restore_checks_the_step_as_a_new_one(event):
    """The #44 review: a restore stored runs 0 from a forged Undo link."""
    from racing.services.playlist import RemovedStep

    wolves = GroupFactory(event=event)
    forged = RemovedStep(wolves.pk, 1, 1, StepChoice(runs=0))
    refused(Failure.BAD_RUNS, playlist.restore_step, event, forged)
    final = RemovedStep(wolves.pk, 2, 1, StepChoice(top=0))
    refused(Failure.NEEDS_TOP, playlist.restore_step, event, final)
    assert not PlaylistEntry.objects.exists()


def test_a_second_restore_changes_nothing(event):
    wolves = GroupFactory(event=event)
    step = playlist.queue_step(event, wolves.pk, 1, StepChoice())
    removed = playlist.remove_step(event, step.pk)

    assert playlist.restore_step(event, removed) is not None
    assert playlist.restore_step(event, removed) is None
    assert len(rows(event)) == 1


def test_an_undo_query_round_trips():
    from racing.services.playlist import RemovedStep

    removed = RemovedStep(3, 2, 1, StepChoice(top=4, bucketed=True, runs=2, then=-1))
    query = {k: str(v) for k, v in removed.to_query().items()}
    assert RemovedStep.from_query(query) == removed
    assert RemovedStep.from_query({**query, "runs": "x"}) is None
    assert RemovedStep.from_query({}) is None
