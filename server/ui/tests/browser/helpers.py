"""Page helpers the browser tests and `tools/race_day_screens.py` share
(#61 review): waiting on htmx, closing toasts, opening a settings section,
pausing the page's fake clock (#348).

This module imports no Django, so a tool run outside pytest imports it too.
"""

from datetime import timedelta

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import expect


def htmx_settled(page):
    """Wait until htmx has no request in flight and has settled every swap.
    The page settles at once (`defaultSettleDelay` 0), so a swapped-in
    control is bound as soon as it shows, and every swap is checked for that
    (UNBOUND_AFTER_SWAP); wait for this before reading what a request
    changed."""
    page.wait_for_function(
        "!document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


def dismiss_toasts(page):
    """Close the toasts, as a race coordinator would, before tapping a
    control they cover."""
    toasts = page.locator("#toasts").get_by_role("button", name="Dismiss")
    while toasts.count():
        toasts.first.click()


# True once htmx has processed every element under `root` (and `root`) that
# makes a request: htmx 2 sets `initHash` on an element's internal data when
# it binds the element's `hx-*` attributes.
BOUND = """root => [root, ...root.querySelectorAll('[hx-get], [hx-post]')]
  .filter((e) => e.matches('[hx-get], [hx-post]'))
  .every((e) => (e['htmx-internal-data'] || {}).initHash)"""

# GONE once a swap has taken `root` out of the page, true once BOUND holds
# for it: htmx's cleanup of a swapped-out element deletes its internal data
# but `firstInitCompleted` (htmx 2.0.4 `deInitNode`), so BOUND never holds
# for it again (#425).
GONE = "gone"
BOUND_OR_GONE = f"""root => !root.isConnected ? '{GONE}' : ({BOUND})(root)"""

# How many swaps `bound()` follows before it fails, so a locator that
# resolves to a new element each time cannot keep it waiting (#466 review).
MAX_SWAPS = 5


def bound(page, locator):
    """Wait until htmx has bound the requests of what `locator` shows. Alpine
    shows swapped-in content at once, but htmx binds its `hx-*` attributes
    only when the swap settles, and a control used before then sends
    nothing (#46 CI: the award sheet's "For"
    select never fetched the partition list). No settling class marks every
    such swap, so `htmx_settled()` alone does not cover it. A poll's swap
    during the wait takes out the element `locator` showed, and the wait
    follows `locator` to the element swapped in
    (`test_a_tap_follows_a_card_a_poll_swaps_while_bound_waits`), for up to
    MAX_SWAPS swaps. Each wait takes Playwright's timeout. Past the timeout
    or the swaps, it fails naming `locator` and which bound it hit
    (`test_bound_follows_swaps_up_to_its_cap`,
    `test_bound_names_the_control_it_timed_out_on`)."""
    for swaps in range(MAX_SWAPS + 1):
        try:
            found = page.wait_for_function(BOUND_OR_GONE, arg=locator.element_handle())
        except PlaywrightTimeout as timeout:
            raise AssertionError(
                f"bound(): {locator} showed no element htmx bound within "
                f"Playwright's timeout, after {swaps} swaps"
            ) from timeout
        if found.json_value() != GONE:
            return
    raise AssertionError(
        f"bound(): {locator} was swapped out {MAX_SWAPS + 1} times and never bound"
    )


def ready(page, body):
    """`body`, a sheet body just loaded, once its controls are bound."""
    expect(body).to_be_visible()
    bound(page, body)
    return body


def open_section(page, key):
    """The settings page's `<details>` section `key`, opened."""
    section = page.locator(f'details[data-section="{key}"]')
    if section.get_attribute("open") is None:
        section.locator("summary").click()
    expect(section).to_have_attribute("open", "")
    return section


def pause_clock_at(page, at):
    """Pause Playwright's clock at `at` (a datetime), on a page that has not
    navigated (about:blank), so the page loads on the paused clock. It
    refuses any other page (`test_pause_clock_at_refuses_a_page_that_has_loaded`
    in `test_kiosk_scale_mph.py`).

    The clock runs from its install, so `pause_at(at)` after an install at
    `at` itself failed "Cannot fast-forward to the past" once the two calls
    were more than a moment apart, as under load (#317, #348). Installed a
    minute early, the pause moves forward unless the two calls are a minute
    or more apart (`test_the_clock_pauses_after_a_slow_install` in
    `test_kiosk_results_by_racer.py` and `test_kiosk_scale_mph.py`)."""
    assert page.url == "about:blank", f"pause_clock_at on a loaded page: {page.url}"
    page.clock.install(time=at - timedelta(minutes=1))
    page.clock.pause_at(at)


# Posts `message` to the page and resolves once the page's own listeners
# have run: this listener is added after theirs, and listeners run in the
# order they were added.
POST_MESSAGE = """(message) => new Promise((done) => {
  window.addEventListener('message', () => done(), {once: true});
  window.postMessage(message, '*');
})"""


def post_message(page, message):
    """Post `message` to `page`, as replay.js posts to its kiosk frame, and
    return once the page has handled it."""
    page.evaluate(POST_MESSAGE, message)
