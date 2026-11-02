"""The shell: home, login, role navigation, and the parity boxes of
index.php and login.php (#40)."""

import re

import pytest
from playwright.sync_api import expect

from ui.tests.browser.conftest import assert_accessible, shoot
from ui.tests.roles import ROLE_NAV, ROLES


def visible_texts(locator) -> list[str]:
    return [t.strip() for t in locator.all_inner_texts()]


@pytest.mark.parametrize("role", ROLES)
def test_each_role_sees_exactly_its_registry_entries(login, role):
    page = login(role)
    expected = ROLE_NAV[role]

    page.goto("/")
    home = {}
    for section in page.locator("main section[data-section]").all():
        name = section.get_attribute("data-section")
        home[name] = visible_texts(section.locator("li a"))
    assert home == expected["sections"]

    if page.size == "desktop":
        sidebar = page.get_by_role("navigation", name="Main menu")
        expect(sidebar).to_be_visible()
        side = {}
        for heading in sidebar.locator("h2").all():
            links = heading.locator("xpath=following-sibling::ul[1]").locator("a")
            side[heading.inner_text().strip().title()] = visible_texts(links)
        assert side == expected["sections"]
        expect(page.locator("[data-bottom-nav]")).to_be_hidden()
    else:
        nav = page.locator("[data-bottom-nav]")
        if expected["phone"] is None:
            expect(nav).to_have_count(0)
        else:
            items = [" ".join(t.split()) for t in nav.locator("li").all_inner_texts()]
            assert items == expected["phone"]
        expect(page.get_by_role("navigation", name="Main menu")).to_be_hidden()


def test_the_more_drawer_holds_the_rest_and_the_help_link(login):
    page = login("RaceCrew")
    if page.size == "desktop":
        pytest.skip("the drawer is the phone's")
    page.goto("/")

    page.get_by_role("button", name="More").click()
    drawer = page.get_by_role("dialog", name="More")
    expect(drawer).to_be_visible()
    expect(drawer.get_by_role("link", name="Car Photos")).to_be_visible()
    expect(drawer.get_by_role("link", name="Retrospective")).to_be_visible()
    expect(drawer.get_by_role("link", name="Race Check-In")).to_have_count(0)
    expect(drawer.get_by_role("link", name="Quick reference")).to_have_attribute(
        "href", "/quickref.pdf"
    )
    expect(drawer.locator("[data-version]")).to_contain_text("DerbyNet ")
    assert_accessible(page)
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    expect(page.get_by_role("button", name="More")).to_be_focused()


def test_the_sidebar_shows_the_help_link_and_version(login):
    page = login("RaceCoordinator")
    if page.size == "phone":
        pytest.skip("the sidebar is the desktop's")
    page.goto("/")

    sidebar = page.get_by_role("navigation", name="Main menu")
    expect(sidebar.get_by_role("link", name="Quick reference")).to_be_visible()
    expect(sidebar.locator("[data-version]")).to_contain_text("DerbyNet ")


def test_set_up_gets_two_columns_on_a_desktop(login):
    page = login("RaceCoordinator")
    page.goto("/")

    before = page.locator('section[data-section="Before"]').bounding_box()
    after = page.locator('section[data-section="After"]').bounding_box()
    if page.size == "desktop":
        assert after["x"] > before["x"] + before["width"] / 2
        assert after["y"] == before["y"]
    else:
        assert after["x"] == before["x"]
        assert after["y"] > before["y"]


def test_a_new_install_goes_to_set_up(page, _database):
    """No event yet: home sends every visitor to set-up."""
    page.goto("/")

    expect(page).to_have_url(re.compile(r"/setup/$"))


def test_roles_for_machines_show_after_show_all_roles(page, event):
    page.goto("/login/")
    roles = page.locator("button[data-role]")
    expect(roles).to_have_text(["RaceCrew", "RaceCoordinator"])

    page.get_by_role("link", name="Show all roles").click()

    expect(roles).to_have_text(["Timer", "Photo", "RaceCrew", "RaceCoordinator"])
    page.get_by_role("link", name="Show fewer roles").click()
    expect(roles).to_have_count(2)


def test_a_wrong_password_shows_an_inline_error(page, event, page_errors):
    page_errors.allow(r"status of 401")
    page.goto("/login/")
    page.locator('button[data-role="RaceCrew"]').click()
    sheet = page.get_by_role("dialog", name="Log in as RaceCrew")
    expect(sheet.get_by_label("Password")).to_be_focused()

    sheet.get_by_label("Password").fill("wrong")
    sheet.get_by_role("button", name="Log in").click()

    sheet = page.get_by_role("dialog", name="Log in as RaceCrew")
    expect(sheet.get_by_role("alert")).to_have_text("Incorrect password.")
    expect(sheet.get_by_label("Password")).to_be_focused()
    shoot(page, "login-wrong-password")
    sheet.get_by_label("Password").fill("murphy")
    sheet.get_by_label("Password").press("Enter")
    expect(page).to_have_url(re.compile(r"/$"))
    expect(page.locator("[data-role-chip]")).to_contain_text("RaceCrew")


def test_log_out_from_the_app_bar(login):
    page = login("RaceCoordinator")

    page.get_by_role("button", name="Log out").first.click()

    expect(page).to_have_url(re.compile(r"/login/$"))
    expect(page.locator("[data-role-chip]")).to_have_count(0)


def test_the_kiosk_and_camera_entries(page, event):
    page.goto("/login/")

    for name in ("Be a Kiosk", "Fullscreen Kiosk", "Replay Kiosk", "Be a Camera"):
        expect(page.get_by_role("link", name=name)).to_be_visible()


def test_the_documentation_hint_hides_when_settings_are_locked(page, event, settings):
    page.goto("/login/")
    page.locator('button[data-role="RaceCrew"]').click()
    expect(page.locator("[data-docs-hint]")).to_be_visible()

    settings.LOCK_SETTINGS = True
    page.goto("/login/")
    page.locator('button[data-role="RaceCrew"]').click()

    expect(page.get_by_role("dialog", name="Log in as RaceCrew")).to_be_visible()
    expect(page.locator("[data-docs-hint]")).to_have_count(0)


def test_an_htmx_request_without_permission_gets_a_toast(login, page_errors):
    page_errors.allow(r"403")
    page = login("RaceCrew")
    page.goto("/")

    page.evaluate("htmx.ajax('GET', '/dashboard/', {target: '#main'})")

    expect(page.locator("#toasts [role=alert]")).to_contain_text("Log in again.")
    expect(page.locator("main section[data-section]").first).to_be_visible()


def test_the_default_password_banner(login):
    page = login("RaceCoordinator")
    page.goto("/")

    banner = page.locator("[data-default-password-banner]")
    expect(banner).to_contain_text("still use their default passwords")
    expect(banner.get_by_role("link", name="Change passwords")).to_be_visible()


@pytest.mark.parametrize("role", ["anonymous", "RaceCrew", "RaceCoordinator"])
def test_home_and_login_are_accessible(login, role):
    page = login(role)

    page.goto("/")
    page.wait_for_load_state("networkidle")
    shoot(page, f"home-{role}")
    assert_accessible(page)

    page.goto("/login/")
    page.wait_for_load_state("networkidle")
    shoot(page, f"login-{role}")
    assert_accessible(page)

    page.locator('button[data-role="RaceCoordinator"]').click()
    expect(page.get_by_role("dialog")).to_be_visible()
    shoot(page, f"login-sheet-{role}")
    assert_accessible(page)
