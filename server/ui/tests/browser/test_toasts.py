"""app.js behavior the README states: toast timing, the client-side error
toasts, and SortableJS loading only where a list needs it (#40)."""

import pytest
from playwright.sync_api import expect


@pytest.fixture
def gallery(settings, page, event):
    settings.ENABLE_TEST_ENDPOINTS = True
    page.clock.install()
    page.goto("/components/")
    page.wait_for_load_state("networkidle")
    return page


def pass_ben(gallery):
    gallery.locator('[data-demo="toast"]').click()
    toast = gallery.locator("#toasts [role=status]")
    expect(toast).to_have_count(1)
    return toast


def test_an_info_toast_leaves_after_8_seconds(gallery):
    toast = pass_ben(gallery)

    gallery.clock.run_for(7000)
    expect(toast).to_have_count(1)
    gallery.clock.run_for(1500)
    expect(toast).to_have_count(0)


def test_an_error_toast_stays_until_closed(gallery, page_errors):
    page_errors.allow(r"422")
    gallery.locator('[data-demo="refuse"]').click()
    toast = gallery.locator("#toasts [role=alert]")
    expect(toast).to_have_count(1)

    gallery.clock.run_for(60_000)

    expect(toast).to_have_count(1)
    toast.get_by_role("button", name="Dismiss").click()
    expect(toast).to_have_count(0)


def test_a_toast_waits_while_it_has_focus(gallery):
    toast = pass_ben(gallery)

    toast.get_by_role("button", name="Undo").focus()
    gallery.clock.run_for(20_000)
    expect(toast).to_have_count(1)

    gallery.locator('[data-demo="sheet"]').focus()
    gallery.clock.run_for(8500)
    expect(toast).to_have_count(0)


def test_a_toast_waits_while_the_pointer_is_on_it(gallery):
    if gallery.size == "phone":
        pytest.skip("a phone has no hover")
    toast = pass_ben(gallery)

    toast.locator("[data-message]").hover()
    gallery.clock.run_for(20_000)
    expect(toast).to_have_count(1)

    gallery.mouse.move(5, 5)
    gallery.clock.run_for(8500)
    expect(toast).to_have_count(0)


def test_a_network_failure_shows_a_toast(gallery, page_errors):
    page_errors.allow(r"demo/refuse|ERR_FAILED|htmx:sendError|htmx:afterRequest")
    gallery.route("**/components/demo/refuse/", lambda route: route.abort())

    gallery.locator('[data-demo="refuse"]').click()

    expect(gallery.locator("#toasts [role=alert]")).to_contain_text(
        "The server did not answer."
    )


def test_a_server_failure_shows_a_toast(gallery, page_errors):
    page_errors.allow(r"500")
    gallery.route(
        "**/components/demo/refuse/", lambda route: route.fulfill(status=500, body="")
    )

    gallery.locator('[data-demo="refuse"]').click()

    expect(gallery.locator("#toasts [role=alert]")).to_contain_text(
        "The server failed (HTTP 500)."
    )


def test_sortablejs_loads_only_on_a_page_with_a_sortable_list(settings, page, event):
    settings.ENABLE_TEST_ENDPOINTS = True
    loaded = []
    page.on("request", lambda r: "sortable-" in r.url and loaded.append(r.url))

    page.goto("/")
    page.wait_for_load_state("networkidle")
    assert loaded == []
    assert page.evaluate("typeof window.Sortable") == "undefined"

    page.goto("/components/")
    page.wait_for_function("window.Sortable !== undefined")
    assert len(loaded) == 1


COVERED = """sheet => [...sheet.querySelectorAll('button')]
    .filter(b => b.offsetParent !== null)
    .map(b => {
        const r = b.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        const hit = document.elementFromPoint(x, y);
        return hit && !b.contains(hit) ? b.textContent.trim() || b.ariaLabel : null;
    })
    .filter(Boolean)"""


def test_a_toast_leaves_an_open_sheets_buttons_uncovered(gallery):
    """A toast shown while a sheet is open (an error, or the 8 s of an Undo)
    sat over a bottom sheet's buttons on a phone, so a tap on Delete or Back
    hit the toast (#47 retro). A phone's toasts move to the top while a
    sheet is open, and back to the bottom once it closes."""
    gallery.locator('[data-demo="confirm"]').click()
    sheet = gallery.locator('[data-sheet="gallery-confirm"]')
    expect(sheet).to_be_visible()
    gallery.evaluate("derbynet.toast('A refusal with a message long enough to wrap')")
    expect(gallery.locator("#toasts [role=alert]")).to_have_count(1)

    assert sheet.evaluate(COVERED) == []

    gallery.keyboard.press("Escape")
    expect(sheet).to_be_hidden()
    toast = gallery.locator("#toasts [role=alert]").bounding_box()
    assert toast["y"] > gallery.viewport_size["height"] / 2
