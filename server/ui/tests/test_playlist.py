"""The rounds playlist page's views (#44): the guard, the redirect and the
back button, the racing scene, the entry sheet, the wait switch (D12),
removing with Undo, reordering, starting, and the scheduling partial with
its method and lanes."""

import re

import pytest

from core import race_settings
from core.models import RaceSettings, RaceState
from kiosks.models import Scene
from racing.models import PlaylistEntry
from racing.services import lanes
from racing.tests.factories import PlaylistEntryFactory
from roster.models import Group
from ui.tests.db import no_db_writes
from ui.tests.race import race_setup, run_heat, scheduled
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


def post(client, url, data=None, target="toasts"):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target)


def sheet_post(client, url, data=None):
    return post(client, url, data, target="playlist-sheet-body")


def get(client, url, data=None):
    return client.get(url, data or {}, HTTP_HX_REQUEST="true")


def html(response) -> str:
    return response.content.decode()


def queue(response) -> str:
    """The re-rendered queue a change sends out of band."""
    found = re.search(
        r'<div id="playlist-queue" hx-swap-oob="innerHTML">(.*)</div>\s*$',
        html(response),
        re.S,
    )
    assert found, html(response)[:500]
    return found.group(1)


def steps(event):
    return list(
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list(
            "group__name",
            "round_number",
            "bucket_limit",
            "n_times_per_lane",
            "continue_racing",
            "waits_for_operator",
        )
    )


@pytest.fixture
def race_day(event):
    """Scouts and Outlaws, round 1 of each made, nothing queued."""
    return race_setup(event, groups=(("Scouts", 6), ("Outlaws", 6)))


def group_id(event, name):
    return Group.objects.get(event=event, name=name).pk


def add(client, event, name, number, **fields):
    data = {
        "group": group_id(event, name),
        "round": number,
        "runs": 1,
        "then": 0,
        "waits": "1",
        **fields,
    }
    return sheet_post(client, "/playlist/add/", data)


# ----- the page --------------------------------------------------------------------


def test_the_page_needs_set_up(login, event):
    assert login("RaceCoordinator").get("/playlist/").status_code == 200
    assert login("RaceCrew").get("/playlist/").status_code == 302
    assert login("anonymous").get("/playlist/").status_code == 302


def test_playlist_php_moves_to_the_page_with_its_query(client, event):
    response = client.get("/playlist.php?back=setup.php")
    assert response.status_code == 301
    assert response["Location"] == "/playlist/?back=setup.php"


@pytest.mark.parametrize(
    ("back", "url"),
    [
        ("setup.php", "/setup/"),
        ("coordinator.php", "/dashboard/"),
        ("/groups/", "/groups/"),
        ("https://example.com/", "/dashboard/"),
        ("//example.com/", "/dashboard/"),
        ("", "/dashboard/"),
    ],
)
def test_the_back_button_returns_to_the_calling_page(coordinator, back, url):
    page = html(coordinator.get("/playlist/", {"back": back}))
    assert f'<a href="{url}" aria-label="Back"' in page


def test_the_page_reads_and_writes_nothing_on_get(coordinator, race_day):
    with no_session_writes(), no_db_writes():
        assert coordinator.get("/playlist/").status_code == 200


def test_the_racing_scene_and_its_notice(coordinator, event):
    page = html(coordinator.get("/playlist/"))
    assert "data-no-scene" in page

    racing = Scene.objects.get(event=event, name="Racing")
    response = post(coordinator, "/playlist/scene/", {"racing_scene": racing.pk})

    assert RaceSettings.objects.get(event=event).racing_scene == racing
    assert "data-no-scene" not in queue(response)
    post(coordinator, "/playlist/scene/", {"racing_scene": "-1"})
    assert RaceSettings.objects.get(event=event).racing_scene is None


def test_a_scene_of_another_event_is_refused(coordinator, event):
    from core.tests.factories import EventFactory

    theirs = Scene.objects.filter(event=EventFactory()).first()
    response = post(coordinator, "/playlist/scene/", {"racing_scene": theirs.pk})
    assert response.status_code == 422
    assert RaceSettings.objects.get(event=event).racing_scene is None


def test_the_master_schedule_notice(coordinator, event):
    assert "data-master-notice" not in html(coordinator.get("/playlist/"))
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    assert "data-master-notice" in html(coordinator.get("/playlist/"))


# ----- the entry sheet -------------------------------------------------------------


def test_the_whole_race_day_can_be_queued_in_one_pass(coordinator, event, race_day):
    """`build_rounds()`: each group's next round is offered while the rounds
    before it are made, queued or raced, so both finals can be queued at
    once, and a queued round leaves the list."""
    scouts, outlaws = group_id(event, "Scouts"), group_id(event, "Outlaws")
    page = html(coordinator.get("/playlist/"))
    for g in (scouts, outlaws):
        assert f'data-add-round="{g}-1"' in page
        assert f'data-add-round="{g}-2"' in page
    assert f'data-add-round="{scouts}-3"' not in page

    add(coordinator, event, "Scouts", 1)
    reply = queue(add(coordinator, event, "Scouts", 2, cut="1", top="4"))

    assert f'data-add-round="{scouts}-1"' not in reply
    assert f'data-add-round="{scouts}-2"' not in reply
    assert f'data-add-round="{scouts}-3"' in reply


def test_a_finals_sheet_asks_for_its_top_n(coordinator, event, race_day):
    add(coordinator, event, "Scouts", 1)
    sheet = html(
        get(
            coordinator,
            "/playlist/add/",
            {"group": group_id(event, "Scouts"), "round": 2},
        )
    )
    assert 'name="top"' in sheet and "required" in sheet
    assert 'name="top" type="number" inputmode="numeric" min="1" required value=""' in (
        sheet
    )

    refused = html(add(coordinator, event, "Scouts", 2, cut="1", top=""))
    assert "data-error" in refused and "how many racers" in refused
    assert len(steps(event)) == 1

    add(coordinator, event, "Scouts", 2, cut="1", top="4")
    assert steps(event)[1] == ("Scouts", 2, 4, 1, False, True)


def test_a_step_waits_for_the_operator_by_default_after_the_first(
    coordinator, event, race_day
):
    """D12: the sheet's switch starts off for the first step, on after."""
    scouts = group_id(event, "Scouts")
    first = html(get(coordinator, "/playlist/add/", {"group": scouts, "round": 1}))
    assert re.search(r'name="waits" value="1" data-waits', first)

    add(coordinator, event, "Scouts", 1, waits="")
    outlaws = group_id(event, "Outlaws")
    second = html(get(coordinator, "/playlist/add/", {"group": outlaws, "round": 1}))
    assert re.search(r'name="waits" value="1" checked data-waits', second)


def test_adding_closes_the_sheet_and_shows_the_card(coordinator, event, race_day):
    response = add(coordinator, event, "Scouts", 1, then="-1", runs="2")

    assert 'x-init="$store.ui.close()"' in html(response)
    card = queue(response)
    assert "Scouts, Round 1" in card and "Start next round" in card
    assert steps(event) == [("Scouts", 1, 0, 2, True, True)]


def test_editing_every_field(coordinator, event, race_day):
    add(coordinator, event, "Scouts", 1)
    add(coordinator, event, "Scouts", 2, cut="1", top="4")
    final = PlaylistEntry.objects.get(event=event, round_number=2)
    awards = Scene.objects.get(event=event, name="Awards")

    sheet = html(get(coordinator, f"/playlist/step/{final.pk}/"))
    assert 'value="4"' in sheet

    sheet_post(
        coordinator,
        f"/playlist/step/{final.pk}/",
        {"cut": "1", "top": "8", "runs": "3", "then": str(awards.pk), "waits": ""},
    )
    final.refresh_from_db()
    assert (
        final.bucket_limit,
        final.n_times_per_lane,
        final.scene_at_finish,
        final.continue_racing,
        final.waits_for_operator,
    ) == (8, 3, awards, True, False)


def test_a_bad_runs_value_shows_in_the_sheet(coordinator, event, race_day):
    response = add(coordinator, event, "Scouts", 1, runs="9")
    assert "data-error" in html(response)
    assert steps(event) == []


def test_the_wait_switch_on_a_card(coordinator, event, race_day):
    add(coordinator, event, "Scouts", 1)
    step = PlaylistEntry.objects.get(event=event)

    post(coordinator, f"/playlist/step/{step.pk}/waits/", {"waits": "0"})
    step.refresh_from_db()
    assert not step.waits_for_operator


def test_a_waiting_step_shows_the_intermission(coordinator, event, race_day):
    add(coordinator, event, "Scouts", 1)
    step = PlaylistEntry.objects.get(event=event)
    RaceState.objects.filter(event=event).update(waiting_entry=step)

    page = html(coordinator.get("/playlist/"))
    assert "data-waiting-state" in page and "Start this round" in page


# ----- remove, Undo, reorder -------------------------------------------------------


def test_remove_then_undo(coordinator, event, race_day):
    for name in ("Scouts", "Outlaws"):
        add(coordinator, event, name, 1)
    add(coordinator, event, "Scouts", 2, cut="1", top="4")
    before = steps(event)
    middle = PlaylistEntry.objects.get(event=event, group__name="Outlaws")

    response = post(coordinator, f"/playlist/step/{middle.pk}/remove/")
    assert len(steps(event)) == 2
    undo = re.search(r'hx-post="(/playlist/restore/\?[^"]+)"', html(response))
    assert undo, html(response)[:500]

    post(coordinator, undo.group(1).replace("&amp;", "&"))
    assert steps(event) == before


def test_reorder(coordinator, event, race_day):
    for name in ("Scouts", "Outlaws"):
        add(coordinator, event, name, 1)
    ids = list(
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list("pk", flat=True)
    )

    post(coordinator, "/playlist/order/", {"order": f"{ids[1]},{ids[0]}"})
    assert [s[0] for s in steps(event)] == ["Outlaws", "Scouts"]

    stale = post(coordinator, "/playlist/order/", {"order": str(ids[0])})
    assert stale.status_code == 422
    assert [s[0] for s in steps(event)] == ["Outlaws", "Scouts"]


# ----- racing ----------------------------------------------------------------------


def test_start_playlist_races_the_first_step(coordinator, event, race_day):
    add(coordinator, event, "Scouts", 1)

    post(coordinator, "/playlist/start/")

    state = RaceState.objects.get(event=event)
    assert state.racing and state.current_round == race_day["Scouts"]


def test_starting_needs_control_race(login, event, race_day):
    from accounts.models import Role
    from accounts.permissions import Perm

    PlaylistEntryFactory(group=race_day["Scouts"].group)
    Role.objects.filter(name="RaceCoordinator").update(permissions=Perm.SET_UP.value)
    client = login("RaceCoordinator")
    assert post(client, "/playlist/start/").status_code == 403
    assert not RaceState.objects.get(event=event).racing


# ----- the scheduling partial ------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "max_runs", "rotation"),
    [("abbreviated", "1", "0"), ("rotation", "0", "1"), ("normal", "0", "0")],
)
def test_each_method_writes_phps_settings(
    coordinator, event, method, max_runs, rotation
):
    """`on_schedule_method_change()` in `website/js/settings.js`."""
    reply = html(post(coordinator, "/playlist/scheduling/method/", {"method": method}))

    assert race_settings.read(event, "max-runs-per-car") == max_runs
    assert race_settings.read(event, "rotation-schedule") == rotation
    assert f'data-method="{method}"' in reply


def test_lanes_in_use(coordinator, event):
    RaceSettings.objects.filter(event=event).update(lane_count=4)

    reply = html(
        post(
            coordinator,
            "/playlist/scheduling/lanes/",
            {"lanes": "4", "used": ["1", "2", "4"]},
        )
    )

    assert RaceSettings.objects.get(event=event).unused_lane_mask == 0b0100
    assert 'data-usable="3"' in reply
    assert "Lane 3 is set aside as unused." in reply


def test_a_lane_change_with_unraced_heats_is_refused(coordinator, event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    run_heat(event)

    reply = html(
        post(coordinator, "/playlist/scheduling/lanes/", {"lanes": "4", "used": ["1"]})
    )

    assert RaceSettings.objects.get(event=event).unused_lane_mask == 0
    assert 'role="alert"' in reply
    assert "Remove unraced schedules before changing lane counts." in reply


def test_closed_and_unused_lanes_are_told_apart(coordinator, event):
    """D36, D45: with lane 2 closed and lane 4 unused, two lanes are usable
    on a 4-lane track, and the partial says why."""
    RaceSettings.objects.filter(event=event).update(
        lane_count=4, unused_lane_mask=0b1000
    )
    lanes.close_lanes(event, [2])

    reply = html(get(coordinator, "/playlist/scheduling/"))

    assert 'data-usable="2"' in reply
    assert "2 of 4 lanes usable." in reply
    assert "Lane 2 is closed as damaged" in reply
    assert "Lane 4 is set aside as unused." in reply
    assert 'data-lane="2" data-lane-state="closed"' in reply
    assert 'data-lane="4" data-lane-state="unused"' in reply


def test_the_dashboards_schedule_sheet_opens_the_partial(coordinator, event):
    wolves = race_setup(event)["Wolves"]
    sheet = html(get(coordinator, f"/dashboard/round/{wolves.pk}/sheet/schedule/"))
    assert 'hx-get="/playlist/scheduling/"' in sheet


def _undo_url(response) -> str:
    found = re.search(r'hx-post="(/playlist/restore/\?[^"]+)"', html(response))
    assert found, html(response)[:500]
    return found.group(1).replace("&amp;", "&")


def test_a_second_undo_restores_nothing(coordinator, event, race_day):
    """The #44 review: a double tap on Undo put the step back twice."""
    for name in ("Scouts", "Outlaws"):
        add(coordinator, event, name, 1)
    first = PlaylistEntry.objects.get(event=event, group__name="Scouts")
    undo = _undo_url(post(coordinator, f"/playlist/step/{first.pk}/remove/"))

    post(coordinator, undo)
    post(coordinator, undo)

    assert [s[0] for s in steps(event)] == ["Scouts", "Outlaws"]


def test_a_forged_undo_stores_nothing(coordinator, event, race_day):
    """The #44 review: `runs=0` in a hand-made Undo link was stored."""
    add(coordinator, event, "Scouts", 1)
    step = PlaylistEntry.objects.get(event=event)
    undo = _undo_url(post(coordinator, f"/playlist/step/{step.pk}/remove/"))
    forged = re.sub(r"runs=\d+", "runs=0", undo)
    if forged == undo:
        forged = undo + "&runs=0"

    response = post(coordinator, forged)

    assert response.status_code == 422
    assert steps(event) == []


def test_the_queue_reads_no_more_for_more_steps(event):
    """The #44 review: `state.build` read the cut and the allowed racers
    once per step."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from ui.playlist import state

    rounds = race_setup(event, groups=(("Scouts", 4), ("Outlaws", 4)))

    def reads(n_steps):
        PlaylistEntry.objects.filter(event=event).delete()
        for seq in range(1, n_steps + 1):
            group = rounds["Scouts" if seq % 2 else "Outlaws"].group
            PlaylistEntryFactory(group=group, seq=seq, round_number=(seq + 1) // 2)
        with CaptureQueriesContext(connection) as seen:
            state.build(event)
        return len(seen)

    assert reads(6) == reads(2)
