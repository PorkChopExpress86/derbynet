"""The kiosk pages other than now racing through an instant replay (#509,
D140): `replay-started` pauses neither their polls nor their scroll, since
in PHP only now-racing.js:3-17 and now-racing-columnar.js:5-12 read the
message. The now-racing pages' own replay rules are in test_replay.py
(D137)."""

from datetime import datetime

import pytest
from playwright.sync_api import expect

from ui.tests.browser.helpers import pause_clock_at
from ui.tests.race import race_round, race_setup, scheduled

# The page logs `replay-started` with the list's glide offset at that
# moment, and each results/rounds/ fetch with its time.
RECORD = """window.__log = [];
window.addEventListener('message', (e) => {
  const rounds = document.querySelector('[data-rounds]');
  window.__log.push({at: performance.now(), message: e.data,
                     transform: rounds ? rounds.style.transform : null});
});
const send = window.fetch;
window.fetch = function (url, ...rest) {
  if (String(url).includes('/results/rounds/')) {
    window.__log.push({at: performance.now(), fetch: String(url)});
  }
  return send.call(this, url, ...rest);
};"""

# After the message: a results/rounds/ fetch, and the list moved.
POLLED_AND_GLIDED = """() => {
  const [started] = window.__log.filter((e) => e.message === 'replay-started');
  if (!started) return false;
  const fetched = window.__log.some((e) => e.fetch && e.at > started.at);
  const now = document.querySelector('[data-rounds]').style.transform;
  return fetched && now !== started.transform;
}"""

# Within one poll's wait (1 s, kiosk-results.js) and its reply, and 25 of
# the glide's 40 ms steps: slack 4000 ms less that reply.
RESULTS_WAIT_MS = 5000

PAUSED_AT = datetime(2026, 11, 9, 12, 0, 0)


@pytest.fixture
def raced(event):
    """Lions' 16 racers, every heat of round 1 raced: on a 1280x800 TV the
    results list and the standings are taller than the screen."""
    rounds = race_setup(event, groups=(("Lions", 16),))
    scheduled(rounds["Lions"])
    race_round(event)
    return rounds


@pytest.mark.sizes("desktop", reason="the kiosk is a TV")
def test_the_results_kiosk_polls_and_glides_through_a_replay(page, event, raced):
    """After `replay-started`, results-by-racer still asks results/rounds/
    and its list still glides (D140). The wait is `RESULTS_WAIT_MS`, with
    its slack beside it."""
    page.add_init_script(RECORD)
    page.goto("/kiosk.php?page=kiosks/results-by-racer.kiosk")
    expect(page.locator("[data-result] [data-shown]").first).not_to_be_empty()
    # one read: the list's height is the server's rendering, which no poll
    # changes while no result is recorded.
    assert page.evaluate(
        "(() => { const r = document.querySelector('[data-results]');"
        " return r.querySelector('[data-rounds]').scrollHeight > r.clientHeight; })()"
    ), "the results fit the screen; the test checks no glide"
    page.evaluate("() => window.postMessage('replay-started', '*')")
    page.wait_for_function(POLLED_AND_GLIDED, timeout=RESULTS_WAIT_MS)


def first_row(page):
    return page.locator("[data-rows] > [data-racer]").first


@pytest.mark.sizes("desktop", reason="the kiosk is a TV")
def test_the_standings_kiosk_scrolls_through_a_replay(page, event, raced):
    """After `replay-started`, the standings still move a row every 2 s
    (kiosk-standings.js, D140). The page is a preview, with no kiosk poll
    whose request the clock could give up, and loads on a paused clock,
    which runs the 5 s before the first list starts, 1.5 s per row until
    every row shows, then 2 s once the message is in."""
    pause_clock_at(page, PAUSED_AT)
    page.goto("/kiosk.php?page=kiosks/standings.kiosk")
    rows = page.locator("[data-rows] > [data-racer]")
    expect(rows).to_have_count(16)
    page.clock.run_for(5000)
    for _ in range(16):
        page.clock.run_for(1500)
    expect(page.locator("[data-racer][hidden]")).to_have_count(0)
    # one read: the clock is paused, so the list stands still.
    assert page.evaluate(
        "(() => { const l = document.querySelector('[data-rows]');"
        " return l.scrollHeight > l.clientHeight + 2; })()"
    ), "the standings fit the screen; the test checks no scroll"
    before = first_row(page).inner_text()
    page.evaluate("() => window.postMessage('replay-started', '*')")
    page.clock.run_for(2000)
    expect(first_row(page)).not_to_have_text(before)
