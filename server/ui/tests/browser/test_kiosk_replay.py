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

# The page records the message the test posts, `replay-started`, with
# the list's glide offset at that moment, and the time of each fetch.
RECORD = """window.__fetches = [];
window.addEventListener('message', () => {
  const rounds = document.querySelector('[data-rounds]');
  window.__started = {at: performance.now(), transform: rounds.style.transform};
});
const send = window.fetch;
window.fetch = function (...args) {
  window.__fetches.push(performance.now());
  return send.apply(this, args);
};"""

# After the message: a poll's fetch.
POLLED = """() => {
  const started = window.__started;
  return window.__fetches.some((at) => at > started.at);
}"""

# After the message: the list moved.
GLIDED = """() => {
  const started = window.__started;
  return document.querySelector('[data-rounds]').style.transform !== started.transform;
}"""

# After the message a poll goes out at most one poll's wait (1 s,
# kiosk-results.js) after the reply of one still out, and the glide steps
# every 40 ms: slack 4000 ms less that reply for POLLED, and 4960 ms for
# GLIDED.
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
    """After `replay-started`, results-by-racer still polls (results/rounds/
    and the intermission, both every second) and its list still glides
    (D140). Each wait is `RESULTS_WAIT_MS`, with its slack beside it."""
    page.add_init_script(RECORD)
    page.goto("/kiosk.php?page=kiosks/results-by-racer.kiosk")
    expect(page.locator("[data-result] [data-shown]").first).not_to_be_empty()
    # one read: the list's height is the server's rendering, which no poll
    # changes while no result is recorded.
    assert page.evaluate(
        "(() => { const r = document.querySelector('[data-results]');"
        " return r.querySelector('[data-rounds]').scrollHeight > r.clientHeight; })()"
    ), "the results fit the screen; the test checks no glide"
    # The evaluate resolves once the page has recorded the message.
    page.evaluate(
        """() => new Promise((done) => {
          window.addEventListener('message', () => done());
          window.postMessage('replay-started', '*');
        })"""
    )
    page.wait_for_function(POLLED, timeout=RESULTS_WAIT_MS)
    page.wait_for_function(GLIDED, timeout=RESULTS_WAIT_MS)


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
