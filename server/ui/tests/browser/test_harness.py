"""The page fixture's own rules (#42 retro)."""

from playwright.sync_api import expect


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


def test_a_navigation_drops_an_image_still_loading(page, event):
    """Playwright reports no end for an image request a navigation cut off,
    so the drain after the test waited 10 s on it and failed (#47 retro,
    a thumbnail left loading by a link to another page)."""
    page.route("**/held-image.png", lambda route: None)
    page.goto("/login/")
    with page.expect_request("**/held-image.png"):
        page.evaluate(
            """() => {
              const img = document.createElement('img');
              img.src = '/held-image.png';
              document.body.append(img);
            }"""
        )
    page.goto("/login/")
    assert not [r for r in page.in_flight.requests if "held-image" in r.url]


def test_a_navigation_drops_a_script_still_loading(page, event):
    """The Web Serial guard on `/timer.php` leaves the page while the
    preload scanner still fetches the timer's scripts, and Playwright
    reports no end for them, so the drain waited 10 s on them (#50, seen
    in CI on #52's PR when the route that refused them answered late)."""
    page.route("**/held-script.js", lambda route: None)
    page.goto("/login/")
    with page.expect_request("**/held-script.js"):
        page.evaluate(
            """() => {
              const script = document.createElement('script');
              script.src = '/held-script.js';
              document.body.append(script);
            }"""
        )
    page.goto("/login/")
    assert not [r for r in page.in_flight.requests if "held-script" in r.url]
