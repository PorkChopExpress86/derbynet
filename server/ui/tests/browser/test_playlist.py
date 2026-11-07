"""The rounds playlist page in a browser (#44): the owner's race day (D11,
D12) at 390x844, reordering by the row menu, the lane refusal, the
playlist.php and settings.php parity boxes, and the screenshots."""

import re

import pytest
from playwright.sync_api import expect

from core import race_settings
from core.models import RaceSettings, RaceState
from kiosks.models import Scene
from racing.models import PlaylistEntry, Round
from racing.services import lanes
from racing.tests.factories import PlaylistEntryFactory
from ui.tests.browser.conftest import (
    assert_accessible,
    dismiss_toasts,
    htmx_post,
    shoot,
)
from ui.tests.race import car_times, race_setup, run_heat, scheduled


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check is at 390x844")


def open_playlist(page, query=""):
    page.goto(f"/playlist/{query}")
    expect(page.locator("#playlist-queue [data-section=queue]")).to_be_visible()
    return page


def sheet(page):
    return page.locator('[data-sheet="playlist"]')


def card(page, name):
    return page.locator("[data-queue] > li", has_text=name)


def queued(event):
    return list(
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list("group__name", "round_number")
    )


def add_round(page, name, *, top=None):
    """Tap a round in the list to add, fill the entry sheet, and add it."""
    dismiss_toasts(page)
    page.get_by_role("button", name=re.compile(f"^Add {name}")).click()
    form = sheet(page).locator("[data-entry-sheet]")
    expect(form).to_be_visible()
    if top is not None:
        form.get_by_label("How many racers go to this final?").fill(str(top))
    with htmx_post(page, "/playlist/add/"):
        form.get_by_role("button", name="Add to playlist").click()
    expect(sheet(page)).to_be_hidden()


def race_day(event):
    return race_setup(event, groups=(("Scouts", 8), ("Outlaws", 8)))


# ----- D11, D12: the owner's race day ----------------------------------------------


class Timer:
    """The legacy timer: `run_heat` in testing/common.sh posts STARTED and
    FINISHED to /action.php as the Timer role."""

    def __init__(self, playwright, live_server):
        self.api = playwright.request.new_context(base_url=live_server.url)
        reply = self.api.post(
            "/action.php",
            form={"action": "role.login", "name": "Timer", "password": ""},
        )
        assert reply.ok, reply.text()

    def message(self, message, **lanes):
        reply = self.api.post(
            "/action.php",
            form={"action": "timer-message", "message": message, **lanes},
        )
        assert "success" in reply.text(), reply.text()

    def race_round(self, event):
        """Every heat of the current round, a lower car always faster."""
        round_id = RaceState.objects.get(event=event).current_round_id
        heats = 0
        while True:
            state = RaceState.objects.get(event=event)
            if not state.racing or state.current_round_id != round_id:
                return heats
            self.message("STARTED")
            self.message("FINISHED", **dict(car_times(event)))
            heats += 1

    def close(self):
        self.api.dispose()


def test_the_owners_race_day(login, event, playwright, live_server, page_errors):
    """At 390x844: queue Scouts round 1, a Scouts top 4 final, Outlaws round
    1 and an Outlaws top 4 final on this page, start the playlist, finish
    each round through the legacy timer, and start each next round with
    one tap on the dashboard's intermission card, with no page load between
    rounds. No other operator step."""
    page_errors.allow(r"request failed: \S*/dashboard/live/\S* net::ERR_ABORTED$")
    page = login("RaceCoordinator")
    phone_only(page)
    race_day(event)
    open_playlist(page)

    add_round(page, "Scouts, Round 1")
    add_round(page, "Scouts, Round 2", top=4)
    add_round(page, "Outlaws, Round 1")
    add_round(page, "Outlaws, Round 2", top=4)
    assert queued(event) == [
        ("Scouts", 1),
        ("Scouts", 2),
        ("Outlaws", 1),
        ("Outlaws", 2),
    ]
    # D12: every step after the first waits for the operator.
    waits = list(
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list("waits_for_operator", flat=True)
    )
    assert waits == [False, True, True, True]
    expect(card(page, "Scouts, Round 2").locator("[data-top]")).to_have_text("Top 4")
    shoot(page, "playlist-race-day-queued")

    dismiss_toasts(page)
    with htmx_post(page, "/playlist/start/"):
        page.get_by_role("button", name="Start playlist").click()

    # The race coordinator races from the dashboard, which polls (#41);
    # this page loads once and does not (#44). From here on no page loads:
    # the timer ends each round, and one tap starts the next.
    page.goto("/dashboard/")
    expect(page.locator("#live")).to_be_visible()
    loads = []
    page.on("framenavigated", lambda frame: loads.append(frame.url))
    timer = Timer(playwright, live_server)
    try:
        expected = ["Scouts, Round 2", "Outlaws, Round 1", "Outlaws, Round 2"]
        for name in expected:
            assert timer.race_round(event) > 0
            state = RaceState.objects.get(event=event)
            assert not state.racing and state.waiting_entry is not None
            intermission = page.locator("[data-intermission]")
            expect(intermission).to_contain_text(f"Up next: {name}", timeout=5000)
            if name == expected[0]:
                shoot(page, "playlist-race-day-intermission")
            intermission.get_by_role("button", name="Start next round").click()
            expect(page.locator("[data-intermission]")).to_have_count(0)
            state = RaceState.objects.get(event=event)
            assert state.racing and state.waiting_entry is None
            round = Round.objects.select_related("group").get(pk=state.current_round_id)
            assert f"{round.group.name}, Round {round.number}" == name
            if round.number == 2:
                assert round.roster.count() == 4
        assert timer.race_round(event) > 0
    finally:
        timer.close()
    state = RaceState.objects.get(event=event)
    assert state.waiting_entry is None and not state.racing
    assert loads == []


def test_the_waiting_card_on_the_playlist_page(login, event):
    """D12: while the intermission waits on a step, its card says so and
    starts it with one tap."""
    from racing.services import playlist
    from racing.services.rounds import playlist_finalists

    rounds = race_day(event)
    PlaylistEntryFactory(group=rounds["Scouts"].group, seq=1, waits_for_operator=False)
    PlaylistEntryFactory(group=rounds["Outlaws"].group, seq=2)
    playlist.start_playlist(event, playlist_finalists())
    while RaceState.objects.get(event=event).racing:
        run_heat(event)
    assert RaceState.objects.get(event=event).waiting_entry is not None

    page = open_playlist(login("RaceCoordinator"))
    waiting = page.locator("[data-queue] > li[data-waiting]")
    expect(waiting).to_contain_text("Outlaws, Round 1")
    expect(waiting.locator('[data-state="warn"]')).to_have_text("Waiting")
    # Clear of the bottom nav, as the race coordinator scrolls to it.
    waiting.evaluate("e => e.scrollIntoView({block: 'center'})")
    shoot(page, "playlist-intermission", full_page=False)
    with htmx_post(page, "/playlist/start-next/"):
        waiting.get_by_role("button", name="Start this round").click()
    state = RaceState.objects.get(event=event)
    assert state.racing and state.current_round == rounds["Outlaws"]


# ----- reordering on a phone, with no drag ----------------------------------------


def test_a_phone_reorders_by_the_row_menu(login, event):
    page = login("RaceCoordinator")
    phone_only(page)
    rounds = race_setup(event, groups=(("Scouts", 4), ("Outlaws", 4), ("Lions", 4)))
    for seq, name in enumerate(("Scouts", "Outlaws", "Lions"), 1):
        PlaylistEntryFactory(group=rounds[name].group, seq=seq)
    open_playlist(page)

    page.get_by_role("button", name="Actions for Scouts, Round 1").click()
    with htmx_post(page, "/playlist/order/"):
        page.get_by_role("menuitem", name="Move down").locator("visible=true").click()
    assert [g for g, _ in queued(event)] == ["Outlaws", "Scouts", "Lions"]

    dismiss_toasts(page)
    page.get_by_role("button", name="Actions for Lions, Round 1").click()
    with htmx_post(page, "/playlist/order/"):
        page.get_by_role("menuitem", name="Move up").locator("visible=true").click()
    assert [g for g, _ in queued(event)] == ["Outlaws", "Lions", "Scouts"]
    expect(page.locator("[data-queue] > li").first).to_contain_text("Outlaws")


# ----- the scheduling partial ------------------------------------------------------


def open_scheduling(page):
    page.get_by_role("button", name="Scheduling method and lanes").click()
    partial = sheet(page).locator("[data-scheduling]")
    expect(partial).to_be_visible()
    return partial


def test_a_lane_change_with_unraced_heats_is_refused(login, event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    run_heat(event)
    page = open_playlist(login("RaceCoordinator"))
    partial = open_scheduling(page)
    expect(partial.locator("[data-locked]")).to_be_visible()

    lane = partial.get_by_role("switch", name=re.compile("Lane 3"))
    with htmx_post(page, "/playlist/scheduling/lanes/"):
        lane.click()

    alert = sheet(page).get_by_role("alert")
    expect(alert).to_have_text("Remove unraced schedules before changing lane counts.")
    expect(sheet(page).get_by_role("switch", name=re.compile("Lane 3"))).to_be_checked()
    assert RaceSettings.objects.get(event=event).unused_lane_mask == 0


def test_methods_and_lanes_in_use(login, event):
    """settings.php's scheduling method modal and lane images."""
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    page = open_playlist(login("RaceCoordinator"))
    partial = open_scheduling(page)
    for label, max_runs, rotation in (
        ("One run per car", "1", "0"),
        ("Lane rotation", "0", "1"),
        ("Normal", "0", "0"),
    ):
        with htmx_post(page, "/playlist/scheduling/method/"):
            sheet(page).get_by_text(label, exact=True).click()
        assert race_settings.read(event, "max-runs-per-car") == max_runs
        assert race_settings.read(event, "rotation-schedule") == rotation

    expect(partial.locator("[data-usable]")).to_contain_text("4 of 4 lanes usable.")
    with htmx_post(page, "/playlist/scheduling/lanes/"):
        sheet(page).get_by_role("switch", name=re.compile("Lane 4")).click()
    expect(sheet(page).locator("[data-usable]")).to_contain_text(
        "3 of 4 lanes usable. Lane 4 is set aside as unused."
    )
    assert RaceSettings.objects.get(event=event).unused_lane_mask == 0b1000
    with htmx_post(page, "/playlist/scheduling/lanes/"):
        sheet(page).get_by_role("switch", name=re.compile("Lane 4")).click()
    assert RaceSettings.objects.get(event=event).unused_lane_mask == 0


def test_closed_and_unused_lanes_on_a_4_lane_track(login, event):
    RaceSettings.objects.filter(event=event).update(
        lane_count=4, unused_lane_mask=0b1000
    )
    lanes.close_lanes(event, [2])
    page = open_playlist(login("RaceCoordinator"))
    partial = open_scheduling(page)
    expect(partial.locator("[data-usable]")).to_have_text(
        "2 of 4 lanes usable. Lane 2 is closed as damaged, from the race "
        "dashboard. Lane 4 is set aside as unused."
    )


def test_the_dashboards_schedule_sheet_opens_the_partial(login, event, page_errors):
    page_errors.allow(r"request failed: \S*/dashboard/live/\S* net::ERR_ABORTED$")
    race_setup(event)
    page = login("RaceCoordinator")
    page.goto("/dashboard/")
    page.locator('[data-button="schedule"]').first.click()
    dash = page.locator('[data-sheet="dash"]')
    dash.get_by_role("button", name="Scheduling method and lanes").click()
    expect(dash.locator("[data-scheduling]")).to_be_visible()
    with htmx_post(page, "/playlist/scheduling/method/"):
        dash.get_by_text("Lane rotation", exact=True).click()
    assert race_settings.read(event, "rotation-schedule") == "1"
    expect(dash.locator("[data-schedule-form]")).to_be_visible()


# ----- playlist.php parity ---------------------------------------------------------


def test_racing_scene_and_its_notice(login, event):
    page = open_playlist(login("RaceCoordinator"))
    expect(page.locator("[data-no-scene]")).to_be_visible()
    with htmx_post(page, "/playlist/scene/"):
        page.get_by_role("combobox", name="Racing scene").select_option(label="Racing")
    expect(page.locator("[data-no-scene]")).to_have_count(0)
    assert RaceSettings.objects.get(event=event).racing_scene.name == "Racing"


def test_add_a_final_with_every_follow_on_option(login, event):
    """Top N, by subgroup, runs per lane, and "And then" a scene."""
    race_day(event)
    RaceSettings.objects.filter(event=event).update(use_subgroups=True)
    page = open_playlist(login("RaceCoordinator"))
    add_round(page, "Scouts, Round 1")
    dismiss_toasts(page)

    page.get_by_role("button", name="Add Scouts, Round 2, a follow-on round").click()
    form = sheet(page).locator("[data-entry-sheet]")
    expect(form.get_by_label("How many racers go to this final?")).to_be_focused()
    # D43: no default, and the sheet will not send it empty.
    top = form.get_by_label("How many racers go to this final?")
    expect(top).to_have_value("")
    expect(top).to_have_attribute("required", "")
    form.get_by_label("How many racers go to this final?").fill("4")
    form.get_by_text(re.compile("^Each ")).click()
    form.get_by_label("Runs per lane").select_option("2")
    form.get_by_label("And then").select_option(label="Awards scene")
    shoot(page, "playlist-entry-sheet", full_page=False)
    with htmx_post(page, "/playlist/add/"):
        form.get_by_role("button", name="Add to playlist").click()

    final = PlaylistEntry.objects.get(event=event, round_number=2)
    assert (
        final.bucket_limit,
        final.bucketed,
        final.n_times_per_lane,
        final.scene_at_finish.name,
        final.continue_racing,
    ) == (4, True, 2, "Awards", True)
    expect(card(page, "Scouts, Round 2").locator("[data-then]")).to_have_text(
        "Awards scene"
    )


def test_inline_edit_of_every_field(login, event):
    rounds = race_day(event)
    PlaylistEntryFactory(group=rounds["Scouts"].group, seq=1)
    final = PlaylistEntryFactory(
        group=rounds["Scouts"].group, seq=2, round_number=2, bucket_limit=4
    )
    page = open_playlist(login("RaceCoordinator"))
    card(page, "Scouts, Round 2").locator("[data-edit-entry]").click()
    form = sheet(page).locator("[data-entry-sheet]")
    expect(form.get_by_label("How many racers go to this final?")).to_have_value("4")
    form.get_by_label("How many racers go to this final?").fill("8")
    form.get_by_label("Runs per lane").select_option("3")
    form.get_by_label("And then").select_option(label="Start next round")
    form.get_by_role("switch").uncheck()
    with htmx_post(page, f"/playlist/step/{final.pk}/"):
        form.get_by_role("button", name="Save").click()
    final.refresh_from_db()
    assert (
        final.bucket_limit,
        final.n_times_per_lane,
        final.continue_racing,
        final.scene_at_finish,
        final.waits_for_operator,
    ) == (8, 3, True, None, False)

    dismiss_toasts(page)
    switch = card(page, "Scouts, Round 2").get_by_role("switch")
    with htmx_post(page, f"/playlist/step/{final.pk}/waits/"):
        switch.click()
    final.refresh_from_db()
    assert final.waits_for_operator


def test_remove_with_undo(login, event):
    rounds = race_day(event)
    for seq, name in enumerate(("Scouts", "Outlaws"), 1):
        PlaylistEntryFactory(group=rounds[name].group, seq=seq)
    page = open_playlist(login("RaceCoordinator"))
    page.get_by_role("button", name="Actions for Scouts, Round 1").click()
    with htmx_post(page, "/remove/"):
        page.get_by_role("menuitem", name="Remove").locator("visible=true").click()
    assert queued(event) == [("Outlaws", 1)]
    expect(card(page, "Scouts, Round 1")).to_have_count(0)

    with htmx_post(page, "/playlist/restore/"):
        page.locator("#toasts").get_by_role("button", name="Undo").click()
    assert queued(event) == [("Scouts", 1), ("Outlaws", 1)]
    expect(page.locator("[data-queue] > li").first).to_contain_text("Scouts")


def test_back_to_the_calling_page(login, event):
    page = login("RaceCoordinator")
    open_playlist(page, "?back=setup.php")
    expect(page.get_by_role("link", name="Back")).to_have_attribute("href", "/setup/")
    open_playlist(page)
    expect(page.get_by_role("link", name="Back")).to_have_attribute(
        "href", "/dashboard/"
    )
    page.goto("/playlist.php?back=coordinator.php")
    page.wait_for_url(re.compile(r"/playlist/\?back=coordinator.php$"))


def test_the_master_schedule_notice(login, event):
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    page = open_playlist(login("RaceCoordinator"))
    expect(page.locator("[data-master-notice]")).to_contain_text(
        "does not move on to the next round"
    )


def test_allowed_racers_show_on_the_queued_final(login, event):
    """D32, D44: a racer allowed into the final shows on its card."""
    from racing.services.finals import allow_final_request, request_final_place
    from roster.models import Racer
    from ui.tests.race import add_racers

    rounds = race_day(event)
    PlaylistEntryFactory(group=rounds["Scouts"].group, seq=1)
    PlaylistEntryFactory(
        group=rounds["Scouts"].group, seq=2, round_number=2, bucket_limit=4
    )
    scheduled(rounds["Scouts"])
    while RaceState.objects.get(event=event).racing:
        run_heat(event)
    late = add_racers(event, "Scouts", 1, 199)[0]
    allow_final_request(request_final_place(Racer.objects.get(pk=late.pk)))

    page = open_playlist(login("RaceCoordinator"))
    expect(card(page, "Scouts, Round 2").locator("[data-allowed]")).to_contain_text(
        "Scouts1 Racer"
    )


# ----- screenshots and accessibility -----------------------------------------------


def test_screenshots_and_accessibility(login, event):
    rounds = race_day(event)
    awards = Scene.objects.get(event=event, name="Awards")
    PlaylistEntryFactory(group=rounds["Scouts"].group, seq=1, waits_for_operator=False)
    PlaylistEntryFactory(
        group=rounds["Scouts"].group,
        seq=2,
        round_number=2,
        bucket_limit=4,
        scene_at_finish=awards,
        continue_racing=True,
    )
    PlaylistEntryFactory(group=rounds["Outlaws"].group, seq=3, continue_racing=True)
    page = open_playlist(login("RaceCoordinator"))
    shoot(page, "playlist-queue")
    assert_accessible(page)

    page.get_by_role("button", name="Add Outlaws, Round 2, a follow-on round").click()
    expect(sheet(page).locator("[data-entry-sheet]")).to_be_visible()
    shoot(page, "playlist-entry-sheet-add", full_page=False)
    assert_accessible(page)
    sheet(page).get_by_role("button", name="Cancel").click()

    open_scheduling(page)
    shoot(page, "playlist-scheduling", full_page=False)
    assert_accessible(page)
