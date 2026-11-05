"""The page fixture's own rules (#42 retro)."""

import pytest
from playwright.sync_api import expect

from ui.tests.browser.conftest import InFlight


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
