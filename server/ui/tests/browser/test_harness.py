"""The page fixture's own rules (#42 retro)."""

import os
import subprocess

import pytest
from playwright.sync_api import expect

from ui.tests.browser.conftest import InFlight, _settle, close_tab, font_config


def test_an_aborted_htmx_request_is_no_page_error(page, page_errors, event):
    """htmx logs an abort as console errors. A poll that a control cuts short
    failed #42's browser tests at random until the fixture learned the pair."""
    page_errors.allow(r"/never-answers/ net::ERR_ABORTED$")
    page.route("**/never-answers/", lambda route: None)
    page.goto("/login/")
    page.evaluate(
        """() => {
          const d = document.createElement('div');
          d.id = 'slow';
          d.setAttribute('hx-get', '/never-answers/');
          d.setAttribute('hx-trigger', 'go');
          document.body.append(d);
          htmx.process(d);
          htmx.trigger(d, 'go');
        }"""
    )
    expect(page.locator("#slow.htmx-request")).to_have_count(1)
    with page.expect_console_message(lambda m: m.text == "htmx:sendAbort"):
        page.evaluate("() => htmx.trigger('#slow', 'htmx:abort')")
    assert page_errors.aborts == 1


# A request of each type in `InFlight.ORPHANED`, sent to a route that never
# answers.
HELD = {
    "xhr": """() => { const x = new XMLHttpRequest();
      x.open('GET', '/held-request'); x.send(); }""",
    "fetch": "() => { fetch('/held-request').catch(() => {}); }",
    "image": """() => { const img = document.createElement('img');
      img.src = '/held-request'; document.body.append(img); }""",
    "script": """() => { const script = document.createElement('script');
      script.src = '/held-request'; document.body.append(script); }""",
    "stylesheet": """() => { const link = document.createElement('link');
      link.rel = 'stylesheet'; link.href = '/held-request';
      document.head.append(link); }""",
}


@pytest.mark.parametrize("kind", HELD)
def test_a_navigation_drops_a_request_still_loading(page, page_errors, event, kind):
    """Playwright reports no end for a request a navigation cut off, so the
    drain after the test waited 10 s on it and failed: an image left loading
    by a link (#47 retro), the timer's scripts when the Web Serial guard
    leaves `/timer.php` (#50, seen in CI on #52's PR). Each type the harness
    drops gets its own held request here (#52 retro: script and stylesheet
    were listed with no test)."""
    page_errors.allow(r"/held-request net::ERR_ABORTED$")
    page.route("**/held-request", lambda route: None)
    page.goto("/login/")
    with page.expect_request(
        lambda r: r.url.endswith("/held-request") and r.resource_type == kind
    ):
        page.evaluate(HELD[kind])
    page.goto("/login/")
    assert not [r for r in page.in_flight.requests if "held-request" in r.url]


def test_every_type_the_harness_drops_has_a_held_request():
    assert sorted(HELD) == sorted(InFlight.ORPHANED)


def test_a_tab_closed_with_a_request_out_is_named(page, event):
    """A tab closed with its request out left that request in the set with
    no end, and the drain failed 10 s later with no hint why (the camera tab
    in PR #205's ui (postgres)). The harness now names it."""
    page.goto("/login/")
    tab = page.context.new_page()
    tab.route("**/held-request", lambda route: None)
    tab.goto("/login/")
    with tab.expect_request("**/held-request"):
        tab.evaluate(HELD["fetch"])
    tab.close()
    assert not page.in_flight.of(tab)
    assert page.in_flight.closed_with == [
        f"GET {page.url.rsplit('/', 2)[0]}/held-request"
    ]
    page.in_flight.closed_with.clear()


def test_close_tab_waits_for_the_request_out(page, event):
    """`close_tab()` waits for a held request's reply before the tab
    closes, so nothing is left to name."""
    page.goto("/login/")
    tab = page.context.new_page()
    held = []
    tab.route("**/held-request", lambda route: held.append(route))
    tab.goto("/login/")
    with tab.expect_request("**/held-request"):
        tab.evaluate(HELD["fetch"])
    for _ in range(200):
        if held:
            break
        tab.wait_for_timeout(20)
    held[0].fulfill(status=204)
    close_tab(page, tab)
    assert page.in_flight.closed_with == []


def answer_the_poll(route):
    """A 204, as the port's polls answer when nothing changed. An empty 200
    that the page never reads fails with `net::ERR_ABORTED` while the page
    is still open (`test_an_unread_empty_200_is_an_abort`), so a poll the
    settle tests answered that way failed them at teardown (#224)."""
    route.fulfill(status=204)


def start_a_fast_poll(page):
    """Start a 10 ms poll, and return once its first request is out, so the
    settle always meets a poll that has already sent. Without that wait,
    whether the poll had sent first was down to timing: with the empty 200
    above the settle tests failed only a few load runs in 50, where with
    the wait they fail every run (#224)."""
    with page.expect_request("**/held-request"):
        page.evaluate("() => setInterval(() => fetch('/held-request'), 10)")


def test_an_unread_empty_200_is_an_abort(page, page_errors, event):
    """Chromium ends an empty 200 that the page never reads with
    `net::ERR_ABORTED`, while the page is still open, and a 204 that the
    page never reads finishes (#224). Hence `answer_the_poll`."""
    page_errors.allow(r"/empty-200 net::ERR_ABORTED$")
    page.route("**/empty-200", lambda route: route.fulfill(body=""))
    page.route("**/held-request", answer_the_poll)
    page.goto("/login/")
    with page.expect_event(
        "requestfailed", lambda r: r.url.endswith("/empty-200")
    ) as failed:
        page.evaluate("() => { fetch('/empty-200'); }")
    assert failed.value.failure == "net::ERR_ABORTED"
    with page.expect_event(
        "requestfinished", lambda r: r.url.endswith("/held-request")
    ):
        page.evaluate("() => { fetch('/held-request'); }")


def test_the_settle_after_a_test_stops_a_fast_poll(page, event):
    """A poll faster than the drain's 50 ms check could start a request
    after the drain and meet the database flush (the replay page's 250 ms
    poll, a Postgres deadlock in #53). The settle stops every timer first."""
    page.route("**/held-request", answer_the_poll)
    page.goto("/login/")
    sent = []
    page.on("request", lambda r: sent.append(r))
    start_a_fast_poll(page)
    assert _settle(page) == []
    after = len(sent)
    page.wait_for_timeout(200)
    # One read after the wait: the point is that nothing more was sent.
    assert len(sent) == after


def test_the_settle_drains_a_request_sent_as_it_stops(page, event, monkeypatch):
    """A poll's last tick can send in the task that stops the timers, and
    its `request` event reach Python after the stop returns. The drain then
    found nothing out and returned (1 run in 50 under load, #224 retro).
    Here the tick is held in that task: `clearTimeout` sends first."""
    held = []
    page.route("**/held-request", lambda route: held.append(route))
    page.goto("/login/")
    page.evaluate(
        """() => {
          const clear = window.clearTimeout;
          window.clearTimeout = (id) => {
            window.clearTimeout = clear;
            fetch('/held-request');
            clear(id);
          };
        }"""
    )
    monkeypatch.setattr(InFlight, "TIMEOUT_MS", 300)
    assert _settle(page) == [f"GET {page.url.rsplit('/', 2)[0]}/held-request"]
    held[0].fulfill(status=204)
    assert page.in_flight.drain(page) == []


def test_the_settle_sends_no_barrier_from_a_blank_page(page, monkeypatch):
    """A page that never left `about:blank` has no server to fetch from; a
    barrier there would wait out its timeout."""
    monkeypatch.setattr(InFlight, "TIMEOUT_MS", 300)
    sent = []
    page.on("request", lambda r: sent.append(r))
    assert _settle(page) == []
    assert sent == []


def test_the_settle_stops_the_polls_of_a_page_on_a_fake_clock(page, event):
    """Playwright's clock numbers its timers from 1e12, so clearing every id
    from 0 spun the renderer for hours and hung the whole run (test_toasts,
    #53 retro). The settle clears only the ids a page can hold."""
    page.clock.install()
    page.route("**/held-request", answer_the_poll)
    page.goto("/login/")
    start_a_fast_poll(page)
    assert _settle(page) == []
    sent = []
    page.on("request", lambda r: sent.append(r))
    page.clock.run_for(1000)
    page.wait_for_timeout(100)
    # One read after the wait: the point is that nothing more was sent.
    assert sent == []


# kiosk.css's font stack, and the name of the font Chromium draws it in.
KIOSK_FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


def drawn_font(page) -> list[str]:
    page.set_content(f"<p style='font-family: {KIOSK_FONT}'>Heat 100</p>")
    cdp = page.context.new_cdp_session(page)
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    root = cdp.send("DOM.getDocument")["root"]["nodeId"]
    node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": "p"})
    fonts = cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]})
    return [font["familyName"] for font in fonts["fonts"]]


@pytest.mark.parametrize("family", ["DejaVu Sans", "Liberation Sans"])
def test_a_forced_font_draws_the_kiosk(
    browser_type, browser_type_launch_args, tmp_path, family
):
    """`DERBYNET_BROWSER_FONT` draws kiosk.css's stack in the font it names,
    whatever this machine's own sans-serif is. Two families, so one of them
    differs from the machine's default."""
    env = font_config(family, tmp_path)
    browser = browser_type.launch(**{**browser_type_launch_args, "env": env})
    try:
        assert drawn_font(browser.new_page()) == [family]
    finally:
        browser.close()


def test_a_font_not_installed_is_refused(tmp_path):
    """fontconfig falls back without a word, so a misspelt family would run
    the suite in this machine's font."""
    with pytest.raises(pytest.UsageError, match="not installed"):
        font_config("No Such Font Sans", tmp_path)


def test_the_page_draws_the_font_asked_for(page):
    """The `page` fixture draws kiosk.css's stack in `DERBYNET_BROWSER_FONT`
    when it is set, and in this machine's fontconfig sans-serif when not."""
    family = (
        os.environ.get("DERBYNET_BROWSER_FONT")
        or subprocess.run(
            ["fc-match", "-f", "%{family[0]}", "sans-serif"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert drawn_font(page) == [family]
