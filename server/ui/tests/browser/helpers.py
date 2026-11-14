"""Page helpers the browser tests and `tools/race_day_screens.py` share
(#61 review): waiting on htmx, closing toasts, opening a settings section.

This module imports no Django, so a tool run outside pytest imports it too.
"""

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


def bound(page, locator):
    """Wait until htmx has bound the requests of what `locator` shows. Alpine
    shows swapped-in content at once, but htmx binds its `hx-*` attributes
    only when the swap settles, 20 ms later or more under load, and a
    control used before then sends nothing (#46 CI: the award sheet's "For"
    select never fetched the partition list). No settling class marks every
    such swap, so `htmx_settled()` alone does not cover it."""
    page.wait_for_function(BOUND, arg=locator.element_handle())


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
