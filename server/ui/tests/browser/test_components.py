"""The #39 components behave as the spec says, on the gallery page (#40)."""

import pytest
from playwright.sync_api import expect

from ui.tests.browser.conftest import assert_accessible, shoot


@pytest.fixture
def gallery(page, event, settings):
    settings.ENABLE_TEST_ENDPOINTS = True
    page.goto("/components/")
    page.wait_for_load_state("networkidle")
    return page


def test_the_gallery_is_accessible(gallery):
    shoot(gallery, "components")
    assert_accessible(gallery)


def test_a_switch_that_saves_stays_flipped(gallery):
    switch = gallery.get_by_role("switch", name="Saves (204)")

    with gallery.expect_response("**/components/demo/ok/") as response:
        switch.click()

    assert response.value.status == 204
    assert response.value.request.post_data == "value=1"
    expect(switch).to_have_attribute("aria-checked", "true")


def test_a_refused_switch_flips_back_with_an_error_toast(gallery, page_errors):
    page_errors.allow(r"422")
    switch = gallery.get_by_role("switch", name="Refused (422), flips back")

    switch.click()

    expect(gallery.locator("#toasts [role=alert]")).to_contain_text(
        "The server refused the change."
    )
    expect(switch).to_have_attribute("aria-checked", "false")


def test_a_422_goes_to_the_toasts_whatever_the_target(gallery, page_errors):
    page_errors.allow(r"422")

    gallery.locator('[data-demo="refuse"]').click()

    expect(gallery.locator("#toasts [role=alert]")).to_have_count(1)
    gallery.locator("#toasts").get_by_role("button", name="Dismiss").click()
    expect(gallery.locator("#toasts [role=alert]")).to_have_count(0)


def test_a_toast_with_undo(gallery):
    gallery.locator('[data-demo="toast"]').click()
    toast = gallery.locator("#toasts [role=status]")
    expect(toast).to_contain_text("Ben Verify passed.")

    with gallery.expect_response("**/components/demo/ok/") as undo:
        toast.get_by_role("button", name="Undo").click()

    assert undo.value.status == 204
    expect(toast).to_have_count(0)


def test_a_sheet_traps_the_page_and_returns_focus(gallery):
    opener = gallery.locator('[data-demo="sheet"]')
    opener.click()
    sheet = gallery.get_by_role("dialog", name="Racer details")

    expect(sheet).to_be_visible()
    expect(sheet.get_by_role("button", name="Close")).to_be_focused()
    assert gallery.evaluate("document.getElementById('page').inert")
    shoot(gallery, "components-sheet")
    assert_accessible(gallery)
    gallery.keyboard.press("Escape")
    expect(sheet).to_be_hidden()
    expect(opener).to_be_focused()


def test_the_confirm_sheet_names_the_count(gallery):
    gallery.locator('[data-demo="confirm"]').click()
    sheet = gallery.get_by_role("dialog", name="Delete racers?")

    with gallery.expect_response("**/components/demo/toast/"):
        sheet.get_by_role("button", name="Delete 42 racers").click()

    expect(sheet).to_be_hidden()
    expect(gallery.locator("#toasts [role=status]")).to_contain_text("passed")


def test_the_segmented_control_picks_one(gallery):
    car = gallery.get_by_role("radio", name="Car number")

    gallery.get_by_text("Car number", exact=True).click()

    expect(car).to_be_checked()
    expect(gallery.get_by_role("radio", name="Name")).not_to_be_checked()


def test_the_stepper_stays_in_range(gallery):
    field = gallery.get_by_role("spinbutton", name="Racers in the final")
    more = gallery.get_by_role("button", name="More: Racers in the final")

    for _ in range(6):
        if more.is_enabled():
            more.click()

    expect(field).to_have_value("8")
    expect(more).to_be_disabled()


def test_the_sortable_list_moves_by_the_row_menu(gallery):
    rows = gallery.get_by_role("list", name="Awards in presentation order")

    with gallery.expect_request("**/components/demo/ok/") as posted:
        gallery.get_by_role("button", name="Actions for Fastest Car").click()
        gallery.get_by_role("menuitem", name="Move up").click()

    assert posted.value.post_data == "order=a1%2Ca3%2Ca2"
    expect(rows.locator("li > span")).to_have_text(
        ["Best Design", "Fastest Car", "Most Colorful"]
    )
    expect(
        gallery.get_by_role("button", name="Actions for Fastest Car")
    ).to_be_focused()


def test_the_sortable_list_moves_by_drag(gallery):
    if gallery.size == "phone":
        pytest.skip("Playwright's mouse drag stands in for touch on the desktop")
    rows = gallery.get_by_role("list", name="Awards in presentation order")
    gallery.wait_for_function("window.Sortable !== undefined")

    gallery.get_by_role("button", name="Drag to reorder Best Design").drag_to(
        gallery.get_by_role("button", name="Drag to reorder Fastest Car")
    )

    expect(rows.locator("li > span")).to_have_text(
        ["Most Colorful", "Fastest Car", "Best Design"]
    )


def test_polling_pauses_while_a_field_has_focus(gallery):
    polls = []
    gallery.on(
        "request",
        lambda r: r.url.endswith("/components/demo/ok/") and polls.append(r),
    )

    gallery.wait_for_timeout(4500)
    running = len(polls)
    gallery.get_by_label("Polling pauses while this field has focus").focus()
    polls.clear()
    gallery.wait_for_timeout(4500)
    paused = len(polls)

    assert running >= 2
    assert paused == 0


def test_the_search_field_sticks_over_its_list(gallery):
    """Always on screen with no keypress, unlike find-racer.js, and it sticks
    under the app bar while its list scrolls."""
    field = gallery.get_by_role("searchbox", name="Find a racer by name or car number")
    expect(field).to_be_in_viewport()

    gallery.mouse.wheel(0, 250)
    gallery.wait_for_function("window.scrollY > 200")

    expect(field).to_be_in_viewport()
