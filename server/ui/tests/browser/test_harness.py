"""The page fixture's own rules (#42 retro)."""

import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from django.core.signals import request_started
from playwright.sync_api import expect

from ui.tests.browser import conftest
from ui.tests.browser.conftest import (
    BLOCKED,
    InFlight,
    _settle,
    check_harness,
    close_tab,
    font_config,
    stop_timers,
)
from ui.tests.browser.helpers import pause_clock_at


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


# The settle's bound on a fake-clock page (#347).
FAKE_CLOCK_SETTLE_S = 1.0


def test_the_settle_on_a_fake_clock_takes_under_a_second(page, event):
    """STOP_TIMERS cleared the native id range through the fake clock's
    `clearTimeout`, which builds an Error for each id below its own, and
    blocked the renderer for seconds, during which a resumed fake clock ran
    on toward kiosk.js's give-up (#347). Under the fake clock it now
    clears only the clock's own ids. The slack is FAKE_CLOCK_SETTLE_S less
    the settle's own time (`slack`), which the PR body measures beside a
    should-pass mutation that makes the stop late."""
    page.clock.install()
    page.route("**/held-request", answer_the_poll)
    page.goto("/login/")
    start_a_fast_poll(page)
    started = time.monotonic()
    assert _settle(page) == []
    took = time.monotonic() - started
    slack = FAKE_CLOCK_SETTLE_S - took
    assert slack > 0, f"the settle took {took:.3f} s, over FAKE_CLOCK_SETTLE_S"


def test_stop_timers_stops_a_poll_set_after_a_pending_animation_frame(page, event):
    """The fake clock's `clearTimeout` throws on the id of a pending
    `requestAnimationFrame`, so STOP_TIMERS stopped at that id and left
    every later timer running (#347). Here the frame is requested before
    the poll, on a paused clock, so it is still pending at the stop.
    `stop_timers` runs alone, as `close_tab` and `stop_the_kiosk` run it:
    the settle's request freeze would hide a timer left running."""
    pause_clock_at(page, datetime(2026, 1, 1, 12, 0))
    page.route("**/held-request", answer_the_poll)
    page.goto("/login/")
    page.evaluate(
        """() => {
          requestAnimationFrame(() => {});
          setInterval(() => fetch('/held-request'), 10);
        }"""
    )
    stop_timers(page)
    sent = []
    page.on("request", lambda r: sent.append(r))
    page.clock.run_for(1000)
    page.wait_for_timeout(100)
    # One read after the wait: the point is that nothing more was sent.
    assert sent == []


def test_the_settle_stops_a_timer_that_loads_images(page, event):
    """The settle's request freeze stops fetch and XMLHttpRequest only; a
    timer that loads an image is stopped by clearing the timers."""
    page.route("**/held-request*", answer_the_poll)
    page.goto("/login/")
    with page.expect_request("**/held-request*"):
        page.evaluate(
            """() => {
              let n = 0;
              setInterval(() => { new Image().src = '/held-request?' + n++; }, 10);
            }"""
        )
    assert _settle(page) == []
    sent = []
    page.on("request", lambda r: sent.append(r))
    page.wait_for_timeout(200)
    # One read after the wait: the point is that nothing more was sent.
    assert sent == []


def test_the_settle_stops_a_poll_set_before_the_fake_clock(page, event):
    """A poll set before `page.clock.install()` runs on a native timer,
    which STOP_TIMERS leaves running under the clock. The settle's request
    freeze stops its requests (#347 review)."""
    page.route("**/held-request", answer_the_poll)
    page.goto("/login/")
    start_a_fast_poll(page)
    page.clock.install()
    assert _settle(page) == []
    sent = []
    page.on("request", lambda r: sent.append(r))
    page.wait_for_timeout(200)
    # One read after the wait: the point is that nothing more was sent.
    assert sent == []


# Polls that send their next request only once the last reply is handled,
# each to /held-request, a frame's among them, and when each has its next
# request ready: htmx's `every` queues a trigger that comes while its
# request is out.
REPLY_POLLS = {
    "setTimeout": (
        """() => {
          const poll = () => fetch('/held-request').then(() => setTimeout(poll, 10));
          poll();
        }""",
        "() => true",
    ),
    "setInterval": (
        """() => {
          const poll = () => fetch('/held-request').then(() => setInterval(poll, 10));
          poll();
        }""",
        "() => true",
    ),
    "fetch-chain": (
        """() => {
          const poll = () => fetch('/held-request').then((r) => r.text()).then(poll);
          poll();
        }""",
        "() => true",
    ),
    "animation-frame": (
        """() => {
          let out = false;
          const loop = () => {
            if (!out) {
              out = true;
              fetch('/held-request').then(() => { out = false; });
            }
            requestAnimationFrame(loop);
          };
          requestAnimationFrame(loop);
        }""",
        "() => true",
    ),
    "frame-fetch-chain": (
        """() => {
          const frame = document.createElement('iframe');
          document.body.append(frame);
          const w = frame.contentWindow;
          const poll = () => w.fetch('/held-request').then((r) => r.text()).then(poll);
          poll();
        }""",
        "() => true",
    ),
    "htmx-every": (
        """() => {
          const poll = document.createElement('div');
          poll.id = 'poll';
          poll.setAttribute('hx-get', '/held-request');
          poll.setAttribute('hx-trigger', 'every 20ms');
          poll.setAttribute('hx-swap', 'none');
          document.body.append(poll);
          htmx.process(poll);
        }""",
        # htmx's private queue on the element (htmx-2.0.4.min.js,
        # `queuedRequests`).
        "() => document.getElementById('poll')['htmx-internal-data']"
        ".queuedRequests?.length > 0",
    ),
}


@pytest.mark.parametrize("source", REPLY_POLLS)
def test_a_reply_handled_after_the_stop_starts_no_request(
    page, page_errors, event, monkeypatch, source
):
    """A poll whose reply lands after the stop sent its next request after
    the drain came back empty: on a new timer (#317's results-by-racer
    kiosk, #347), from the reply's own handler, from an animation frame,
    and from htmx's queue, with no timer at all (#347 review). The settle
    now also stops the page's fetch and XMLHttpRequest. Here the reply is
    released from Python after the barrier, the settle's last stop."""
    held = []
    page.route("**/held-request", lambda route: held.append(route))
    page.goto("/login/")
    start, ready = REPLY_POLLS[source]
    page.evaluate(start)
    wait_until(page, lambda: held, "the poll's request")
    page.wait_for_function(ready, timeout=InFlight.TIMEOUT_MS)
    barrier = conftest._barrier

    def barrier_then_reply(tab):
        barrier(tab)
        held.pop().fulfill(status=204)

    sent = []
    page.on("request", lambda r: r.url.endswith("/held-request") and sent.append(r))
    with monkeypatch.context() as patch:
        patch.setattr(conftest, "_barrier", barrier_then_reply)
        assert _settle(page) == []
    page.wait_for_timeout(500)
    # One read after the wait: the point is that no request was tried
    # again, blocked or not.
    assert sent == []
    assert page_errors.errors == []


# What a reply handled after the stops can send by neither the page's fetch
# nor its XMLHttpRequest, each to /held-request (#409's review of PR #424,
# its probes; kiosk-awards.js sets an image in a reply's handler, kiosk.js
# navigates on one): an image, a beacon, a fetch from a frame made then,
# and a navigation to a document that polls.
LEAKS = {
    "image": "new Image().src = '/held-request?image';",
    "beacon": "navigator.sendBeacon('/held-request');",
    "new-frame": """const frame = document.createElement('iframe');
      document.body.append(frame);
      frame.contentWindow.fetch('/held-request');""",
    "navigation": "location.href = '/login/?nav';",
}


@pytest.mark.parametrize("source", LEAKS)
def test_the_settle_blocks_what_the_stops_leave(
    page, page_errors, event, monkeypatch, source
):
    """The stubs of the page's own fetch and XMLHttpRequest leave an image,
    a beacon, a frame made after them and a new document free to send; the
    settle then has Chromium block every request of the page. A frame or
    document made after the block gets the stubs too, so no fetch of its
    fails into a handler (a `pageerror` here). The reply is held until the
    block is on."""
    page.route("**/held-request*", answer_the_poll)
    first = []
    page.route(lambda url: url.endswith("?first"), lambda route: first.append(route))
    page.add_init_script(
        """if (location.search === '?nav') addEventListener('DOMContentLoaded', () => {
          fetch('/held-request');
        });"""
    )
    page.goto("/login/")
    with page.expect_request(lambda r: r.url.endswith("?first")):
        page.evaluate(
            """(leak) => {
              fetch('/held-request?first').then(() => new Function(leak)());
            }""",
            LEAKS[source],
        )
    block = conftest.block_sending
    reached = []

    def block_then_reply(tab):
        block(tab)
        page.on(
            "requestfinished",
            lambda r: not r.url.endswith("?first") and reached.append(r),
        )
        first.pop().fulfill(status=204)

    with monkeypatch.context() as patch:
        patch.setattr(conftest, "block_sending", block_then_reply)
        assert _settle(page) == []
    page.wait_for_timeout(300)
    # One read after the wait: the point is that nothing reached a reply.
    assert reached == []
    assert page_errors.errors == []


def test_a_blocked_request_before_the_settle_is_a_page_error(page, page_errors, event):
    """The page errors drop a request the settle's block failed, and only
    after the settle: the same failure before it still counts."""
    page.goto("/login/")
    session = page.context.new_cdp_session(page)
    session.send("Network.enable")
    session.send("Network.setBlockedURLs", {"urls": ["*held-request"]})
    with page.expect_event("requestfailed"):
        page.evaluate("() => { fetch('/held-request').catch(() => {}); }")
    session.detach()
    url = f"{page.url.rsplit('/', 2)[0]}/held-request"
    assert page_errors.errors == [f"request failed: {url} {BLOCKED[0]}"]
    page_errors.errors.clear()


def test_the_block_outlasts_the_settle(page, event):
    """The block ends with its CDP session (#409, PR #424), so the session
    stays open after the settle returns: an image the page asks for later
    is still blocked."""
    page.route("**/held-request*", answer_the_poll)
    page.goto("/login/")
    assert _settle(page) == []
    page.wait_for_timeout(100)
    with page.expect_event("requestfailed") as failed:
        page.evaluate("() => { new Image().src = '/held-request?later'; }")
    assert failed.value.failure == BLOCKED[0]


def test_another_failure_after_the_settle_is_a_page_error(
    page, page_errors, event, monkeypatch
):
    """After the settle the page errors drop the block's failures only: a
    request sent before the block that fails otherwise still counts."""
    held = []
    page.route("**/held-request", lambda route: held.append(route))
    page.goto("/login/")
    with page.expect_request("**/held-request"):
        page.evaluate("() => { fetch('/held-request').catch(() => {}); }")
    block = conftest.block_sending

    def block_then_abort(tab):
        block(tab)
        held.pop().abort()

    with monkeypatch.context() as patch:
        patch.setattr(conftest, "block_sending", block_then_abort)
        assert _settle(page) == []
    url = f"{page.url.rsplit('/', 2)[0]}/held-request"
    wait_until(page, lambda: len(page_errors.errors) > 1, "the console error")
    assert sorted(page_errors.errors) == [
        "console: Failed to load resource: net::ERR_FAILED",
        f"request failed: {url} net::ERR_FAILED",
    ]
    page_errors.errors.clear()


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


SERVER_DIR = Path(__file__).resolve().parents[3]
# #282's command: a test outside browser/ named between two browser files.
SPLIT_ARGS = [
    "ui/tests/browser/test_shell.py",
    "ui/tests/test_kiosk_screens_tool.py",
    "ui/tests/browser/test_kiosk_names.py",
]


def test_a_browser_file_named_after_one_outside_keeps_the_harness():
    """pytest 9.1 bound a conftest's fixtures to the first collector of its
    folder, so a browser file named after a file outside browser/ lost all
    of them (pytest-dev/pytest#14635, fixed in 9.2.0): no phone and desktop
    sizes, and no `_async_unsafe`, so every test errored at setup with
    `SynchronousOnlyOperation` (#282). `--setup-plan` collects and plans the
    fixtures without running a test, in its own process, as #282's command
    was run."""
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "browser",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "--setup-plan",
            "-k",
            "test_long_names_show_whole",
            *SPLIT_ARGS,
        ],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    names = "ui/tests/browser/test_kiosk_names.py::test_long_names_show_whole"
    assert f"{names}[phone-chromium]" in out
    assert f"{names}[desktop-chromium]" in out
    assert re.search(r"^ *SETUP +S _async_unsafe$", out, re.M), out


def collected(path, *fixtures):
    return SimpleNamespace(
        fspath=path,
        nodeid=f"{path.name}::t",
        fixturenames=fixtures,
        add_marker=lambda mark: None,
    )


def test_a_browser_test_without_the_harness_fails_the_run():
    """`check_harness` turns #282's setup errors into one usage error at
    collection, for a test under browser/ only, and names the first test
    that lost the harness."""
    here = Path(__file__).parent
    kept = collected(here / "test_a.py", "page", "_async_unsafe")
    lost = collected(here / "test_b.py", "page")
    later = collected(here / "test_d.py", "page")
    outside = collected(here.parent / "test_c.py", "page")
    check_harness([kept, outside], here)
    with pytest.raises(pytest.UsageError, match=r"^test_b\.py::t lost .*#14635"):
        check_harness([kept, outside, lost, later], here)


def test_the_collection_hook_runs_the_harness_check():
    """The browser conftest's `pytest_collection_modifyitems` calls
    `check_harness` on the items it marks, so a collection that lost the
    harness stops there."""
    from ui.tests.browser import conftest

    here = Path(conftest.__file__).parent
    with pytest.raises(pytest.UsageError, match=r"^test_b\.py::t lost "):
        conftest.pytest_collection_modifyitems([collected(here / "test_b.py", "page")])


def wait_until(page, check, what):
    """Wait until `check()` holds, reading Playwright's events every 20 ms,
    for at most `InFlight.TIMEOUT_MS`."""
    for _ in range(InFlight.TIMEOUT_MS // 20):
        if check():
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"{what} not within {InFlight.TIMEOUT_MS} ms")


class Sent:
    """A stand-in request of `resource_type`, sent by `frame`."""

    def __init__(self, frame, resource_type):
        self.frame = frame
        self.resource_type = resource_type


class RefusedFrame:
    """A request whose `frame` Playwright refuses, as it does a
    `target="_blank"` popup's navigation request."""

    def __init__(self, resource_type):
        self.resource_type = resource_type

    @property
    def frame(self):
        from playwright.sync_api import Error

        raise Error("Frame for this navigation request is not available")


# A page with an iframe and a `target="_blank"` link to a popup.
FRAME_AND_BLANK_LINK = """() => {
  const frame = document.createElement('iframe');
  frame.src = '/frame-target?first';
  document.body.append(frame);
  const link = document.createElement('a');
  link.href = '/popup-target';
  link.target = '_blank';
  link.id = 'blank-link';
  link.textContent = 'Open';
  document.body.prepend(link);
}"""
MOVE_THE_IFRAME = (
    "() => { document.querySelector('iframe').src = '/frame-target?second'; }"
)


def test_a_navigation_keeps_a_request_whose_frame_is_refused(page, event):
    """When a frame navigates, the harness drops that frame's own requests
    of the types in `InFlight.ORPHANED`, and keeps its document request,
    another frame's request and a request whose frame Playwright refuses
    (#378). Only a navigation request has its frame refused, and that is a
    document, which `ORPHANED` never holds
    (`test_a_blank_link_followed_while_a_frame_navigates_is_tracked`), so
    these requests are stand-ins, put in the page's set while its iframe
    navigates."""
    page.route(
        "**/frame-target?*",
        lambda route: route.fulfill(body="<p>frame</p>", content_type="text/html"),
    )
    page.goto("/login/")
    with page.expect_event(
        "framenavigated", lambda f: f.url.endswith("/frame-target?first")
    ):
        page.evaluate(FRAME_AND_BLANK_LINK)
    iframe = next(f for f in page.frames if f.url.endswith("/frame-target?first"))
    own = Sent(iframe, "fetch")
    document = Sent(iframe, "document")
    others = Sent(page.main_frame, "fetch")
    refused = RefusedFrame("fetch")
    stand_ins = {own, document, others, refused}
    page.in_flight.requests |= stand_ins
    try:
        with page.expect_event(
            "framenavigated", lambda f: f.url.endswith("/frame-target?second")
        ):
            page.evaluate(MOVE_THE_IFRAME)
        left = page.in_flight.requests & stand_ins
    finally:
        # The drain after the test names what is left, and a stand-in has
        # no method or url.
        page.in_flight.requests -= stand_ins
    assert left == {document, others, refused}


def test_a_blank_link_followed_while_a_frame_navigates_is_tracked(page, event):
    """A `target="_blank"` link sends the popup's navigation request before
    Playwright has a page for the popup, and `request.frame` raises for that
    request. While it was out, the opener's iframe navigated, and
    `InFlight.navigated`, which read every request's frame, raised from the
    `framenavigated` listener; the test's next call failed with it (#344's
    Preview test, whose thumbnails swap; #378). The harness now keeps
    tracking that request, and drains it once the popup's page loads. It is
    a document request, and the opener's own `request` listener never sees
    it, since Playwright gives it no page; so a recorder there that reads
    `request.frame`, as `test_kiosk_thumbnails.py`'s does, never meets it."""
    held = []
    page.context.route("**/popup-target", lambda route: held.append(route))
    page.route(
        "**/frame-target?*",
        lambda route: route.fulfill(body="<p>frame</p>", content_type="text/html"),
    )
    on_the_opener = []
    page.on("request", lambda r: on_the_opener.append(r.url))
    page.goto("/login/")
    with page.expect_event(
        "framenavigated", lambda f: f.url.endswith("/frame-target?first")
    ):
        page.evaluate(FRAME_AND_BLANK_LINK)
    page.click("#blank-link")
    wait_until(page, lambda: held, "the popup's request")
    assert len(held) == 1
    request = held[0].request
    assert request.resource_type == "document"
    assert request.is_navigation_request()
    # The popup has no page yet while its request is held.
    assert page.context.pages == [page]
    page.evaluate(MOVE_THE_IFRAME)
    wait_until(
        page,
        lambda: any(f.url.endswith("/frame-target?second") for f in page.frames),
        "the iframe's navigation",
    )
    assert [r.url for r in page.in_flight.requests if "popup-target" in r.url] == [
        request.url
    ]
    # No tab is known to have sent it, and asking does not raise.
    assert request not in page.in_flight.of(page)
    with page.context.expect_page() as opened:
        held[0].fulfill(body="<p>popup</p>", content_type="text/html")
    popup = opened.value
    expect(popup.locator("p")).to_have_text("popup")
    assert page.in_flight.drain(page) == []
    assert not [url for url in on_the_opener if "popup-target" in url]
    close_tab(page, popup)


def test_the_barrier_blocked_before_the_settle_is_a_page_error(
    page, page_errors, event
):
    """The page errors drop the settle's blocked barrier, and only once the
    settle sent it: the same failure before then still counts."""
    page.goto("/login/")
    session = page.context.new_cdp_session(page)
    session.send("Network.enable")
    session.send("Network.setBlockedURLs", {"urls": [f"*{conftest.SETTLE_BARRIER}"]})
    with page.expect_event("requestfailed"):
        page.evaluate(
            "(url) => { fetch(url).catch(() => {}); }", conftest.SETTLE_BARRIER
        )
    session.detach()
    url = f"{page.url.rsplit('/', 2)[0]}{conftest.SETTLE_BARRIER}"
    assert page_errors.errors == [f"request failed: {url} {BLOCKED[0]}"]
    page_errors.errors.clear()


# The commit and the barrier race, and the barrier lost in most runs before
# #347's fix, not in every run: each size runs the test this many times.
COMMIT_RACES = 3


@pytest.mark.parametrize("attempt", range(COMMIT_RACES))
def test_the_settle_sees_every_request_of_a_page_that_commits_as_it_starts(
    page, page_errors, event, monkeypatch, attempt
):
    """The settle ran while a navigation was out: a test that failed in
    `log_in` with the login POST held (#347 gap 1). Its stop waits for the
    new document's commit, and the barrier's `page.route` then turned
    Playwright's interception on as the commit landed. The new document's
    requests then reached the server with no `request` event, the barrier's
    among them: a 404 and a 10 s wait for its end (#316's log). Here the
    server holds a navigation until the settle's first stop. The settle
    now blocks the barrier, which never reaches the server, and every
    request the server gets after the release has its `request` event."""
    gate = threading.Event()
    held = []
    after = []

    def hold(sender, environ, **kwargs):
        if environ.get("QUERY_STRING") == "held":
            held.append(environ["PATH_INFO"])
            gate.wait(InFlight.TIMEOUT_MS / 1000)
        elif gate.is_set():
            after.append(environ["PATH_INFO"])

    request_started.connect(hold, weak=False)
    try:
        page.goto("/login/?all")
        seen = []
        page.on(
            "request", lambda r: gate.is_set() and seen.append(urlsplit(r.url).path)
        )
        page.evaluate("() => { location.href = '/login/?held'; }")
        wait_until(page, lambda: held, "the held navigation at the server")
        stop = conftest.stop_timers

        def release_then_stop(tab):
            gate.set()
            stop(tab)

        with monkeypatch.context() as patch:
            patch.setattr(conftest, "stop_timers", release_then_stop)
            assert _settle(page) == []
    finally:
        gate.set()
        request_started.disconnect(hold)
    assert conftest.SETTLE_BARRIER not in after
    wait_until(
        page,
        lambda: not Counter(after) - Counter(seen),
        f"a request event for each of {after} (got {seen})",
    )
    assert page_errors.errors == []
