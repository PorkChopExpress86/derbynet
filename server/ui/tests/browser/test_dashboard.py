"""The race dashboard in a browser (#41): the acceptance flows at 390x844 and
1280x800, the coordinator.php parity boxes, and the screenshots."""

import re

import pytest
from playwright.sync_api import expect

from core.models import RaceSettings, RaceState, SiteState
from racing.models import HeatLane, RosterEntry, Round
from racing.services import (
    finals,
    heats,
    master,
    reschedule,
    rounds,
    schedule,
    timer,
)
from racing.services.rounds import FollowOn, playlist_finalists
from ui.tests.browser.conftest import assert_accessible, dismiss_toasts, shoot
from ui.tests.race import (
    add_racers,
    chart,
    current,
    heat_cars,
    race_setup,
    run_heat,
    scheduled,
)

ABORTED_POLL = r"request failed: \S*/dashboard/live/\S* net::ERR_ABORTED$"


@pytest.fixture(autouse=True)
def _polls_may_be_cut_short(page_errors):
    """A control aborts a poll still in flight (`hx-sync="#live:replace"`),
    so a stale poll never swaps after the control's own reply. The browser
    reports the aborted poll as a failed request."""
    page_errors.allow(ABORTED_POLL)


def open_dashboard(page):
    page.goto("/dashboard/")
    expect(page.locator("#live")).to_be_visible()
    return page


def sheet(page):
    return page.locator('[data-sheet="dash"]')


def finished(event, round):
    scheduled(round)
    while current(event)[2]:
        run_heat(event)


def stop_polling(page):
    """Keep the 2 s poll from firing, so every request after this is one
    the test caused."""
    page.evaluate("window.derbynet.pollable = () => false")


# ----- D36: the not-added list ----------------------------------------------------


def test_a_late_racer_joins_only_after_add_to_schedule(login, event):
    """A racer passed after round 1 started, as check-in's No leaves one
    (#42 asks the question; here the check-in service passes the racer).
    The preview shows the racer in the unraced heats, and the chart changes
    only after Add to schedule (D32, D36)."""
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    run_heat(event)
    run_heat(event)
    late = add_racers(event, "Wolves", 1, 150)[0]
    page = open_dashboard(login("RaceCoordinator"))
    before = chart(wolves)

    row = page.locator(f'[data-not-added-row="{late.pk}"]')
    expect(row).to_contain_text("150 Wolves1 Racer")
    expect(row).to_contain_text("Round 1")
    row.get_by_role("button", name="Preview and add").click()
    preview = sheet(page)
    expect(preview.locator('[data-chart="2"]')).to_contain_text("150")
    new_heats = [
        int(n)
        for n in re.findall(r"\d+", preview.locator("p.text-sm").first.inner_text())
    ]
    assert new_heats and min(new_heats) > 3, new_heats
    assert chart(wolves) == before
    shoot(page, "dashboard-late-preview")

    preview.get_by_role("button", name="Add to schedule").click()
    expect(row).to_have_count(0)
    after = chart(wolves)
    assert after[:3] == before[:3]
    assert any(150 in heat for heat in after) and not any(150 in h for h in before)


# ----- D36, D45: closing a lane ---------------------------------------------------


def test_closing_lane_3_during_heat_3(login, event):
    """Heats 3 (running) and 4 (staged) stay as they were, lane 3 is empty in
    every later heat, and Reopen shows until the next round is scheduled."""
    found = race_setup(event, groups=(("Wolves", 8), ("Bears", 5)))
    wolves = found["Wolves"]
    scheduled(wolves)
    run_heat(event)
    run_heat(event)
    assert current(event)[:2] == (wolves.pk, 3)
    before = chart(wolves)
    page = open_dashboard(login("RaceCoordinator"))

    page.get_by_role("button", name="Close a lane").click()
    form = sheet(page)
    expect(form.locator("[data-affected]")).to_contain_text("Wolves, Round 1")
    expect(form.get_by_label("Average all heats")).to_be_checked()
    form.get_by_text("Lane 3", exact=True).click()
    form.get_by_text("Drop the slowest heat").click()
    shoot(page, "dashboard-close-lane")
    form.get_by_role("button", name="Close lanes").click()

    closed = page.locator("[data-closed-lanes]")
    expect(closed).to_contain_text("Lane 3 closed · Drop the slowest heat")
    after = chart(wolves)
    assert after[2:4] == before[2:4]
    assert all(heat[2] is None for heat in after[4:])
    expect(page.locator("[data-roundid] [data-round-closed]")).to_contain_text("Lane 3")

    expect(closed.get_by_role("button", name="Reopen lane 3")).to_be_visible()
    schedule.generate_schedule(found["Bears"], 1)
    page.reload()
    expect(page.locator("[data-closed-lanes]")).to_contain_text("Lane 3 closed")
    expect(page.get_by_role("button", name="Reopen lane 3")).to_have_count(0)


def test_closing_a_lane_under_the_master_schedule_names_every_round(login, event):
    found = race_setup(event, groups=(("Wolves", 6), ("Bears", 6), ("Lions", 5)))
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    for r in found.values():
        scheduled(r, race=False)
    heats.select_heat(event, heat="next-up", now_racing=True)
    run_heat(event)
    page = open_dashboard(login("RaceCoordinator"))

    page.get_by_role("button", name="Close a lane").click()
    affected = sheet(page).locator("[data-affected] li")
    expect(affected).to_have_text(
        ["Wolves, Round 1", "Bears, Round 1", "Lions, Round 1"]
    )


# ----- D32: final requests and finalist suggestions --------------------------------


@pytest.mark.parametrize("decision", ["Allow", "Decline"])
def test_a_final_request_from_check_in(login, event, decision):
    wolves = race_setup(event)["Wolves"]
    finished(event, wolves)
    late = add_racers(event, "Wolves", 1, 150)[0]
    request = finals.request_final_place(late)
    final = rounds.new_round_roster(FollowOn(wolves), 3).round
    qualifiers = set(final.roster.values_list("racer_id", flat=True))
    page = open_dashboard(login("RaceCoordinator"))

    card = page.locator(f'[data-final-request="{request.pk}"]')
    expect(card).to_contain_text("150 Wolves1 Racer · Wolves")
    card.get_by_role("button", name=decision).click()
    expect(card).to_have_count(0) if decision == "Allow" else expect(
        card
    ).to_contain_text("declined")
    roster = set(final.roster.values_list("racer_id", flat=True))
    if decision == "Allow":
        assert roster == qualifiers | {late.pk}
    else:
        assert roster == qualifiers


def test_a_changed_round_1_result_suggests_finalists(login, event):
    """Car 103 qualifies for a top 3 final. Once the final has raced a heat,
    the race coordinator gives 103 a slow time in round 1: the card suggests
    adding 104 and removing 103, and the final stays as it was until a
    button is tapped (D32)."""
    wolves = race_setup(event)["Wolves"]
    finished(event, wolves)
    final = rounds.new_round_roster(FollowOn(wolves), 3).round
    scheduled(final)
    run_heat(event)
    roster = set(final.roster.values_list("racer__car_number", flat=True))
    assert roster == {101, 102, 103}
    heat_103 = next(h for h in range(1, 9) if 103 in heat_cars(wolves, h))
    heats.select_heat(event, round_id=wolves.pk, heat=heat_103, now_racing=False)
    page = open_dashboard(login("RaceCoordinator"))

    page.get_by_role("button", name="Manual results").click()
    lane = heat_cars(wolves, heat_103).index(103) + 1
    sheet(page).get_by_label(f"Time, lane {lane}").fill("9.5")
    sheet(page).get_by_role("button", name="Save results").click()

    card = page.locator(f'[data-suggestions="{final.pk}"]')
    expect(card).to_contain_text("Now qualifies: 104")
    expect(card).to_contain_text("No longer qualifies: 103")
    assert set(final.roster.values_list("racer__car_number", flat=True)) == roster
    shoot(page, "dashboard-suggestions")

    card.get_by_role("button", name=re.compile("^Add ")).click()
    expect(card).not_to_contain_text("Now qualifies")
    assert set(final.roster.values_list("racer__car_number", flat=True)) == {
        101,
        102,
        103,
        104,
    }


# ----- D12: the playlist intermission ----------------------------------------------


def test_the_race_day_runs_through_both_finals(login, page):
    """The owner's race day (D11): Scouts round 1 and a top 4 final, then
    Outlaws round 1 and a top 8 final, queued through the playlist service.
    Each intermission shows the next round and the leaders, and one tap on
    Start next round starts it."""
    from racing.services.playlist import round_ended, start_playlist
    from racing.tests.test_race_day import race, race_day

    replay = race_day(4, 8)
    event = replay.event
    SiteState.objects.update_or_create(pk=1, defaults={"active_event": event})
    finalists_for = playlist_finalists()
    round = start_playlist(event, finalists_for)
    assert round.number == 1
    login("RaceCoordinator")
    open_dashboard(page)
    expected = ["Scouts, Round 2", "Outlaw, Round 1", "Outlaw, Round 2"]
    for i, name in enumerate(expected):
        race(round)
        round_ended(round, finalists_for)
        card = page.locator("[data-intermission]")
        expect(card).to_contain_text(f"Up next: {name}", timeout=5000)
        expect(card.locator("[data-leaders] li")).to_have_count(3)
        expect(card).to_contain_text(
            f"{round.group.name}, Round {round.number} leaders"
        )
        if i == 0:
            shoot(page, "dashboard-intermission")
        dismiss_toasts(page)
        card.get_by_role("button", name="Start next round").click()
        expect(page.locator("[data-intermission]")).to_have_count(0)
        state = RaceState.objects.get(event=event)
        assert state.racing and state.current_heat == 1
        round = Round.objects.select_related("group").get(pk=state.current_round_id)
        assert f"{round.group.name}, Round {round.number}" == name
    race(round)
    round_ended(round, finalists_for)
    assert RaceState.objects.get(event=event).waiting_entry is None


# ----- the timer finishes a heat ---------------------------------------------------


def test_a_heat_the_timer_finishes_shows_within_3_seconds(
    login, event, playwright, live_server
):
    """`run_heat` in testing/common.sh: log in as Timer, post STARTED and
    FINISHED to Django's /action.php."""
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    page = open_dashboard(login("RaceCoordinator"))
    expect(page.locator("[data-heat-text]")).to_have_text("Heat 1 of 8")
    cars = heat_cars(wolves, 1)

    api = playwright.request.new_context(base_url=live_server.url)
    try:
        login_reply = api.post(
            "/action.php",
            form={"action": "role.login", "name": "Timer", "password": ""},
        )
        assert login_reply.ok, login_reply.text()
        assert (
            "success"
            in api.post(
                "/action.php", form={"action": "timer-message", "message": "STARTED"}
            ).text()
        )
        finished_reply = api.post(
            "/action.php",
            form={
                "action": "timer-message",
                "message": "FINISHED",
                "lane1": "2.111",
                "lane2": "2.222",
                "lane3": "2.333",
                "lane4": "2.444",
            },
        )
        assert "heat-ready" in finished_reply.text()
    finally:
        api.dispose()

    # The finished heat holds on screen with its times, beside the next heat.
    held = page.locator("[data-holding]")
    expect(held).to_be_visible(timeout=3000)
    expect(page.locator('[data-lane="1"] [data-result]')).to_contain_text("2.111")
    expect(page.locator('[data-lane="1"] [data-car]')).to_have_text(f"{cars[0]:03d}")
    expect(page.locator('[data-lane="1"] [data-staging]')).to_contain_text(
        f"{heat_cars(wolves, 2)[0]:03d}"
    )
    shoot(page, "dashboard-heat-finished")


# ----- every mutation updates the page from its own response -----------------------


def test_each_control_updates_the_page_without_a_get(login, event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    page = open_dashboard(login("RaceCoordinator"))
    page.wait_for_load_state("networkidle")
    stop_polling(page)
    gets = []
    page.on(
        "request",
        lambda r: (
            r.method == "GET"
            and "/dashboard/" in r.url
            and "sheet" not in r.url
            and gets.append(r.url)
        ),
    )
    heat_text = page.locator("[data-heat-text]")
    racing_label = page.locator("#racing-label")

    page.get_by_role("switch", name=re.compile("racing", re.I)).click()
    expect(racing_label).to_have_text("Not racing")
    page.get_by_role("switch", name=re.compile("racing", re.I)).click()
    expect(racing_label).to_have_text("Racing")

    page.get_by_role("button", name="Manual results").click()
    for lane, time in enumerate(("2.5", "2.6", "2.7", "2.8"), 1):
        sheet(page).get_by_label(f"Time, lane {lane}").fill(time)
    sheet(page).get_by_role("button", name="Save results").click()
    # Racing moved on to heat 2; heat 1 holds on screen with its results.
    expect(page.locator("[data-hold-note]")).to_contain_text("Results of heat 1")
    expect(page.locator('[data-lane="1"] [data-result]')).to_contain_text("2.500")
    assert current(event)[1] == 2

    rerun = page.locator('[data-action="rerun"]')
    expect(rerun).to_have_text("Re-Run Previous")
    rerun.click()
    expect(rerun).to_have_text("Reinstate Heat")
    rerun.click()
    # The reinstated heat 1 is current again, with its results.
    expect(rerun).to_have_text("Re-Run This Heat")
    expect(heat_text).to_have_text("Heat 1 of 8")

    page.locator('[data-action="next"]').click()
    expect(heat_text).to_have_text("Heat 2 of 8")
    page.locator('[data-action="prev"]').click()
    expect(heat_text).to_have_text("Heat 1 of 8")
    expect(page.locator('[data-lane="1"] [data-result]')).to_contain_text("2.500")
    page.get_by_role("button", name="Manual results").click()
    sheet(page).get_by_role("button", name="Discard results").click()
    expect(page.locator('[data-lane="1"] [data-result]')).not_to_contain_text("2.5")
    page.locator("#toasts").get_by_role("button", name="Undo").click()
    expect(page.locator('[data-lane="1"] [data-result]')).to_contain_text("2.500")

    assert gets == []


# ----- D39: an emptied heat --------------------------------------------------------


def test_an_emptied_heat_keeps_its_number_and_is_skipped_by_hand(login, event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    run_heat(event)
    run_heat(event)  # Heat 3 runs; heat 4 is staged.
    # Every car of the staged heat is taken off the roster (D37, D39).
    staged = [c for c in heat_cars(wolves, 4) if c]
    RosterEntry.objects.filter(round=wolves, racer__car_number__in=staged).delete()
    reschedule.reschedule(wolves)
    assert (wolves.pk, 4) in {(r, h) for _, r, h, _ in master.held_emptied_heats(event)}
    run_heat(event)  # Heat 3's result moves racing to the emptied heat 4.
    assert current(event)[:2] == (wolves.pk, 4)
    page = open_dashboard(login("RaceCoordinator"))

    expect(page.locator("[data-heat-text]")).to_have_text(re.compile("^Heat 4 of"))
    expect(page.locator("[data-emptied]")).to_contain_text("Every car has left heat 4")
    expect(page.locator("[data-roundid] [data-round-emptied]")).to_contain_text("4")
    shoot(page, "dashboard-emptied-heat")
    page.get_by_role("button", name="Skip heat").click()
    expect(page.locator("[data-heat-text]")).to_have_text(re.compile("^Heat 5 of"))


# ----- parity with coordinator.php --------------------------------------------------


def test_timer_and_test_links(login, event):
    page = open_dashboard(login("RaceCoordinator"))
    if page.size == "phone":
        expect(page.get_by_role("link", name="Timer status")).to_be_visible()
        expect(page.get_by_role("button", name="Timer", exact=True)).to_be_hidden()
        return
    expect(page.get_by_role("link", name="Test", exact=True)).to_have_attribute(
        "href", "/timer/test/"
    )
    page.route("**/timer.php", lambda route: route.fulfill(body="<title>Timer</title>"))
    with page.expect_popup() as popup:
        page.get_by_role("button", name="Timer", exact=True).click()
    assert popup.value.url.endswith("/timer.php")


def test_schedule_race_and_round_controls(login, event):
    found = race_setup(event, groups=(("Wolves", 6), ("Bears", 5)))
    page = open_dashboard(login("RaceCoordinator"))
    bears = page.locator(f'[data-roundid="{found["Bears"].pk}"]')
    bears.get_by_role("button", name="Schedule").click()
    form = sheet(page)
    form.get_by_role("button", name="More: Times each racer runs in each lane").click()
    expect(
        form.get_by_role("spinbutton", name="Times each racer runs in each lane")
    ).to_have_value("2")
    expect(form.get_by_role("button", name="Schedule Only")).to_be_visible()
    form.get_by_role("button", name="Schedule + Race").click()
    expect(page.locator("[data-heat-title]")).to_have_text("Bears, Round 1")
    assert HeatLane.objects.filter(round=found["Bears"]).count() == 40

    wolves = page.locator(f'[data-roundid="{found["Wolves"].pk}"]')
    dismiss_toasts(page)
    wolves.get_by_role("button", name="Schedule").click()
    sheet(page).get_by_role("button", name="Schedule Only").click()
    expect(wolves.get_by_role("button", name="Race")).to_be_visible()
    dismiss_toasts(page)
    wolves.get_by_role("button", name="Unschedule").click()
    expect(sheet(page)).to_contain_text("has a schedule of 6 heats")
    sheet(page).get_by_role("button", name="Unschedule 6 heats").click()
    expect(wolves.get_by_role("button", name="Schedule")).to_be_visible()

    run_heat(event)
    page.reload()
    bears = page.locator(f'[data-roundid="{found["Bears"].pk}"]')
    dismiss_toasts(page)
    bears.get_by_role("button", name="Repeat Round").click()
    expect(sheet(page)).to_contain_text("This cannot be undone")
    sheet(page).get_by_role("button", name="Purge 1 heat of results").click()
    expect(bears.locator("[data-progress-text]")).to_contain_text("0 run")


def test_new_round_stepper(login, event):
    wolves = race_setup(event)["Wolves"]
    finished(event, wolves)
    page = open_dashboard(login("RaceCoordinator"))
    page.get_by_role("button", name="Add New Rounds").click()
    form = sheet(page)
    form.get_by_text("Round 2 of Wolves").click()
    form.get_by_role("button", name="Next").click()
    form.get_by_role("button", name="More: How many racers").click()
    expect(form.get_by_role("spinbutton", name="How many racers")).to_have_value("4")
    shoot(page, "dashboard-new-round")
    form.get_by_role("button", name="Next").click()
    expect(form.locator("[data-review]")).to_contain_text("Top 4 overall")
    form.get_by_role("button", name="Make round").click()
    card = page.locator("[data-roundid]", has_text="Round 2")
    expect(card.get_by_role("button", name="Schedule")).to_be_visible()
    assert Round.objects.get(group=wolves.group, number=2).roster.count() == 4


def test_simulated_racing_and_unexpected_results(login, event):
    timer.set_test_mode(event, True)
    heats.set_racing(event, True)
    page = open_dashboard(login("RaceCoordinator"))
    herald = page.locator("[data-herald]")
    expect(herald).to_contain_text("Simulated racing in progress")
    herald.get_by_role("button", name="Stop").click()
    expect(herald).to_have_count(0)
    RaceState.objects.filter(event=event).update(refused_results=1)
    expect(page.locator("[data-refused]")).to_contain_text(
        "1 unexpected result from the timer has been received.", timeout=5000
    )


def test_start_playlist_and_playlist_link(login, event):
    from racing.tests.factories import PlaylistEntryFactory

    wolves = race_setup(event)["Wolves"]
    PlaylistEntryFactory(event=event, group=wolves.group, round_number=1)
    page = open_dashboard(login("RaceCoordinator"))
    expect(page.get_by_role("link", name="Rounds Playlist")).to_have_attribute(
        "href", "/playlist/"
    )
    page.get_by_role("button", name="Start Playlist").click()
    # With one group, PHP's round name leaves the group out.
    expect(page.locator("[data-heat-title]")).to_have_text("Round 1")


# ----- screenshots and accessibility -----------------------------------------------


def test_screenshots_and_accessibility(login, event):
    found = race_setup(event, groups=(("Wolves", 8), ("Bears", 6)))
    page = open_dashboard(login("RaceCoordinator"))
    shoot(page, "dashboard-idle")
    assert_accessible(page)

    scheduled(found["Wolves"])
    run_heat(event)
    page.reload()
    expect(page.locator("[data-heat-text]")).to_have_text("Heat 2 of 8")
    shoot(page, "dashboard-heat-in-progress")
    assert_accessible(page)

    page.get_by_role("button", name="Manual results").click()
    expect(sheet(page).get_by_label("Time, lane 1")).to_be_focused()
    shoot(page, "dashboard-manual-results")
    assert_accessible(page)


def test_the_review_names_the_buckets_step_2_named(login, event):
    wolves = race_setup(event)["Wolves"]
    finished(event, wolves)
    page = open_dashboard(login("RaceCoordinator"))
    page.get_by_role("button", name="Add New Rounds").click()
    form = sheet(page)
    form.get_by_text("Round 2 of Wolves").click()
    form.get_by_role("button", name="Next").click()
    form.locator("[data-bucketed]").click()
    step_2 = form.locator("#bucketed-label").inner_text()
    assert step_2.startswith("From each ")
    form.get_by_role("button", name="Next").click()
    expect(form.locator("[data-review]")).to_contain_text(
        "Top 3 " + step_2[0].lower() + step_2[1:]
    )


def test_a_poll_that_fails_otherwise_still_fails_the_test(login, event, page_errors):
    """Only an aborted poll is allowed; a poll the network drops is an error."""
    page = open_dashboard(login("RaceCoordinator"))
    page.route("**/dashboard/live/**", lambda route: route.abort("failed"))
    page.wait_for_event("requestfailed", timeout=5000)
    failed = [e for e in page_errors.errors if "/dashboard/live/" in e]
    assert failed and "ERR_FAILED" in failed[0]
    page.unroute("**/dashboard/live/**")
    page_errors.errors.clear()
    page_errors.allow(".*")


def test_a_control_pressed_during_the_hold_keeps_the_held_heat(login, event):
    """Every control sends the heat on screen and its hold, so a sheet saved
    during the 10 s hold leaves the finished heat on screen."""
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    page = open_dashboard(login("RaceCoordinator"))
    page.get_by_role("button", name="Manual results").click()
    for lane, time in enumerate(("2.5", "2.6", "2.7", "2.8"), 1):
        sheet(page).get_by_label(f"Time, lane {lane}").fill(time)
    sheet(page).get_by_role("button", name="Save results").click()
    note = page.locator("[data-hold-note]")
    expect(note).to_contain_text("Results of heat 1")
    dismiss_toasts(page)
    page.get_by_role("button", name="Replay settings").click()
    sheet(page).get_by_role("button", name="Save").click()
    expect(page.locator("#toasts")).to_contain_text("Replay settings saved.")
    expect(note).to_contain_text("Results of heat 1")
