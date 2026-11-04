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
from ui.tests.browser.conftest import shoot
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
