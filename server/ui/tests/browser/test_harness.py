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
