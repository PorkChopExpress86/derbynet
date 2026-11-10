"""The kiosk display pages in a browser (#52): `kioskNudge()` on each page
fetches the page's poll endpoint at once and renders what it answers, and
with no push stream a `scene.apply` reaches a kiosk within one 5 s poll.

Each test changes what the server would answer, calls `kioskNudge()`, and
waits less than the page's own poll interval would take, so only the nudge
can have fetched it. #56 calls `kioskNudge()` from the push stream."""

import json
import shutil
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from core.models import RaceSettings
from kiosks.models import Kiosk, Scene
from racing.models import HeatLane, TimerStatus
from racing.services import heats
from roster.models import Racer
from ui.tests.browser.conftest import close_tab, shoot
from ui.tests.race import race_setup, run_heat, scheduled

ADDRESS = "kiosk-test52"
NUDGE_MS = 1500
HEADSHOTS = Path(__file__).resolve().parents[4] / "testing" / "data" / "headshots"


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
    """Wait until no animation is running, so a screenshot shows the page at
    rest. That is every animation, not only the entry ones: on a page with an
    `infinite` one, such as the flag's wave (kiosk.css `k-wave`), whose
    playState stays "running", it waits until it times out (#238). The wait
    in `test_each_page_with_no_other_test_draws_at_rest` skips those."""
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
    settled(page)
    shoot(page, "kiosk-identify")


def test_qrcode_takes_new_parameters_from_the_nudge(page, event, day):
    open_kiosk(page, event, "kiosks/qrcode.kiosk", '{"title":"One","content":"a"}')
    Kiosk.objects.filter(address=ADDRESS).update(
        params='{"title":"Results online","content":"https://example.org/results"}'
    )

    nudge(page, "query=poll.kiosk")

    expect(page.locator("[data-qr-title]")).to_have_text(
        "Results online", timeout=NUDGE_MS
    )
    expect(page.locator("[data-qr] svg")).to_be_visible()
    settled(page)
    shoot(page, "kiosk-qrcode")


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
    Racer.objects.filter(event=event, first_name__in=("Lions2", "Tigers3")).update(
        passed_inspection=False
    )

    nudge(page, "/kiosk/check-in/")

    expect(page.locator("[data-wanted] li")).to_have_text(
        ["Racer, Lions2", "Racer, Tigers3"], timeout=NUDGE_MS
    )
    settled(page)
    shoot(page, "kiosk-please-check-in")


def test_standings_change_key_at_the_nudge(page, event, day):
    """The rows reveal one every 1.5 s, last place first (kiosk-standings.js).
    The last catalog entry here lists the 4 Lions, so the shot waits for
    4 x 1.5 s = 6 s of reveals, within a 20 s bound: 14 s of slack."""
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
    expect(page.locator("[data-racer]")).to_have_count(4)
    expect(page.locator("[data-racer][hidden]")).to_have_count(0, timeout=20_000)
    settled(page)
    shoot(page, "kiosk-standings")


def test_the_award_kiosk_shows_the_current_award_at_the_nudge(page, event, day):
    from awards.editing import set_winner
    from awards.models import Award
    from core.models import RaceState

    open_kiosk(page, event, "kiosks/award-presentations.kiosk")
    award = Award.objects.create(
        event=event, name="Best Paint", award_type_id=1, group=day["Lions"].group
    )
    winner = Racer.objects.filter(event=event).first()
    set_winner(event, award, winner)
    RaceState.objects.filter(event=event).update(current_award_key=f"award-{award.pk}")

    nudge(page, "query=award.current")

    expect(page.locator("[data-award-name]")).to_have_text(
        "Best Paint", timeout=NUDGE_MS
    )
    RaceState.objects.filter(event=event).update(reveal_current_award=True)
    expect(page.locator("[data-reveal]")).to_be_visible()
    expect(page.locator("[data-recipient]")).to_have_text(
        f"{winner.first_name} {winner.last_name}"
    )
    settled(page)
    shoot(page, "kiosk-award-presentations")


def test_the_slideshow_asks_slide_next_at_the_nudge(
    settings, tmp_path, page, event, day
):
    """With no slides in the event's or the config folder, slide.next falls
    to the racers with a photo: one headshot here."""
    settings.DATA_DIR = tmp_path / "data"
    settings.CONFIG_DIR = tmp_path / "config"
    (tmp_path / "racers").mkdir()
    shutil.copy(HEADSHOTS / "Cub-0033.jpg", tmp_path / "racers")
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(tmp_path / "racers")
    )
    Racer.objects.filter(event=event, first_name="Lions1").update(
        headshot="Cub-0033.jpg"
    )
    open_kiosk(page, event, "kiosks/slideshow.kiosk")

    nudge(page, "query=slide.next")

    expect(page.locator(".k-slide").last.locator(".k-caption")).to_have_text(
        "101: Lions1 Racer"
    )
    page.wait_for_function(
        "(() => { const img = [...document.querySelectorAll('.k-slide')].pop()"
        ".querySelector('img'); return img.complete && img.naturalWidth > 0; })()"
    )
    settled(page)
    shoot(page, "kiosk-slideshow")


def test_results_by_racer_polls_its_rounds_at_the_nudge(page, event, day):
    run_heat(event)
    run_heat(event)
    open_kiosk(page, event, "kiosks/results-by-racer.kiosk")

    nudge(page, "/results/rounds/")

    expect(page.locator("[data-round]").first).to_be_visible()
    expect(page.locator("[data-result] [data-shown]").first).not_to_be_empty()
    # Its screenshot is test_kiosk_results_by_racer.py's, taken with the
    # list's glide paused at the top (#247).


# What shows that each page no other test here drives has drawn (#238).
READY = {
    "derbynet": ".k-center img.k-enter",
    "flag": ".k-flag img",
    "now-racing-columnar": "[data-best] div",
    "now-racing-columnar-reversed": "[data-best] div",
    "scale-MPH": "[data-scale] [data-car]",
}


@pytest.mark.parametrize("name", sorted(READY))
def test_each_page_with_no_other_test_draws_at_rest(page, event, day, name):
    """The screenshot of each kiosk page no test above drives (#238). The
    columnar pages show heat 2 staged on a connected timer, with heat 1's
    times among the best heat times."""
    run_heat(event)
    TimerStatus.objects.filter(event=event).update(
        state=TimerStatus.State.STAGING, assume_connected_for_test=True
    )
    open_kiosk(page, event, f"kiosks/{name}.kiosk")

    expect(page.locator(READY[name]).first).to_be_visible()
    if name.startswith("now-racing-columnar"):
        expect(page.locator('[data-lane="1"] [data-car]')).not_to_be_empty()
        heat_1 = HeatLane.objects.filter(round=day["Lions"], heat=1)
        for time in heat_1.values_list("finish_time", flat=True):
            expect(page.locator("[data-best]")).to_contain_text(f"{time:.3f}")
    page.wait_for_function(
        "[...document.images].every(i => i.complete && i.naturalWidth > 0)"
    )
    # `settled()`, less the flag's wave, which never ends (kiosk.css `k-wave`).
    page.wait_for_function(
        "document.getAnimations().every(a => a.playState !== 'running'"
        " || a.effect.getComputedTiming().iterations === Infinity)"
    )
    shoot(page, f"kiosk-{name}")


def test_a_scene_reaches_the_kiosk_within_one_poll_with_no_stream(page, event, day):
    """No EventSource is involved: the 5 s poll.kiosk alone carries it."""
    from kiosks import services

    open_kiosk(page, event, "kiosks/welcome.kiosk")
    settled(page)
    shoot(page, "kiosk-welcome")
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


# Lanes 2 and 3 tie. The server stores both as place 1, and the columnar
# pages number places by finish order (`i + 1` after a sort by place,
# now-racing-columnar.js:58-74), so the tied lanes show 1 and 2 in lane
# order.
TIED_TIMES = (3.1, 2.5, 2.5, 3.3)
FINISH_ORDER = {2: "1", 3: "2", 1: "3", 4: "4"}
# The places land 400 ms apart (kiosk-now-racing.js `results()`), the last
# 1.2 s after the first, which comes with the first poll reply after the
# result, at most 500 ms after the reply before it plus a round trip:
# about 1.7 s plus two round trips against 10 s, about 8 s of slack.
LAND_MS = 10_000
# A poll request goes out 500 ms after the page has handled the reply before
# it (kiosk-now-racing.js `Kiosk.poll(poll, 500, 'after')`); the first after
# the landing also waits out its last 400 ms: 900 ms plus a round trip
# against 5 s, about 4 s of slack.
POLL_MS = 5_000
# The places' changes while they are watched, one batch per MutationObserver
# callback: the changes made in one task, each as [lane, text, class].
WATCH_PLACES = """() => {
  window.placeBatches = [];
  const observer = new MutationObserver(records => window.placeBatches.push(
    records.map(r => (r.target.nodeType === 1 ? r.target : r.target.parentNode)
      .closest('[data-place]'))
      .map(p => [p.closest('[data-lane]').dataset.lane, p.textContent, p.className])));
  document.querySelectorAll('[data-place]').forEach(el => observer.observe(el,
    {childList: true, characterData: true, subtree: true, attributes: true}));
}"""


@pytest.mark.parametrize(
    "name", ("now-racing-columnar", "now-racing-columnar-reversed")
)
def test_a_columnar_page_keeps_the_places_through_the_linger(page, event, day, name):
    """#251: the columnar pages kept a heat's places for one poll, then
    blanked them while its times stayed. PHP keeps them until the next heat's
    lineup replaces them (`results_written`, now-racing-columnar.js:52-54).

    The heat finishes with a tie and the places land, one lane at a time.
    Then three poll requests go by, each sent 500 ms after the page has
    handled the reply before it (kiosk.js `queue()`), the first after the
    landing. Each reply handled since writes every lane's place in one task,
    with the same finish-order number, so none blanks or changes, and the
    landing doesn't run again: it would write them one lane per task. The
    linger is 60 s, so the next lineup can't clear them first."""
    RaceSettings.objects.filter(event=event).update(now_racing_linger_ms=60_000)
    open_kiosk(page, event, f"kiosks/{name}.kiosk")
    expect(page.locator('[data-lane="1"] [data-car]')).not_to_be_empty()

    run_heat(event, times=TIED_TIMES)
    stored = HeatLane.objects.filter(round=day["Lions"], heat=1).order_by("lane")
    assert [row.finish_place for row in stored] == [3, 1, 1, 4]

    def place(lane):
        return page.locator(f'[data-lane="{lane}"] [data-place]')

    for lane, shown in FINISH_ORDER.items():
        expect(place(lane)).to_have_text(shown, timeout=LAND_MS)
    page.evaluate(WATCH_PLACES)

    for _ in range(3):
        with page.expect_request(lambda r: "query=poll&" in r.url, timeout=POLL_MS):
            pass

    for lane, shown in FINISH_ORDER.items():
        expect(place(lane)).to_have_text(shown)
        expect(page.locator(f'[data-lane="{lane}"] [data-time]')).not_to_be_empty()
    # one read: every change made to a place while the polls went by.
    batches = page.evaluate("window.placeBatches")
    assert len(batches) >= 2
    for batch in batches:
        assert sorted({lane for lane, _, _ in batch}) == ["1", "2", "3", "4"]
        for lane, text, cls in batch:
            assert text == FINISH_ORDER[int(lane)]
            assert "k-shown" in cls.split()
