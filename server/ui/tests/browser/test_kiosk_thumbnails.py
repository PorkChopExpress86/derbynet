"""The kiosk dashboard's thumbnails in a browser (#237): each card draws the
page its kiosk shows, from the page's own template with no script
(`ui.kiosk_pages.snapshots`), and a thumbnail sends no request of its own
while the dashboard is open."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from awards.editing import set_winner
from awards.models import Award
from core.models import RaceState
from kiosks.models import Kiosk
from kiosks.pages import PAGES
from racing.services import polls
from roster.models import Racer
from ui.kiosks.state import PAGE_LABELS
from ui.tests.browser.conftest import assert_accessible, htmx_post, shoot
from ui.tests.browser.test_kiosks import (
    ABORTED_POLL,
    card,
    drop_thumbnail_aborts,
    next_polls,
    open_dashboard,
    ready,
    sheet,
    tap,
)
from ui.tests.race import race_setup, scheduled

# A thumbnail is 112 px wide on a phone and 144 px from sm (640 px)
# (`ui/templates/ui/kiosks/thumbnail.html`).
THUMBNAIL_WIDTH = {"phone": 112, "desktop": 144}


@pytest.fixture
def thumbnail_aborts(page, page_errors) -> list[str]:
    """`drop_thumbnail_aborts()`, for the tests that swap the board while
    its thumbnails load."""
    return drop_thumbnail_aborts(page, page_errors)


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


# True once the card's thumbnail frame is `width` px wide at 16:9 and inside
# its card. Read with a retrying wait: the frame's box settles with the
# swap that drew it.
SCALED = """([frame, card, width]) => {
  const f = frame.getBoundingClientRect(), c = card.getBoundingClientRect();
  return Math.round(f.width) === width
    && Math.round(f.height) === Math.round(width * 720 / 1280)
    && f.left >= c.left && f.right <= c.right;
}"""


def scaled(page, address):
    """Wait until the card's thumbnail is scaled to the card (112 px on a
    phone, 144 px from sm) and inside it."""
    page.wait_for_function(
        SCALED,
        arg=[
            thumbnail(page, address).element_handle(),
            card(page, address).element_handle(),
            THUMBNAIL_WIDTH[page.size],
        ],
    )


def race_and_award(event) -> tuple[dict, str]:
    """A heat on the track and an award revealed: the lane 1 racer of the
    heat as the kiosk's first poll gives it (`current_racers()`), and the
    award's name."""
    round = race_setup(event, groups=(("Lions", 4),))["Lions"]
    scheduled(round)
    winner = Racer.objects.filter(event=event).order_by("pk").first()
    award = Award.objects.create(
        event=event, name="Best Paint", award_type_id=1, sort=1
    )
    set_winner(event, award, winner)
    RaceState.objects.filter(event=event).update(
        current_award_key=f"award-{award.pk}", reveal_current_award=True
    )
    run = polls.running(event)
    lane1 = polls.current_racers(run, run.settings.name_style)[0]
    assert lane1["lane"] == 1
    return lane1, award.name


def expect_content(body, found, lane1, award):
    """What the thumbnail of `found` shows beyond its page key (D84)."""
    if found.name in (
        "now-racing",
        "now-racing-columnar",
        "now-racing-columnar-reversed",
    ):
        lane = body.locator('[data-lane="1"]')
        expect(lane.locator("[data-name]")).to_have_text(lane1["name"])
        expect(lane.locator("[data-car]")).to_have_text(str(lane1["carnumber"]))
    elif found.name == "award-presentations":
        expect(body.locator("[data-award-name]")).to_have_text(award)
        expect(body.locator("[data-recipient]")).to_be_visible()
    elif found.name in ("ondeck", "qrcode"):
        expect(body.locator("[data-thumb-note]")).to_be_visible()


def test_each_page_set_from_the_picker_shows_in_the_thumbnail(
    login, event, page_errors, thumbnail_aborts
):
    """Acceptance: set a kiosk to each of the 15 pages through the page
    picker during a heat, with an award revealed, and its thumbnail draws
    that page, scaled to the card: the heat's racers on a now-racing page,
    the award on the award page, and the on-kiosk note on the on-deck and
    QR pages (D84)."""
    page_errors.allow(ABORTED_POLL)
    lane1, award = race_and_award(event)
    steady_kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))

    for key, found in PAGES.items():
        tap(page, card(page, "tv").locator('[data-action="page"]'))
        picker = ready(page, sheet(page, "kiosks").locator("[data-page-sheet]"))
        choice = picker.locator(f'[data-page-choice="{found.name}"]')
        offered = choice.locator("iframe").content_frame.locator("body")
        expect(offered).to_have_attribute("data-page", key)
        expect_content(offered, found, lane1, award)
        with htmx_post(page, "/kiosks/page/"):
            choice.click()
        expect(sheet(page, "kiosks")).to_be_hidden()
        expect(card(page, "tv").locator("[data-page]")).to_have_text(
            PAGE_LABELS[found.name]
        )
        expect(drawn_page(page, "tv")).to_have_attribute("data-page", key)
        expect(drawn_page(page, "tv")).to_have_attribute("data-thumbnail", "")
        expect_content(drawn_page(page, "tv"), found, lane1, award)
        scaled(page, "tv")


def test_the_dashboard_at_each_size(login, event):
    """A card per page during a heat with an award revealed, for the D75
    screenshots: each thumbnail is scaled inside its card, and none makes a
    card wider than the screen (the page fixture fails a sideways
    scroll)."""
    lane1, award = race_and_award(event)
    for i, key in enumerate(PAGES):
        steady_kiosk(event, f"tv{i:02d}", name=f"TV {i + 1}", page=key)
    page = open_dashboard(login("RaceCoordinator"))
    for i, (key, found) in enumerate(PAGES.items()):
        expect(drawn_page(page, f"tv{i:02d}")).to_have_attribute("data-page", key)
        expect_content(drawn_page(page, f"tv{i:02d}"), found, lane1, award)
        scaled(page, f"tv{i:02d}")
    shoot(page, "kiosk-dashboard-thumbnails")
    assert_accessible(page)


# The interval of each content poll a kiosk page's script registers
# (`Kiosk.poll(fetch, ms, mode)` in ui/static/ui/kiosk-*.js) and the kiosk
# poll's (`setInterval(Kiosk.pollKiosk, 5000)` in kiosk.js).
SCRIPTS = Path(__file__).resolve().parents[2] / "static" / "ui"
POLL_MS = re.compile(
    r"Kiosk\.poll\((?:[^()]|\([^()]*(?:\([^()]*\)[^()]*)*\))*?,\s*(\d+),"
)


def poll_intervals() -> dict[str, list[int]]:
    found = {
        path.name: [int(ms) for ms in POLL_MS.findall(path.read_text())]
        for path in sorted(SCRIPTS.glob("kiosk-*.js"))
    }
    kiosk = (SCRIPTS / "kiosk.js").read_text()
    found["kiosk.js"] = [
        int(ms) for ms in re.findall(r"setInterval\(Kiosk\.pollKiosk, (\d+)\)", kiosk)
    ]
    return {name: ms for name, ms in found.items() if ms}


# Six board polls, 2 s apart (ui.kiosks.state.POLL_SECONDS).
WATCHED_MS = 6 * 2000


def test_the_watch_outlasts_every_poll_but_the_check_in_list():
    """What the no-request test's watch covers, read from the scripts: every
    poll interval is shorter than the watch, but the please-check-in list's
    30 s, a script a thumbnail leaves out with every other
    (`ui/tests/test_kiosks.py`)."""
    found = poll_intervals()
    assert found["kiosk-check-in.js"] == [30000]
    assert found["kiosk.js"] == [5000]
    longer = {n: ms for n, ms in found.items() if max(ms) >= WATCHED_MS}
    assert longer == {"kiosk-check-in.js": [30000]}, found
    assert set(found) >= {
        "kiosk-awards.js",
        "kiosk-now-racing.js",
        "kiosk-ondeck.js",
        "kiosk-results.js",
        "kiosk-slideshow.js",
    }


def test_a_thumbnail_sends_no_request_of_its_own(login, event):
    """Acceptance: with a kiosk on each page, the dashboard sends only its
    board poll once it has loaded. Every request a thumbnail frame sends is
    recorded from before the page opens, and read once after six board
    polls, longer than every poll a kiosk page's script starts but the
    check-in list's (`test_the_watch_outlasts_every_poll_but_the_check_in_list`).
    A thumbnail loads only its stylesheets and images."""
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
    next_polls(page, WATCHED_MS // 2000)

    after = sent[loaded:]
    assert after, "no board poll was recorded"
    assert all(main and "/kiosks/board/" in url for main, _, _, url in after), after
    from_thumbnails = [s for s in sent if not s[0]]
    assert from_thumbnails, "no thumbnail loaded anything"
    assert {kind for _, kind, _, _ in from_thumbnails} <= {"stylesheet", "image"}
    assert {method for _, _, method, _ in from_thumbnails} == {"GET"}


# A fetch of the page itself, held by a route and then aborted.
MAIN_ABORT = "/static/ui/kiosk.css?main-frame"


def test_a_board_swap_while_a_thumbnail_loads(
    login, event, page_errors, thumbnail_aborts
):
    """Each change to the board swaps every card, and the thumbnails with
    them, so a thumbnail can go while its stylesheet is still loading. The
    first thumbnail's stylesheet is held here until a change has swapped
    that thumbnail away: the browser reports that request as failed,
    `net::ERR_ABORTED`, and `thumbnail_aborts` drops it, and the new
    thumbnail draws the change. The same failure from the page's own frame
    stays an error."""
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

    # The page's own fetch of a thumbnail's stylesheet, aborted the same way.
    page.route(f"**{MAIN_ABORT}", lambda route: None)
    with page.expect_request(lambda r: r.url.endswith(MAIN_ABORT)):
        page.evaluate(
            f"() => {{ window.held = new AbortController();"
            f" fetch('{MAIN_ABORT}', {{signal: held.signal}}).catch(() => {{}}); }}"
        )
    with page.expect_event("requestfailed", lambda r: r.url.endswith(MAIN_ABORT)):
        page.evaluate("() => window.held.abort()")
    page.unroute(f"**{MAIN_ABORT}")
    main_failure = (
        f"request failed: {page.url.split('/kiosks/')[0]}{MAIN_ABORT} net::ERR_ABORTED"
    )
    assert len(thumbnail_aborts) == 1, thumbnail_aborts
    assert page_errors.final() == [main_failure]
    # Seen; the rest of the test's errors must still be none.
    page_errors.errors.remove(main_failure)
