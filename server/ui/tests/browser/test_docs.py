"""The guides under `/docs/` in a browser (#61), at both sizes.

- The help link opens the quick reference on every manager page: on a
  desktop from the sidebar, on a phone from the More drawer (D94).
- Each guide fits the screen with no sideways scroll, passes axe, and draws
  its images from the server.
- Printed, a guide leaves out the menus.
"""

import pytest
from playwright.sync_api import expect

from ui.docs import build
from ui.navigation import ENTRIES
from ui.tests.browser.conftest import (
    assert_accessible,
    close_tab,
    no_sideways_scroll,
    shoot,
)

# Registry pages drawn without the shell, so with no help link: the kiosk
# pages shown on their own, and the replay station and its camera, which fill
# the screen.
NO_SHELL = {"slideshow", "ondeck", "replay", "camera"}


@pytest.fixture
def built(settings, tmp_path):
    settings.DOCS_ROOT = tmp_path / "docs"
    build.build(settings.DOCS_ROOT)


def help_link(page):
    if page.size == "desktop":
        return page.get_by_role("navigation", name="Main menu").get_by_role(
            "link", name="Quick reference"
        )
    page.get_by_role("button", name="More").click()
    drawer = page.get_by_role("dialog", name="More")
    expect(drawer).to_be_visible()
    return drawer.get_by_role("link", name="Quick reference")


def test_the_help_link_opens_the_quick_reference_on_every_manager_page(login, built):
    page = login("RaceCoordinator")
    shelled, unshelled = [], []
    for entry in ENTRIES:
        page.goto(f"/{entry.path}")
        if not page.locator("#page").count():
            unshelled.append(entry.key)
            continue
        link = help_link(page)
        expect(link).to_have_attribute("href", "/docs/quick-reference/")
        with page.context.expect_page() as opened:
            link.click()
        guide = opened.value
        expect(guide.locator('[data-doc="quick-reference"] h1')).to_have_text(
            "Quick reference"
        )
        close_tab(page, guide)
        shelled.append(entry.key)
    assert set(unshelled) == NO_SHELL
    assert len(shelled) == len(ENTRIES) - len(NO_SHELL)


@pytest.mark.parametrize("slug", build.GUIDES)
def test_each_guide_fits_the_screen_and_draws_its_images(page, event, built, slug):
    page.goto(f"/docs/{slug}/")
    expect(page.locator(f'[data-doc="{slug}"] h1')).to_be_visible()
    no_sideways_scroll(page)
    images = page.locator("article.doc img")
    for i in range(images.count()):
        image = images.nth(i)
        image.scroll_into_view_if_needed()
        page.wait_for_function(
            "img => img.complete && img.naturalWidth > 0", arg=image.element_handle()
        )
    assert_accessible(page)
    if slug == "quick-reference":
        shoot(page, "docs-quick-reference")


def test_a_printed_guide_leaves_out_the_menus(login, built):
    page = login("RaceCoordinator")
    page.goto("/docs/install-docker/")
    page.emulate_media(media="print")
    expect(page.locator("article.doc h1")).to_be_visible()
    expect(page.get_by_role("navigation", name="Main menu")).to_be_hidden()
    expect(page.locator("#page > div > header")).to_be_hidden()
    expect(page.locator("[data-bottom-nav]")).to_be_hidden()
    expect(page.get_by_role("link", name="All guides")).to_be_hidden()
    page.emulate_media(media="screen")
    expect(page.get_by_role("link", name="All guides")).to_be_visible()
