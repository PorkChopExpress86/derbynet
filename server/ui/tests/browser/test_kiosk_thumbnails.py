"""The kiosk dashboard's thumbnails in a browser (#237): each card draws the
page its kiosk shows, from the page's own template with no script
(`ui.kiosk_pages.snapshots`), and a thumbnail sends no request of its own
while the dashboard is open."""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from kiosks.models import Kiosk
from kiosks.pages import PAGES
from ui.kiosks.state import PAGE_LABELS
from ui.tests.browser.conftest import assert_accessible, htmx_post, shoot
from ui.tests.browser.test_kiosks import (
    ABORTED_POLL,
    ABORTED_THUMBNAIL,
    card,
    next_polls,
    open_dashboard,
    ready,
    sheet,
    tap,
)

# A thumbnail is 112 px wide on a phone and 144 px from sm (640 px).
THUMBNAIL_WIDTH = {"phone": 112, "desktop": 144}


@pytest.fixture(autouse=True)
def _thumbnails_may_be_cut_short(page_errors):
    """A board swap replaces every thumbnail, and the browser reports a
    stylesheet or image one was still loading as a failed request
    (`test_a_board_swap_while_a_thumbnail_loads`)."""
    page_errors.allow(ABORTED_POLL)
    page_errors.allow(ABORTED_THUMBNAIL)


def steady_kiosk(event, address, **fields) -> Kiosk:
    """A kiosk listed and never quiet while the test runs: its last contact
    is ahead of the clock, so the board changes only when the test changes
    it, and every poll answers 204 (`ui.kiosks.state.quiet_since`)."""
    return Kiosk.objects.create(
        event=event,
        address=address,
        last_contact=timezone.now() + timedelta(minutes=5),
        **fields,
    )


def thumbnail(page, address):
    return card(page, address).locator("[data-thumbnail] iframe")


def drawn_page(page, address):
    """The `<body>` of the page the card's thumbnail draws."""
    return thumbnail(page, address).content_frame.locator("body")


def test_each_page_set_from_the_picker_shows_in_the_thumbnail(login, event):
    """Acceptance: set a kiosk to each of the 15 pages through the page
    picker, and its thumbnail draws that page, scaled to the card."""
    steady_kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    width = THUMBNAIL_WIDTH[page.size]

    for key, found in PAGES.items():
        tap(page, card(page, "tv").locator('[data-action="page"]'))
        picker = ready(page, sheet(page, "kiosks").locator("[data-page-sheet]"))
        choice = picker.locator(f'[data-page-choice="{found.name}"]')
        expect(
            choice.locator("iframe").content_frame.locator("body")
        ).to_have_attribute("data-page", key)
        with htmx_post(page, "/kiosks/page/"):
            choice.click()
        expect(sheet(page, "kiosks")).to_be_hidden()
        expect(card(page, "tv").locator("[data-page]")).to_have_text(
            PAGE_LABELS[found.name]
        )
        expect(drawn_page(page, "tv")).to_have_attribute("data-page", key)
        expect(drawn_page(page, "tv")).to_have_attribute("data-thumbnail", "")
        box = thumbnail(page, "tv").bounding_box()
        assert round(box["width"]) == width, (key, box)
        assert round(box["height"]) == round(width * 720 / 1280), (key, box)


def test_the_dashboard_at_each_size(login, event):
    """A card per page, for the D75 screenshots: no thumbnail makes a card
    wider than the screen (the page fixture fails a sideways scroll)."""
    for i, key in enumerate(PAGES):
        steady_kiosk(event, f"tv{i:02d}", name=f"TV {i + 1}", page=key)
    page = open_dashboard(login("RaceCoordinator"))
    for i, key in enumerate(PAGES):
        expect(drawn_page(page, f"tv{i:02d}")).to_have_attribute("data-page", key)
        box = thumbnail(page, f"tv{i:02d}").bounding_box()
        outer = card(page, f"tv{i:02d}").bounding_box()
        assert box["x"] >= outer["x"]
        assert box["x"] + box["width"] <= outer["x"] + outer["width"]
    shoot(page, "kiosk-dashboard-thumbnails")
    assert_accessible(page)


def test_a_thumbnail_sends_no_request_of_its_own(login, event):
    """Acceptance: with a kiosk on each page, the dashboard sends only its
    board poll once it has loaded. Every request a thumbnail frame sends
    is recorded from before the page opens, and read once after six board
    polls (12 s), longer than every content poll a kiosk page starts but
    the 30 s check-in list, whose script a thumbnail leaves out with the
    rest (`ui/tests/test_kiosks.py`). A thumbnail loads only its
    stylesheets and images."""
    for i, key in enumerate(PAGES):
        steady_kiosk(event, f"tv{i:02d}", page=key)
    page = login("RaceCoordinator")
    sent = []
    page.on(
        "request",
        lambda r: sent.append(
            (r.frame == page.main_frame, r.resource_type, r.method, r.url)
        ),
    )
    open_dashboard(page)
    for i, key in enumerate(PAGES):
        expect(drawn_page(page, f"tv{i:02d}")).to_have_attribute("data-page", key)
    page.wait_for_load_state("load")
    next_polls(page, 1)
    loaded = len(sent)
    next_polls(page, 6)

    after = sent[loaded:]
    assert after, "no board poll was recorded"
    assert all(main and "/kiosks/board/" in url for main, _, _, url in after), after
    from_thumbnails = [s for s in sent if not s[0]]
    assert from_thumbnails, "no thumbnail loaded anything"
    assert {kind for _, kind, _, _ in from_thumbnails} <= {"stylesheet", "image"}
    assert {method for _, _, method, _ in from_thumbnails} == {"GET"}


def test_a_board_swap_while_a_thumbnail_loads(login, event):
    """Each change to the board swaps every card, and the thumbnails with
    them, so a thumbnail can go while its stylesheet is still loading. The
    first thumbnail's stylesheet is held here until a change has swapped
    that thumbnail away: the browser reports that one request as failed,
    the only error, and the new thumbnail draws the change."""
    steady_kiosk(event, "tv", name="Main", page="kiosks/flag.kiosk")
    page = login("RaceCoordinator")
    held = []
    failed = []
    page.on("requestfailed", lambda r: failed.append((r.url, r.failure)))

    def hold_the_first(route):
        if held:
            route.continue_()
        else:
            held.append(route)

    page.route("**/static/ui/kiosk.css", hold_the_first)
    # The held stylesheet holds the page's load event too.
    with page.expect_request(lambda r: r.url.endswith("/static/ui/kiosk.css")):
        page.goto("/kiosks/", wait_until="domcontentloaded")
    first = thumbnail(page, "tv").element_handle()

    Kiosk.objects.filter(address="tv").update(name="Gym")
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Gym")
    expect(drawn_page(page, "tv").locator("[data-kiosk-name]")).to_have_text("Gym")
    assert first.evaluate("e => !e.isConnected")
    assert len(held) == 1, "the first stylesheet was not held"
    page.wait_for_load_state("load")
    assert [(u.rsplit("/", 1)[-1], f) for u, f in failed] == [
        ("kiosk.css", "net::ERR_ABORTED")
    ]
    page.unroute("**/static/ui/kiosk.css")
