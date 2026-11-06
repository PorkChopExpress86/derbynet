"""The kiosk display pages in a browser (#52): `kioskNudge()` on each page
fetches the page's poll endpoint at once and renders what it answers, and
with no push stream a `scene.apply` reaches a kiosk within one 5 s poll.

Each test changes what the server would answer, calls `kioskNudge()`, and
waits less than the page's own poll interval would take, so only the nudge
can have fetched it. #56 calls `kioskNudge()` from the push stream."""

import json

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from core.models import RaceSettings
from kiosks.models import Kiosk, Scene
from racing.services import heats
from roster.models import Racer
from ui.tests.browser.conftest import close_tab, shoot
from ui.tests.race import race_setup, run_heat, scheduled

ADDRESS = "kiosk-test52"
NUDGE_MS = 1500


@pytest.fixture
def day(event):
    rounds = race_setup(event, groups=(("Lions", 4), ("Tigers", 4)))
    RaceSettings.objects.filter(event=event).update(lane_colors="red,blue,green")
    scheduled(rounds["Lions"])
    return rounds


def open_kiosk(page, event, key, params=""):
    Kiosk.objects.update_or_create(
        event=event,
        address=ADDRESS,
        defaults={
            "page": key,
            "params": params,
            "name": "Main",
            "last_contact": timezone.now(),
        },
    )
    page.goto(f"/kiosk.php?address={ADDRESS}")
    expect(page.locator("body")).to_have_attribute("data-page", key)


def settled(page):
    """Wait for the entry animations, so a screenshot shows the page at rest."""
    page.wait_for_function(
        "document.getAnimations().every(a => a.playState !== 'running')"
    )


def nudge(page, endpoint: str):
    """Call kioskNudge() and wait, for less than any page's poll interval,
    for the request it sends to `endpoint`."""
    with page.expect_request(lambda r: endpoint in r.url, timeout=NUDGE_MS):
        page.evaluate("kioskNudge()")


def test_no_content_poll_the_nudge_asks_poll_kiosk_and_shows_the_name(page, event, day):
    open_kiosk(page, event, "kiosks/identify.kiosk")
    Kiosk.objects.filter(address=ADDRESS).update(name="Front TV")

    nudge(page, "query=poll.kiosk")

    expect(page.locator(".k-big[data-kiosk-name]")).to_have_text(
        "Front TV", timeout=NUDGE_MS
    )
    expect(page.locator("[data-window-size]")).to_contain_text(" x ")


def test_qrcode_takes_new_parameters_from_the_nudge(page, event, day):
    open_kiosk(page, event, "kiosks/qrcode.kiosk", '{"title":"One","content":"a"}')
    Kiosk.objects.filter(address=ADDRESS).update(
        params='{"title":"Two","content":"https://example.org/b"}'
    )

    nudge(page, "query=poll.kiosk")

    expect(page.locator("[data-qr-title]")).to_have_text("Two", timeout=NUDGE_MS)
    expect(page.locator("[data-qr] svg")).to_be_visible()


def test_now_racing_shows_the_next_heat_at_the_nudge(page, event, day):
    open_kiosk(page, event, "kiosks/now-racing.kiosk")
    expect(page.locator('[data-lane="1"] [data-car]')).not_to_be_empty()
    settled(page)
    shoot(page, "kiosk-now-racing")
    run_heat(event)

    nudge(page, "query=poll&")

    expect(page.locator('[data-lane="1"] [data-time]')).not_to_be_empty(
        timeout=NUDGE_MS
    )
    expect(page.locator(".k-lane.k-p1")).to_have_count(1, timeout=3000)


def test_ondeck_draws_the_chart_from_the_nudge(page, event, day):
    open_kiosk(page, event, "kiosks/ondeck.kiosk")
    expect(page.locator("tr.k-current")).to_have_count(1)
    heats.select_heat(event, round_id=day["Lions"].pk, heat=2, now_racing=True)

    nudge(page, "values=ondeck")

    expect(page.locator('tr.k-current[data-heat$="_2"]')).to_have_count(
        1, timeout=NUDGE_MS
    )
    settled(page)
    shoot(page, "kiosk-ondeck")


def test_please_check_in_refreshes_at_the_nudge(page, event, day):
    open_kiosk(page, event, "kiosks/please-check-in.kiosk")
    expect(page.locator("[data-all-in]")).to_be_visible()
    racer = Racer.objects.filter(event=event).first()
    Racer.objects.filter(pk=racer.pk).update(passed_inspection=False)

    nudge(page, "/kiosk/check-in/")

    expect(page.locator("[data-wanted] li")).to_have_text(
        [f"{racer.last_name}, {racer.first_name}"], timeout=NUDGE_MS
    )


def test_standings_change_key_at_the_nudge(page, event, day):
    from racing.services.standings import standings_snapshot
    from ui.tests.race import race_round

    race_round(event)
    keys = [e.key for e in standings_snapshot(event).standings.catalog()]
    open_kiosk(page, event, "kiosks/standings.kiosk", json.dumps({"key": keys[0]}))
    expect(page.locator("[data-racer]:not([hidden])").first).to_be_visible()
    Kiosk.objects.filter(address=ADDRESS).update(params=json.dumps({"key": keys[-1]}))

    nudge(page, "/kiosk/standings/")

    expect(page.locator("[data-shown-key]")).to_have_attribute(
        "data-shown-key", keys[-1], timeout=NUDGE_MS
    )


def test_the_award_kiosk_shows_the_current_award_at_the_nudge(page, event, day):
    from awards.editing import set_winner
    from awards.models import Award
    from core.models import RaceState

    open_kiosk(page, event, "kiosks/award-presentations.kiosk")
    award = Award.objects.create(
        event=event, name="Best Paint", award_type_id=1, group=day["Lions"].group
    )
    set_winner(event, award, Racer.objects.filter(event=event).first())
    RaceState.objects.filter(event=event).update(current_award_key=f"award-{award.pk}")

    nudge(page, "query=award.current")

    expect(page.locator("[data-award-name]")).to_have_text(
        "Best Paint", timeout=NUDGE_MS
    )


def test_the_slideshow_asks_slide_next_at_the_nudge(page, event, day):
    open_kiosk(page, event, "kiosks/slideshow.kiosk")

    nudge(page, "query=slide.next")

    expect(page.locator(".k-slide")).not_to_have_count(0)


def test_results_by_racer_polls_its_rounds_at_the_nudge(page, event, day):
    open_kiosk(page, event, "kiosks/results-by-racer.kiosk")

    nudge(page, "/results/rounds/")

    expect(page.locator("[data-round]").first).to_be_visible()


def test_a_scene_reaches_the_kiosk_within_one_poll_with_no_stream(page, event, day):
    """No EventSource is involved: the 5 s poll.kiosk alone carries it."""
    from kiosks import services

    open_kiosk(page, event, "kiosks/welcome.kiosk")
    services.choose_scene(event, Scene.objects.get(event=event, name="Racing"))

    expect(page.locator("body")).to_have_attribute(
        "data-page", "kiosks/now-racing.kiosk", timeout=6000
    )


def test_a_poll_that_never_answers_is_given_up_after_15_s(
    page, page_errors, event, day
):
    """now-racing-poller.js's watchdog queues a new request when none has
    gone out for 15 s. A reply that never comes would otherwise leave the
    chain waiting forever, and kioskNudge() with it."""
    if page.size != "desktop":
        pytest.skip("one size is enough for a 15 s wait")
    page_errors.allow(r"query=poll&\S* net::ERR_ABORTED$")
    held = []

    def hold_first(route):
        if held:
            route.continue_()
        else:
            held.append(route)

    page.route("**/action.php?query=poll&*", hold_first)
    with page.expect_request(lambda r: "query=poll&" in r.url):
        open_kiosk(page, event, "kiosks/now-racing.kiosk")

    with page.expect_request(lambda r: "query=poll&" in r.url, timeout=17000):
        page.evaluate("kioskNudge()")
    expect(page.locator('[data-lane="1"] [data-car]')).not_to_be_empty()


def next_poll(tab):
    """Wait for the reply to a poll sent after the test changed the state.
    The first reply seen may answer a poll that read the state before the
    change, so wait for two; the second went out after the first came back.
    Counting replies, not seconds, keeps a slow server from failing the
    test (seen on Postgres under `-n auto`)."""
    for _ in range(2):
        with tab.expect_response(lambda r: "query=poll&" in r.url):
            pass


def test_d12_two_racing_kiosks_show_the_leaders_through_the_intermission(
    page, live_server
):
    """Two fake kiosks of the Racing scene, now racing and on deck. When the
    round ends and the next step waits for the operator, both show the
    round's leaders at the next poll; when the operator starts the next round
    both go back at the next poll."""
    from core.models import SiteState
    from racing.services.playlist import round_ended, start_playlist
    from racing.services.playlist import start_waiting_step as start
    from racing.services.rounds import playlist_finalists
    from racing.tests.test_race_day import race, race_day

    replay = race_day(4, 8)
    event = replay.event
    SiteState.objects.update_or_create(pk=1, defaults={"active_event": event})
    finalists_for = playlist_finalists()
    round = start_playlist(event, finalists_for)
    tabs = {}
    for address, key in (
        ("kiosk-a-test52", "kiosks/now-racing.kiosk"),
        ("kiosk-b-test52", "kiosks/ondeck.kiosk"),
    ):
        Kiosk.objects.create(
            event=event, address=address, page=key, last_contact=timezone.now()
        )
        tab = page if not tabs else page.context.new_page()
        tab.goto(f"/kiosk.php?address={address}")
        expect(tab.locator("body")).to_have_attribute("data-page", key)
        tabs[address] = tab

    race(round)
    round_ended(round, finalists_for)
    for tab in tabs.values():
        tab.bring_to_front()
        next_poll(tab)
        leaders = tab.locator("[data-leaders]")
        expect(leaders).to_be_visible(timeout=1000)
        expect(leaders).to_contain_text("Scouts, Round 1 leaders")
        expect(leaders.locator("li")).to_have_count(3)
        expect(leaders).to_contain_text("Up next: Scouts, Round 2")
    page.bring_to_front()
    page.wait_for_function(
        "document.getAnimations().every(a => a.playState !== 'running')"
    )
    shoot(page, "kiosk-intermission")

    start(event)
    for tab in tabs.values():
        tab.bring_to_front()
        next_poll(tab)
        expect(tab.locator("[data-leaders]")).to_be_hidden(timeout=1000)
    for tab in tabs.values():
        if tab is not page:
            close_tab(page, tab)
