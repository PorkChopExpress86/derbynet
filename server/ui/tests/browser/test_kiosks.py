"""The kiosk dashboard and the scene editor in a browser (#46): a scene
switch that holds through the polls (C19), three fake kiosks that follow a
scene tapped on the phone, as `testing/test-scenes.sh` drives them, the
slideshow Configure sheet showing what was saved (C20), the parity boxes of
`kiosk-dashboard.php` and `scenes.php`, and the screenshots."""

import json
import re
from contextlib import nullcontext
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from kiosks.madlib import madlib
from kiosks.models import Kiosk, Scene, SceneKiosk
from roster.models import Group
from ui.kiosks import state
from ui.tests.browser.conftest import (
    assert_accessible,
    close_tab,
    dismiss_toasts,
    htmx_post,
    htmx_settled,
    shoot,
)
from ui.tests.browser.helpers import MAX_SWAPS, bound, ready

ADDRESSES = ("kiosk1-test46", "kiosk2-test46", "kiosk3-test46")
ABORTED_POLL = r"request failed: \S*/kiosks/board/\S* net::ERR_ABORTED$"


def drop_thumbnail_aborts(page, page_errors) -> list[str]:
    """A board swap replaces every card and its thumbnail frame (#237), and
    the browser reports a stylesheet or image a replaced frame was still
    loading as a failed request, `net::ERR_ABORTED`. Such a failure from a
    thumbnail frame is dropped from the page's errors; the same failure
    from the page itself still fails the test
    (`test_kiosk_thumbnails.py::test_a_board_swap_while_a_thumbnail_loads`).
    Returns the failures dropped."""
    dropped = []

    def failed(request):
        if request.failure == "net::ERR_ABORTED" and request.frame != page.main_frame:
            dropped.append(f"request failed: {request.url} {request.failure}")

    page.on("requestfailed", failed)
    final = page_errors.final

    def without_thumbnails() -> list[str]:
        errors = final()
        for message in dropped:
            if message in errors:
                errors.remove(message)
        return errors

    page_errors.final = without_thumbnails
    return dropped


@pytest.fixture
def thumbnail_aborts(page, page_errors) -> list[str]:
    return drop_thumbnail_aborts(page, page_errors)


@pytest.fixture(autouse=True)
def _polls_may_be_cut_short(page_errors, thumbnail_aborts):
    """A change aborts a board poll still in flight
    (`hx-sync="#kiosk-board:replace"`), so the tap is sent at once and no
    stale poll swaps after its reply. The browser reports the aborted poll
    as a failed request. Every test here opens the board or the scene
    editor with thumbnails and changes it, or lets a kiosk go quiet, which
    swaps them (`thumbnail_aborts`)."""
    page_errors.allow(ABORTED_POLL)


def kiosk(event, address, **fields) -> Kiosk:
    return Kiosk.objects.create(
        event=event, address=address, last_contact=timezone.now(), **fields
    )


def scene(event, name) -> Scene:
    return Scene.objects.get(event=event, name=name)


def poll_kiosk(page, address) -> dict:
    """`query=poll.kiosk` as the kiosk at `address` sends it."""
    reply = page.request.get(f"/action.php?query=poll.kiosk&address={address}")
    assert reply.ok
    return json.loads(reply.text())["kiosk-setting"]


def open_dashboard(page):
    page.goto("/kiosks/")
    expect(page.locator("#kiosk-board [data-switcher]")).to_be_visible()
    return page


def card(page, address):
    return page.locator(f'#kiosk-board [data-kiosk="{address}"]')


def sheet(page, name):
    return page.locator(f'[data-sheet="{name}"]')


def tap(page, control):
    """Click a control of a list or board that htmx may have just swapped
    (a poll, a filter, a reply out of band), once htmx has bound it."""
    htmx_settled(page)
    bound(page, control)
    dismiss_toasts(page)
    control.click()


def scene_button(page, found):
    return page.locator(f'#kiosk-board button[data-scene="{found.pk}"]')


def next_polls(page, count):
    """Wait for `count` board polls to answer."""
    for _ in range(count):
        with page.expect_response(
            lambda r: "/kiosks/board/" in r.url, timeout=10_000
        ) as reply:
            pass
        assert reply.value.status in (200, 204)


def test_a_scene_switch_still_shows_after_three_polls(login, event):
    """C19: PHP's switcher reset at the next poll, because its poll echoed
    a key nothing writes."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene(event, "Racing")
    with htmx_post(page, "/kiosks/scene/"):
        tap(page, scene_button(page, racing))
    expect(scene_button(page, racing)).to_have_attribute("aria-pressed", "true")
    next_polls(page, 3)
    expect(scene_button(page, racing)).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#kiosk-board button[aria-pressed="true"]')).to_have_count(1)


@pytest.mark.sizes("phone", reason="this acceptance check is at 390x844")
def test_three_fake_kiosks_follow_a_scene_tapped_on_the_phone(login, event):
    """The steps of `testing/test-scenes.sh`: the kiosks check in and get
    identify, are named, and then each scene tapped gives each named kiosk
    its page, read back through `query=poll.kiosk`."""
    page = login("RaceCoordinator")
    for address in ADDRESSES:
        assert poll_kiosk(page, address)["page"] == "kiosks/identify.kiosk"
    open_dashboard(page)
    for address, name in zip(ADDRESSES, ("Main", "Aux1", "Aux2"), strict=True):
        tap(page, card(page, address).locator('[data-action="name"]'))
        form = ready(page, sheet(page, "kiosks").locator("[data-name-sheet]"))
        form.locator(f'[data-name-chips] button[data-value="{name}"]').click()
        expect(form.get_by_label("Name for this kiosk")).to_have_value(name)
        with htmx_post(page, "/kiosks/name/"):
            form.locator("[data-submit]").click()
        expect(sheet(page, "kiosks")).to_be_hidden()
        expect(card(page, address).locator("[data-name]")).to_have_text(name)
    expect(page.locator("[data-named-warning]")).to_have_count(0)

    for name, pages in (
        ("Racing", ("now-racing", "ondeck", "results-by-racer")),
        ("Check-In", ("slideshow", "please-check-in", "welcome")),
        ("Awards", ("award-presentations", "please-check-in", "welcome")),
    ):
        with htmx_post(page, "/kiosks/scene/"):
            tap(page, scene_button(page, scene(event, name)))
        for address, brief in zip(ADDRESSES, pages, strict=True):
            assert poll_kiosk(page, address)["page"] == f"kiosks/{brief}.kiosk"
            expect(card(page, address).locator("[data-page]")).to_have_attribute(
                "data-page", f"kiosks/{brief}.kiosk"
            )
    shoot(page, "kiosks-scene-applied")


def test_the_slideshow_configure_sheet_shows_what_was_saved(login, event):
    """C20: PHP's dialogs never showed the stored parameters."""
    lions = Group.objects.create(event=event, name="Lions", sort_order=1)
    Group.objects.create(event=event, name="Tigers", sort_order=2)
    kiosk(event, "tv", name="Lobby", page="kiosks/slideshow.kiosk")
    page = open_dashboard(login("RaceCoordinator"))
    tap(page, card(page, "tv").locator('[data-action="configure"]'))
    form = ready(
        page, sheet(page, "kiosks").locator('[data-configure-sheet="slideshow"]')
    )
    form.get_by_label("Title text").fill("Welcome, Pack 1")
    form.get_by_label("Tigers").uncheck()
    with htmx_post(page, "/kiosks/configure/"):
        form.locator("[data-submit]").click()
    expect(sheet(page, "kiosks")).to_be_hidden()
    expect(card(page, "tv").locator("[data-summary]")).to_have_text(
        "Welcome, Pack 1 · Lions"
    )
    assert json.loads(Kiosk.objects.get(address="tv").params) == {
        "title": "Welcome, Pack 1",
        "subdir": "",
        "classids": [lions.pk],
    }

    page.reload()
    tap(page, card(page, "tv").locator('[data-action="configure"]'))
    form = ready(
        page, sheet(page, "kiosks").locator('[data-configure-sheet="slideshow"]')
    )
    expect(form.get_by_label("Title text")).to_have_value("Welcome, Pack 1")
    expect(form.get_by_label("Lions")).to_be_checked()
    expect(form.get_by_label("Tigers")).not_to_be_checked()
    shoot(page, "kiosks-configure", full_page=False)
    assert_accessible(page)


def test_kiosk_dashboard_parity(login, event):
    """The boxes of `kiosk-dashboard.php`: the 60 s list, the names and last
    contact, the page select, Configure for each page with parameters,
    Reload, the named-kiosk warning and New Kiosk Window."""
    kiosk(event, "10.0.0.1", name="Main", page="kiosks/now-racing.kiosk")
    kiosk(event, "10.0.0.2", page="kiosks/welcome.kiosk")
    quiet = kiosk(event, "10.0.0.3", name="Aux1", page="kiosks/flag.kiosk")
    Kiosk.objects.filter(pk=quiet.pk).update(
        last_contact=timezone.now() - timedelta(seconds=20)
    )
    gone = kiosk(event, "10.0.0.9", name="Old")
    Kiosk.objects.filter(pk=gone.pk).update(
        last_contact=timezone.now() - timedelta(seconds=90)
    )
    page = open_dashboard(login("RaceCoordinator"))

    # Kiosks seen in the last 60 s only.
    expect(page.locator("#kiosk-board [data-kiosk]")).to_have_count(3)
    expect(card(page, "10.0.0.9")).to_have_count(0)
    # Madlib name, assigned name, last contact age (over 5 s only).
    expect(card(page, "10.0.0.2").locator("[data-madlib]")).to_have_text(
        madlib("10.0.0.2")
    )
    expect(card(page, "10.0.0.1").locator("[data-name]")).to_have_text("Main")
    # Its contact set again now, as the page can take long to open under load.
    Kiosk.objects.filter(pk=quiet.pk).update(
        last_contact=timezone.now() - timedelta(seconds=20)
    )
    expect(card(page, "10.0.0.3").locator("[data-last-contact]")).to_have_text(
        re.compile(r"Last contact: (19|2\d)s ago")
    )
    # A kiosk that just checked in shows no age. Under load the page can take
    # longer than 5 s to open, so the kiosk checks in again here.
    poll_kiosk(page, "10.0.0.1")
    expect(card(page, "10.0.0.1").locator("[data-last-contact]")).to_be_hidden()
    # The named kiosk count warning.
    expect(page.locator("[data-named-warning]")).to_contain_text(
        "2 named kiosks out of 3"
    )
    shoot(page, "kiosks-dashboard")
    assert_accessible(page)

    # The page select, with a thumbnail per page.
    tap(page, card(page, "10.0.0.2").locator('[data-action="page"]'))
    picker = ready(page, sheet(page, "kiosks").locator("[data-page-sheet]"))
    expect(picker.locator("[data-page-choice]")).to_have_count(15)
    expect(picker.locator("[data-page-choice] [data-thumbnail] iframe")).to_have_count(
        15
    )
    with htmx_post(page, "/kiosks/page/"):
        picker.locator('[data-page-choice="qrcode"]').click()
    expect(sheet(page, "kiosks")).to_be_hidden()
    expect(card(page, "10.0.0.2").locator("[data-page]")).to_have_text("QR code")

    # Configure for each page with parameters.
    for brief, label in (
        ("please-check-in", None),
        ("standings", "Show standings for"),
        ("award-presentations", "Confetti"),
        ("qrcode", "QR code"),
    ):
        Kiosk.objects.filter(address="10.0.0.2").update(
            page=f"kiosks/{brief}.kiosk", params=""
        )
        expect(card(page, "10.0.0.2").locator("[data-page]")).to_have_attribute(
            "data-page", f"kiosks/{brief}.kiosk"
        )
        htmx_settled(page)
        tap(page, card(page, "10.0.0.2").locator('[data-action="configure"]'))
        form = ready(
            page, sheet(page, "kiosks").locator(f'[data-configure-sheet="{brief}"]')
        )
        if label:
            expect(form.get_by_text(label, exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        expect(sheet(page, "kiosks")).to_be_hidden()

    # Reload.
    with htmx_post(page, "/kiosks/reload/"):
        tap(page, card(page, "10.0.0.1").locator('[data-action="reload"]'))
    expect(card(page, "10.0.0.1")).to_contain_text("Reloading")
    assert poll_kiosk(page, "10.0.0.1")["reload"] is True
    # Identify.
    with htmx_post(page, "/kiosks/identify/"):
        tap(page, card(page, "10.0.0.1").locator('[data-action="identify"]'))
    assert Kiosk.objects.get(address="10.0.0.1").page == "kiosks/identify.kiosk"

    # New Kiosk Window, on a desktop only.
    window = page.locator('[data-action="new-kiosk-window"]')
    if page.size == "phone":
        expect(window).to_be_hidden()
    else:
        kiosk_url = re.compile(r"/kiosk\.php\?address=localhost%20\d+$")
        dismiss_toasts(page)
        with page.expect_popup() as popup:
            window.click()
        assert kiosk_url.search(popup.value.url)
        close_tab(page, popup.value)


def test_a_presenter_sees_the_board_without_controls(page, live_server, event):
    """C37: a role with PRESENT_AWARDS and not CONTROL_RACE gets the board,
    and no control that would fail."""
    from accounts.models import Role
    from accounts.permissions import Perm

    role = Role(name="Presenter", permissions=int(Perm.PRESENT_AWARDS), position=9)
    role.set_password("present")
    role.save()
    kiosk(event, "tv", name="Main")
    page.goto("/login/?all")
    page.locator('button[data-role="Presenter"]').click()
    page.get_by_label("Password").fill("present")
    page.get_by_label("Password").press("Enter")
    page.wait_for_url(re.compile(r"/$"))
    open_dashboard(page)
    expect(page.locator("[data-view-only]")).to_be_visible()
    expect(card(page, "tv")).to_be_visible()
    expect(page.locator("#kiosk-board button")).to_have_count(0)


def test_scene_editor_parity(login, event):
    """The boxes of `scenes.php`: new scene, delete scene, add kiosk name,
    a page and parameters per kiosk name, and back to the calling page."""
    page = login("RaceCoordinator")
    page.goto("/scenes.php?back=setup.php")
    expect(page).to_have_url(re.compile(r"/kiosks/scenes/\?back=setup\.php$"))
    expect(page.locator('a[aria-label="Back"]')).to_have_attribute("href", "/setup/")
    entries = page.locator("#scene-entries [data-entry]")
    expect(entries).to_have_count(3)
    expect(page.locator("#scene-entries [data-thumbnail] iframe")).to_have_count(1)
    shoot(page, "scenes-editor")
    assert_accessible(page)

    # New scene: the page loads at it, keeping back.
    page.locator('[data-action="new-scene"]').click()
    form = ready(page, sheet(page, "scenes").locator("[data-new-scene-sheet]"))
    form.get_by_label("Scene name").fill("Pack Night")
    with page.expect_navigation():
        form.locator("[data-submit]").click()
    made = Scene.objects.get(event=event, name="Pack Night")
    expect(page).to_have_url(re.compile(rf"\?scene={made.pk}&back=setup\.php$"))
    expect(page.locator('[data-scene-picker] [aria-current="page"]')).to_have_text(
        "Pack Night"
    )

    # Add kiosk name, with its page.
    page.locator('[data-action="add-name"]').click()
    form = ready(page, sheet(page, "scenes").locator("[data-add-name-sheet]"))
    form.get_by_label("Kiosk name").fill("Lobby")
    form.get_by_label("Page in this scene").select_option("kiosks/slideshow.kiosk")
    with htmx_post(page, "/kiosks/scenes/name/"):
        form.locator("[data-submit]").click()
    expect(sheet(page, "scenes")).to_be_hidden()
    lobby = page.locator('#scene-entries [data-entry="Lobby"]')
    expect(lobby.locator("[data-page]")).to_have_text("Slideshow")

    # Its parameters.
    tap(page, lobby.locator('[data-action="configure"]'))
    form = ready(
        page, sheet(page, "scenes").locator('[data-configure-sheet="slideshow"]')
    )
    form.get_by_label("Title text").fill("Pack night")
    with htmx_post(page, "/kiosks/scenes/configure/"):
        form.locator("[data-submit]").click()
    expect(lobby.locator("[data-summary]")).to_contain_text("Pack night")
    row = SceneKiosk.objects.get(scene=made, kiosk_name="Lobby")
    assert json.loads(row.params)["title"] == "Pack night"

    # Another page for Main in this scene.
    tap(page, page.locator('#scene-entries [data-entry="Main"] [data-action="page"]'))
    picker = ready(page, sheet(page, "scenes").locator("[data-page-sheet]"))
    dismiss_toasts(page)
    with htmx_post(page, "/kiosks/scenes/page/"):
        picker.locator('[data-page-choice="standings"]').click()
    expect(page.locator('#scene-entries [data-entry="Main"] [data-page]')).to_have_text(
        "Standings"
    )
    shoot(page, "scenes-editor-edited")

    # Delete scene: the page loads at the first scene.
    dismiss_toasts(page)
    page.locator('[data-action="delete-scene"]').click()
    confirm = ready(page, sheet(page, "scenes").locator("[data-delete-scene-sheet]"))
    with page.expect_navigation():
        confirm.locator("[data-confirm]").click()
    assert not Scene.objects.filter(pk=made.pk).exists()
    expect(page.locator('[data-scene-picker] [aria-current="page"]')).to_have_text(
        "Welcome"
    )
    expect(page.locator('a[aria-label="Back"]')).to_have_attribute("href", "/setup/")


def hold_the_tap(page):
    """Holds the scene POST in a CDP session of the test's own and returns
    the function that lets it go, so the test lets the tap through only
    after it has released the board's polls (#316). Letting go ends the
    hold, so a later tap posts as usual."""
    hold = page.context.new_cdp_session(page)
    paused = []
    hold.on("Fetch.requestPaused", lambda e: paused.append(e["requestId"]))
    hold.send(
        "Fetch.enable",
        {"patterns": [{"urlPattern": "*/kiosks/scene/*", "requestStage": "Request"}]},
    )

    def let_go():
        for _ in range(500):
            if paused:
                break
            page.wait_for_timeout(20)
        assert paused, "the tap sent no POST within 10 s"
        hold.send("Fetch.continueRequest", {"requestId": paused[0]})
        hold.send("Fetch.disable")
        hold.detach()

    return let_go


def test_a_tap_while_a_poll_is_out_is_not_lost(login, event):
    """A scene tapped while a board poll is in flight reaches the server,
    even when that poll's reply changes the board. Queued behind the poll,
    the tap belonged to a button the reply then replaced, and htmx drops
    the request of a detached element (#46 CI: the switch never posted).

    The board's route stays to the end of the test and lets a later poll
    through once released. `hold_the_tap` lets the tap's POST go only
    after that release, on every run (#316)."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene(event, "Racing")
    htmx_settled(page)
    held = []
    released = []

    def board(route):
        if released:
            route.continue_()
        else:
            held.append(route)

    page.route("**/kiosks/board/**", board)
    let_go = hold_the_tap(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    # Wait for the held poll itself, not the board's htmx-request class: a
    # poll sent before the route can still be out, and its reply, carrying
    # the new name, could come between the click's mousedown and mouseup.
    # Before D102 that reply swapped the switcher under the press, and no
    # POST went out (CI on #193 and #199; #316's `swap_mid_click` probe).
    # The board now holds that swap until the click
    # (`test_a_press_that_spans_a_board_swap_still_taps`), and this test
    # still waits for the poll it holds. The board polls one at a time
    # (`hx-sync="this:drop"`), so with this one held no reply comes.
    for _ in range(500):
        if held:
            break
        page.wait_for_timeout(20)
    assert held, "no poll was out within 10 s"
    expect(page.locator("#kiosk-board.htmx-request")).to_have_count(1)
    with htmx_post(page, "/kiosks/scene/"):
        scene_button(page, racing).click()
        released.append(True)
        for route in held:
            route.continue_()  # the poll the tap aborted
        let_go()
    expect(scene_button(page, racing)).to_have_attribute("aria-pressed", "true")
    assert held, "no poll was out"
    next_polls(page, 1)  # the route lets a later poll through
    # A second tap, after the hold has ended, posts as usual.
    welcome = scene(event, "Welcome")
    with htmx_post(page, "/kiosks/scene/"):
        tap(page, scene_button(page, welcome))
    expect(scene_button(page, welcome)).to_have_attribute("aria-pressed", "true")


def test_a_tap_follows_a_card_a_poll_swaps_while_bound_waits(login, event):
    """A poll's reply swaps the board just after `bound()` first resolves a
    card's Page button to an element (#425). htmx's cleanup of the
    swapped-out button deletes its `initHash`, so a wait on that element
    alone never ends; `tap()` follows the button to the card swapped in,
    clicks it only once htmx has bound it, and opens its Page sheet, under
    the new name.

    The control stands in for the locator and lets the held poll through
    the first time `bound()` asks it for an element, so the swap lands
    inside the real `tap()` on every run. htmx binds a swapped-in button
    at once (`defaultSettleDelay` 0), so the test unbinds the new one, has
    htmx bind it again only when `bound()` asks for the button again, and
    records whether the click found it bound."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    page_button = card(page, "tv").locator('[data-action="page"]')
    unbind_and_watch_the_click = """() => {
      const button = document.querySelector(
        '#kiosk-board [data-kiosk="tv"] [data-action="page"]');
      delete button['htmx-internal-data'].initHash;
      document.addEventListener('click', (e) => {
        const control = e.target.closest('[hx-get]');
        window.clickedBound = !!(control['htmx-internal-data'] || {}).initHash;
      }, {capture: true, once: true});
    }"""

    class SwapThenRebind:
        resolves = 0

        def __getattr__(self, name):
            return getattr(page_button, name)

        def element_handle(self):
            self.resolves += 1
            handle = page_button.element_handle()
            if self.resolves == 1:
                polls = hold_the_polls(page, stretch=False)
                Kiosk.objects.filter(address="tv").update(name="Lobby")
                polls.wait_for(1)
                polls.answer(0)
                polls.end()
                assert not handle.evaluate("e => e.isConnected"), "no swap"
                page.evaluate(unbind_and_watch_the_click)
            elif self.resolves == 2:
                handle.evaluate("e => htmx.process(e)")
            return handle

    control = SwapThenRebind()
    tap(page, control)
    assert page.evaluate("window.clickedBound") is True, "clicked before the bind"
    assert control.resolves == 2, "bound() took the button before and after the swap"
    expect(page.locator("#sheet-kiosks-title")).to_have_text("Page for Lobby")
    ready(page, sheet(page, "kiosks").locator("[data-page-sheet]"))


# A control outside the board, so no poll swaps it; htmx binds it only when
# a test asks.
STAND_IN = """() => {
  const control = document.createElement('button');
  control.id = 'stand-in';
  control.setAttribute('hx-get', '/');
  document.body.append(control);
}"""


@pytest.mark.parametrize(
    ("swaps", "outcome"),
    [
        (MAX_SWAPS, nullcontext()),
        (
            MAX_SWAPS + 1,
            pytest.raises(
                AssertionError,
                match=f"swapped out {MAX_SWAPS + 1} times and never bound",
            ),
        ),
    ],
    ids=["at-the-cap", "past-the-cap"],
)
def test_bound_follows_swaps_up_to_its_cap(login, event, swaps, outcome):
    """`bound()` takes a control's element again after each swap, up to
    MAX_SWAPS swaps, and past them fails naming the swaps (#466 review).
    The control swaps the stand-in out each time `bound()` takes its
    element, `swaps` times, and then has htmx bind the element it gives."""
    page = open_dashboard(login("RaceCoordinator"))
    page.evaluate(STAND_IN)
    stand_in = page.locator("#stand-in")

    class SwapEachResolve:
        resolves = 0

        def element_handle(self):
            self.resolves += 1
            handle = stand_in.element_handle()
            handle.evaluate(
                "e => e.replaceWith(e.cloneNode(true))"
                if self.resolves <= swaps
                else "e => htmx.process(e)"
            )
            return handle

    control = SwapEachResolve()
    with outcome:
        bound(page, control)
    assert control.resolves == MAX_SWAPS + 1, "bound() stopped taking elements"


def test_bound_waits_on_the_element_swapped_in(login, event):
    """After a swap, `bound()` waits on the element swapped in, and a
    timeout there names the swap (#466 review). The control swaps the
    stand-in out the first time `bound()` takes its element and shortens
    Playwright's timeout the next time; htmx never binds the element
    swapped in, so the outcome does not depend on the timeout's length."""
    page = open_dashboard(login("RaceCoordinator"))
    page.evaluate(STAND_IN)
    stand_in = page.locator("#stand-in")

    class SwapOnce:
        resolves = 0

        def element_handle(self):
            self.resolves += 1
            handle = stand_in.element_handle()
            if self.resolves == 1:
                handle.evaluate("e => e.replaceWith(e.cloneNode(true))")
            else:
                page.set_default_timeout(1_000)
            return handle

    with pytest.raises(
        AssertionError,
        match="showed no element htmx bound within Playwright's timeout, after 1 swaps",
    ):
        bound(page, SwapOnce())
    page.set_default_timeout(30_000)  # Playwright's default


@pytest.mark.parametrize(
    "selector", ["#stand-in", "#no-such-control"], ids=["never-bound", "never-shown"]
)
def test_bound_names_the_control_it_timed_out_on(login, event, selector):
    """A control htmx never binds, or a control the page never shows, fails
    `bound()` with a message naming the control once Playwright's timeout
    passes, not Playwright's own (#466 review). Nothing here binds later,
    so the test shortens Playwright's timeout and the outcome does not
    depend on its length."""
    page = open_dashboard(login("RaceCoordinator"))
    page.evaluate(STAND_IN)
    page.set_default_timeout(1_000)
    with pytest.raises(
        AssertionError,
        match=rf"bound\(\): .*'{selector}'.* showed no element htmx bound "
        "within Playwright's timeout, after 0 swaps",
    ):
        bound(page, page.locator(selector))
    page.set_default_timeout(30_000)  # Playwright's default


# ----- a press that spans a board swap (D102, #344) --------------------------

# The page's own record of a press, in the order the page saw it: each
# pointerdown, keydown and keyup (at its event's timeStamp, which is no later
# than any listener that handles it), each element that takes the focus, each
# click and each htmx request sent, each board poll answered and each other
# request ended, each swap of the board's children with the name the tv card
# shows after it, and each script error the page reports at the window.
BOARD_LOG = """() => {
  const log = window.boardLog = [];
  const board = document.getElementById('kiosk-board');
  const name = () => {
    const el = board.querySelector('[data-kiosk="tv"] [data-name]');
    return el ? el.textContent.trim() : null;
  };
  const now = () => performance.now();
  new MutationObserver(() => log.push({what: 'swap', at: now(), name: name()}))
    .observe(board, {childList: true});
  document.addEventListener(
    'pointerdown', (e) => log.push({what: 'down', at: e.timeStamp}), true);
  const on = (el) => el === document.body ? 'BODY' : el.outerHTML.slice(0, 60);
  const key = (e) => log.push({
    what: e.type, at: e.timeStamp, key: e.key, repeat: e.repeat, on: on(e.target)});
  document.addEventListener('keydown', key, true);
  document.addEventListener('keyup', key, true);
  document.addEventListener(
    'focusin', (e) => log.push({what: 'focus', at: now(), on: on(e.target)}), true);
  window.addEventListener(
    'error', (e) => log.push({what: 'error', message: e.message}));
  document.addEventListener('click', () => log.push({what: 'click', at: now()}), true);
  document.addEventListener('htmx:beforeSend', (e) => log.push({
    what: 'send', at: now(), verb: e.detail.requestConfig.verb,
    path: e.detail.requestConfig.path}), true);
  document.addEventListener('htmx:afterRequest', (e) => {
    if (e.detail.elt === board) {
      log.push({what: 'poll', at: now(), status: e.detail.xhr.status});
    } else {
      log.push({what: 'done', at: now(), path: e.detail.requestConfig.path});
    }
  });
}"""

# The hold's bound, one poll interval from the pointerdown (D102, #344),
# stretched to an hour: a test that releases the press by hand would race
# it under load otherwise. The bound's own test,
# `test_a_press_never_released_holds_the_board_one_poll_interval`, runs with
# the page's.
STRETCH_THE_BOUND = """() => {
  Alpine.$data(document.querySelector('[data-kiosk-dashboard]')).pressBound = 3600000;
}"""

# The Preview link's click, recorded at the window, the last listener
# before the browser follows the link: whether the link was still in the
# page, and where it leads.
FOLLOWED = """() => window.addEventListener('click', (e) => {
  const link = e.target.closest('a[data-action="preview"]');
  if (!link) return;
  const connected = link.isConnected;
  window.boardLog.push({what: 'followed', connected, href: link.href});
})"""

PAGE_BUTTON = '#kiosk-board [data-kiosk="tv"] button[data-action="page"]'
PREVIEW_LINK = '#kiosk-board [data-kiosk="tv"] a[data-action="preview"]'
IDENTIFY_BUTTON = '#kiosk-board [data-kiosk="tv"] button[data-action="identify"]'


class HeldPolls:
    """Holds each board poll at its route until the test answers it, so the
    reply comes while the pointer is down, and no other poll swaps the board
    behind the test's back. `end()` lets every poll through; a test that
    passes calls it, and the harness drains what it lets go."""

    def __init__(self, page):
        self.page = page
        self.held = []
        self.ended = False
        page.route("**/kiosks/board/**", self.route)

    def route(self, route):
        if self.ended:
            route.continue_()
        else:
            self.held.append(route)

    def wait_for(self, count):
        """Wait until the board has sent `count` polls since the hold."""
        for _ in range(500):
            if len(self.held) >= count:
                return
            self.page.wait_for_timeout(20)
        raise AssertionError(f"{len(self.held)} of {count} polls held within 10 s")

    def answer(self, index, status=200):
        """Let poll `index` through and wait until the page has handled its
        reply: a new board (200), or no change (204); either, for `None`."""
        self.held[index].continue_()
        self.page.wait_for_function(
            "n => window.boardLog.filter((r) => r.what === 'poll').length >= n",
            arg=index + 1,
        )
        polls = [r for r in board_log(self.page) if r["what"] == "poll"]
        assert status is None or polls[index]["status"] == status, polls

    def end(self):
        self.ended = True
        for route in self.held[self.answered() :]:
            route.continue_()

    def answered(self) -> int:
        return len([r for r in board_log(self.page) if r["what"] == "poll"])


def hold_the_polls(page, stretch=True) -> HeldPolls:
    """Record the board (`BOARD_LOG`), stretch the hold's bound, and hold
    the board's polls. Wait for any poll out to settle first, so the first
    poll held is one sent after the change the test makes next."""
    htmx_settled(page)
    page.evaluate(BOARD_LOG)
    if stretch:
        page.evaluate(STRETCH_THE_BOUND)
    return HeldPolls(page)


def board_log(page) -> list[dict]:
    return page.evaluate("window.boardLog")


def press(page, control):
    """Put the mouse down on the middle of `control`, and leave it down."""
    bound(page, control)
    dismiss_toasts(page)
    box = control.bounding_box()  # one read: the press point
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()


def touch(page):
    """A CDP session for touches, which Playwright sends only whole
    (`tap()`)."""
    return page.context.new_cdp_session(page)


def touch_start(cdp, page, control):
    bound(page, control)
    dismiss_toasts(page)
    box = control.bounding_box()  # one read: the press point
    point = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})


def after(log, what, start=0) -> int:
    """The index of the first `what` in `log` from `start` on."""
    return next(i for i, r in enumerate(log) if i >= start and r["what"] == what)


def swaps_since(log, start) -> list[dict]:
    return [r for r in log[start:] if r["what"] == "swap"]


def wait_for_a_swap_after(page, start):
    page.wait_for_function(
        "n => window.boardLog.slice(n).some((r) => r.what === 'swap')", arg=start
    )


def a_press_spans_a_swap(page, event, control):
    """The board shows a renamed kiosk, the mouse goes down on `control`,
    and the poll's reply with the new name comes while it is down. Returns
    the held polls."""
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    press(page, control)
    polls.answer(0)
    return polls


@pytest.mark.parametrize("control", ["scene", "page", "preview"])
def test_a_press_that_spans_a_board_swap_still_taps(login, event, control):
    """D102: a mouse press on a board control whose pointerdown comes
    before a poll's reply, and its pointerup after it, still taps the
    control: the scene POST goes out, the Page sheet loads, the Preview
    link is followed. No swap comes between the pointerdown and the click.
    Before #344 the reply replaced the control under the press, and
    Chromium sent no click (#316's `swap_mid_click` probe; PHP's
    kiosk-dashboard.php loses a mouse press on Reload the same way, the
    PR's probe).

    The held board waits for the end of the request the click sent. The
    scene's reply brings a newer board out of band, so that is the one
    swap after the click, and the scene shows on. The Page sheet's reply
    brings none, and the sheet is open, so the held board is dropped: the
    board swaps with the next poll after the sheet closes, and the focus
    goes back to Page, which nothing replaced (#344 review). The Preview
    link opens the kiosk's page in a tab of its own, and the window's last
    click listener records that the link was still in the page then (#378:
    the harness tracks the popup's navigation request while the board's
    frames navigate)."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene(event, "Racing")
    target = {
        "scene": scene_button(page, racing),
        "page": page.locator(PAGE_BUTTON),
        "preview": page.locator(PREVIEW_LINK),
    }[control]
    polls = a_press_spans_a_swap(page, event, target)
    page.evaluate(FOLLOWED)
    if control == "scene":
        with htmx_post(page, "/kiosks/scene/"):
            page.mouse.up()
    elif control == "page":
        with page.expect_response(lambda r: "/kiosks/page/" in r.url and r.ok):
            page.mouse.up()
    else:
        with page.context.expect_page() as opened:
            page.mouse.up()
        preview = opened.value
    log = board_log(page)
    down = after(log, "down")
    click = after(log, "click", down)
    assert after(log, "poll", down) < click, log
    assert swaps_since(log[:click], down) == [], log
    if control == "scene":
        expect(scene_button(page, racing)).to_have_attribute("aria-pressed", "true")
        log = board_log(page)
        assert log[after(log, "send", click)]["path"] == "/kiosks/scene/", log
        # one read: htmx applies the reply's board and ends the request in
        # one task, so a held board applied after it would be here too.
        assert [r["name"] for r in swaps_since(log, click)] == ["Lobby"], log
    elif control == "page":
        expect(sheet(page, "kiosks").locator("form")).to_be_visible()
        log = board_log(page)
        assert log[after(log, "send", click)]["path"].startswith("/kiosks/page/"), log
        done = after(log, "done", click)
        assert log[done]["path"].startswith("/kiosks/page/"), log
        # one read: a held board is applied in the GET's end, logged above.
        assert swaps_since(log, click) == [], log
        page.keyboard.press("Escape")
        expect(sheet(page, "kiosks")).to_be_hidden()
        expect(target).to_be_focused()
        polls.wait_for(2)
        polls.answer(1)
        log = board_log(page)
        assert [r["name"] for r in swaps_since(log, click)] == ["Lobby"], log
    else:
        followed = log[after(log, "followed", click)]
        assert followed["connected"], log
        assert "/kiosk.php?page=" in followed["href"], log
        expect(preview).to_have_url(followed["href"])
        wait_for_a_swap_after(page, click)
        close_tab(page, preview)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    polls.end()


@pytest.mark.sizes("phone", reason="touch input is checked at the phone's size only")
def test_a_touch_that_spans_a_board_swap_waits_for_its_click(login, event):
    """A touch on a scene button across the same reply. Chromium hit-tests
    a touch's click again, so the unheld board loses no touch (the PR's
    probe, on PHP's page too); the hold still waits for the click, which a
    touch sends after its pointerup and pointerout, before it applies the
    board (D102)."""
    page = login("RaceCoordinator")
    kiosk(event, "tv", name="Main")
    open_dashboard(page)
    racing = scene(event, "Racing")
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    cdp = touch(page)
    touch_start(cdp, page, scene_button(page, racing))
    polls.answer(0)
    with htmx_post(page, "/kiosks/scene/"):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    log = board_log(page)
    down = after(log, "down")
    sent = after(log, "send", down)
    assert log[sent]["verb"] == "post", log
    assert swaps_since(log[:sent], down) == [], log
    expect(scene_button(page, racing)).to_have_attribute("aria-pressed", "true")
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    polls.end()


@pytest.mark.parametrize(
    "cancel",
    [
        "moved off",
        pytest.param(
            "pointercancel",
            marks=pytest.mark.sizes(
                "phone",
                reason="touch input is checked at the phone's size only",
            ),
        ),
    ],
)
def test_a_cancelled_press_applies_only_the_newest_held_board(login, event, cancel):
    """Two polls answer while the pointer is down on a scene button, the
    first renaming the kiosk Lobby and the second Hall. The press is then
    cancelled, by moving the mouse off the button or by a pointercancel (a
    touch the browser takes for a scroll), and the board swaps once, to
    Hall (D102). No scene is posted. The `moved off` case runs at both
    sizes; the `pointercancel` case, a touch, is marked to the phone's size
    only."""
    page = login("RaceCoordinator")
    kiosk(event, "tv", name="Main")
    open_dashboard(page)
    racing = scene(event, "Racing")
    button = scene_button(page, racing)
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    if cancel == "moved off":
        press(page, button)
    else:
        cdp = touch(page)
        touch_start(cdp, page, button)
    polls.answer(0)
    Kiosk.objects.filter(address="tv").update(name="Hall")
    polls.wait_for(2)
    polls.answer(1)
    log = board_log(page)
    down = after(log, "down")
    assert swaps_since(log, down) == [], log
    if cancel == "moved off":
        move_off(page)
    else:
        cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
    wait_for_a_swap_after(page, down)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Hall")
    if cancel == "moved off":
        page.mouse.up()
    htmx_settled(page)
    log = board_log(page)
    assert [r["name"] for r in swaps_since(log, down)] == ["Hall"], log
    # one read: a click would have sent its POST before mouse.up() returned.
    assert [r for r in log[down:] if r["what"] == "click"] == [], log
    assert [r for r in log[down:] if r.get("verb") == "post"] == [], log
    expect(button).to_have_attribute("aria-pressed", "false")
    polls.end()


def test_a_change_reply_during_a_press_waits_too(login, event):
    """A change's reply carries the board out of band (`views.changed`),
    and that swap waits under a press as a poll's does (D102). The mouse
    is down on Welcome; a poll answers with the kiosk renamed Lobby, then
    Identify, pressed from the keyboard, answers with it renamed Hall. The
    mouse then moves off Welcome, and the board swaps once, to the newest
    board held, Hall."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    welcome = scene_button(page, scene(event, "Welcome"))
    polls = a_press_spans_a_swap(page, event, welcome)
    Kiosk.objects.filter(address="tv").update(name="Hall")
    page.locator(IDENTIFY_BUTTON).focus()
    with htmx_post(page, "/kiosks/identify/"):
        page.keyboard.press("Enter")
    log = board_log(page)
    down = after(log, "down")
    assert swaps_since(log, down) == [], log
    move_off(page)
    wait_for_a_swap_after(page, down)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Hall")
    page.mouse.up()
    htmx_settled(page)
    log = board_log(page)
    assert [r["name"] for r in swaps_since(log, down)] == ["Hall"], log
    expect(welcome).to_have_attribute("aria-pressed", "false")
    polls.end()


def held_at_route(page, pattern) -> list:
    """Hold each request matching `pattern` at its route; the test lets it
    go with `continue_()`."""
    held = []
    page.route(pattern, lambda route: held.append(route))
    return held


def wait_for_held(page, held, what):
    for _ in range(500):
        if held:
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"no {what} within 10 s")


def wait_for_done(page, path):
    page.wait_for_function(
        "p => window.boardLog.some((r) => r.what === 'done' && r.path.startsWith(p))",
        arg=path,
    )


def move_off(page):
    """Move the mouse off a scene button, onto the switcher's heading."""
    heading = page.locator("#ks-scenes-h")
    box = heading.bounding_box()  # one read: a point off the button
    page.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)


# The timers a click's listeners set wait until the next pointerdown has been
# handled, then run: the order a click's timer that runs late gives (#455).
# Once only: later clicks' timers run as usual, and so does a timer set
# outside a click, such as the board poll's. `window.lateTimers` counts those
# parked, records when each ran, and the pointerdown that let them go: its
# timeStamp and its button's scene.
LATE_CLICK_TIMERS = """() => {
  const real = window.setTimeout;
  const late = window.lateTimers = {parked: 0, ran: [], down: null};
  const parked = [];
  window.setTimeout = function (fn, ms, ...rest) {
    if (window.event?.type === 'click') {
      parked.push(() => fn(...rest));
      late.parked += 1;
      return 0;
    }
    return real.call(window, fn, ms, ...rest);
  };
  window.addEventListener('pointerdown', (e) => {
    window.setTimeout = real;
    late.down = {at: e.timeStamp, scene: e.target.closest('button')?.dataset.scene};
    for (const run of parked) real(() => { late.ran.push(performance.now()); run(); });
  }, {once: true});
}"""


def test_a_press_during_a_taps_request_still_taps(login, event):
    """The mouse taps Identify while a poll's reply is held, so the held
    board waits for Identify's POST, which its route holds. While that POST
    is out, the mouse goes down on Welcome, and Identify's reply then comes:
    its board waits under the new press, and so does the board held before
    (D102). Welcome's POST still goes out. The release Identify's click
    defers runs only after Welcome's pointerdown (`LATE_CLICK_TIMERS`), and
    leaves Welcome's press holding: before #455 it ended that press, and
    Identify's board swapped in under it. The test sets a timer of its own
    outside a click, which the forcing must leave alone, as it leaves the
    board poll's."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    welcomed = scene(event, "Welcome")
    welcome = scene_button(page, welcomed)
    posts = held_at_route(page, "**/kiosks/identify/**")
    polls = a_press_spans_a_swap(page, event, page.locator(IDENTIFY_BUTTON))
    page.evaluate(LATE_CLICK_TIMERS)
    page.evaluate("setTimeout(() => {})")
    page.mouse.up()
    wait_for_held(page, posts, "Identify POST")
    press(page, welcome)
    # one read: mouse.down() returns once the page has handled its pointerdown.
    log = board_log(page)
    second = [i for i, r in enumerate(log) if r["what"] == "down"][1]
    late = page.evaluate("window.lateTimers")
    assert late["down"] == {"at": log[second]["at"], "scene": str(welcomed.pk)}, (
        late,
        log,
    )
    assert late["parked"] == 1, late
    posts[0].continue_()
    wait_for_done(page, "/kiosks/identify/")
    log = board_log(page)
    late = page.evaluate("window.lateTimers")
    assert len(late["ran"]) == 1, late
    assert late["ran"][0] > log[second]["at"], (late, log)
    assert swaps_since(log, second) == [], log
    with htmx_post(page, "/kiosks/scene/"):
        page.mouse.up()
    expect(welcome).to_have_attribute("aria-pressed", "true")
    log = board_log(page)
    click = after(log, "click", second)
    assert swaps_since(log[:click], second) == [], log
    polls.end()


def test_a_tap_whose_request_fails_still_applies_the_held_board(
    login, event, page_errors
):
    """The mouse taps Identify while a poll's reply (Lobby) is held, so the
    held board waits for Identify's POST, which its route holds. The POST
    then fails with a 500, whose reply carries no board, and the held board
    swaps in when the request ends: no later poll is let through (#344)."""
    page_errors.allow(r"500")
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    posts = held_at_route(page, "**/kiosks/identify/**")
    polls = a_press_spans_a_swap(page, event, page.locator(IDENTIFY_BUTTON))
    page.mouse.up()
    wait_for_held(page, posts, "Identify POST")
    log = board_log(page)
    click = after(log, "click", after(log, "down"))
    assert swaps_since(log, click) == [], log
    posts[0].fulfill(status=500, body="refused")
    expect(page.locator("#toasts [role=alert]")).to_contain_text("HTTP 500")
    wait_for_a_swap_after(page, click)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    log = board_log(page)
    assert [r["name"] for r in swaps_since(log, click)] == ["Lobby"], log
    polls.end()


def test_a_change_from_a_sheet_swaps_its_board_at_once(login, event):
    """With no press on, a change's board out of band swaps in with its
    reply (#344): the kiosk named from its sheet shows on the board while
    every poll is held. Held to the request's end instead, the board never
    showed the name (#344's round-5 mutant).

    The board polls while no sheet is open (`derbynet.pollable`), so a poll
    goes out before the tap and again once the sheet closes; the test waits
    for each. The hold starts once the sheet is open, and nothing after the
    reply waits for htmx to settle, which waits on the held poll's
    `.htmx-request` (#437)."""
    kiosk(event, "tv")
    page = open_dashboard(login("RaceCoordinator"))
    with page.expect_request(lambda r: "/kiosks/board/" in r.url, timeout=10_000):
        pass
    tap(page, card(page, "tv").locator('[data-action="name"]'))
    form = ready(page, sheet(page, "kiosks").locator("[data-name-sheet]"))
    polls = hold_the_polls(page)
    form.locator('[data-name-chips] button[data-value="Main"]').click()
    expect(form.get_by_label("Name for this kiosk")).to_have_value("Main")
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/kiosks/name/" in r.url
    ) as reply:
        form.locator("[data-submit]").click()
    assert reply.value.ok, reply.value.status
    polls.wait_for(1)
    expect(sheet(page, "kiosks")).to_be_hidden()
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Main")
    assert polls.answered() == 0, board_log(page)
    polls.end()


def test_a_poll_with_no_change_during_a_press_swaps_nothing(login, event):
    """A poll that answers 204 while the pointer is down holds nothing, and
    the press's end swaps nothing (D102). No kiosk checks in, so nothing on
    the board changes with the clock (a quiet kiosk's card gains its last
    contact, board.html) and the poll answers 204."""
    page = open_dashboard(login("RaceCoordinator"))
    buttons = page.locator("#kiosk-board button[data-scene]")
    count = buttons.count()
    assert count, "no scene buttons: the test checks nothing"
    polls = hold_the_polls(page)
    polls.wait_for(1)
    press(page, scene_button(page, scene(event, "Racing")))
    polls.answer(0, status=204)
    move_off(page)
    page.mouse.up()
    htmx_settled(page)
    log = board_log(page)
    # one read: the move off applies a held board at once, before it returns.
    assert swaps_since(log, after(log, "down")) == [], log
    expect(buttons).to_have_count(count)
    polls.end()


def test_a_newer_board_during_a_taps_request_wins(login, event):
    """The mouse taps Page while a poll's reply, the kiosk renamed Lobby, is
    held, and the sheet's GET is held at its route. The sheet is closed,
    and a poll sent before the click then answers with the kiosk renamed
    Hall, and swaps the board at once, since no press is on. That swap
    drops the held board, the older (#344 review): when the GET ends, the
    board swapped once after the click, to Hall. The GET's body opens the
    sheet again (`kioskSheet.show`); once it is closed, a later press holds
    and applies a board as usual."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    gets = held_at_route(page, "**/kiosks/page/**")
    polls = a_press_spans_a_swap(page, event, page.locator(PAGE_BUTTON))
    polls.wait_for(2)
    page.mouse.up()
    wait_for_held(page, gets, "Page GET")
    page.keyboard.press("Escape")
    expect(sheet(page, "kiosks")).to_be_hidden()
    Kiosk.objects.filter(address="tv").update(name="Hall")
    polls.answer(1)
    gets[0].continue_()
    # The Hall board detached the Page button, and the log's listener on
    # the document hears no end of the GET (asserted below); the sheet's
    # form is swapped in in the task that ends it.
    expect(sheet(page, "kiosks").locator("form")).to_be_visible()
    log = board_log(page)
    click = after(log, "click", after(log, "down"))
    # one read: a held board is applied in the GET's end, the task that
    # swapped the form in.
    assert [r["name"] for r in swaps_since(log, click)] == ["Hall"], log
    assert [r for r in log if r["what"] == "done"] == [], log
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Hall")
    # Nothing is left waiting, the GET's end included: a later press across
    # a poll's reply, the kiosk renamed Cove, swaps once on its move off, to
    # Cove.
    page.keyboard.press("Escape")
    expect(sheet(page, "kiosks")).to_be_hidden()
    Kiosk.objects.filter(address="tv").update(name="Cove")
    polls.wait_for(3)
    later = len(board_log(page))
    press(page, scene_button(page, scene(event, "Racing")))
    polls.answer(2)
    move_off(page)
    page.mouse.up()
    log = board_log(page)
    # one read: the move off applies a held board at once, before it returns.
    assert [r["name"] for r in swaps_since(log, later)] == ["Cove"], log
    polls.end()


def test_a_sheets_reply_during_a_press_is_not_held(login, event):
    """Page is tapped from the keyboard, and the sheet's GET is held at its
    route. The sheet is closed, the mouse goes down on Welcome, and the GET
    then answers: htmx fires its swap's events on the sheet's body, not the
    board, so the hold never sees it, and the body opens the sheet again
    (`kioskSheet.show`). The board does not swap."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    welcome = scene_button(page, scene(event, "Welcome"))
    gets = held_at_route(page, "**/kiosks/page/**")
    polls = hold_the_polls(page)
    page.locator(PAGE_BUTTON).focus()
    page.keyboard.press("Enter")
    wait_for_held(page, gets, "Page GET")
    page.keyboard.press("Escape")
    expect(sheet(page, "kiosks")).to_be_hidden()
    press(page, welcome)
    gets[0].continue_()
    expect(sheet(page, "kiosks").locator("form")).to_be_visible()
    log = board_log(page)
    assert swaps_since(log, after(log, "down")) == [], log
    page.mouse.up()
    page.keyboard.press("Escape")
    expect(sheet(page, "kiosks")).to_be_hidden()
    polls.end()


# The scene POST that a second tap's `hx-sync="#kiosk-board:replace"`
# aborts, allowed in that test alone.
ABORTED_SCENE = r"request failed: \S*/kiosks/scene/\S* net::ERR_ABORTED$"


@pytest.mark.parametrize("second", ["keyboard", "mouse"])
def test_a_second_tap_that_aborts_a_taps_request_still_posts(
    login, event, page_errors, second
):
    """The mouse taps Racing while a poll's reply (Lobby) is held, and
    Racing's POST is held at its route. Welcome is then tapped, from the
    keyboard or with the mouse, and its `hx-sync="#kiosk-board:replace"`
    aborts Racing's POST. The aborted request drops the held board, as the
    request that aborted it brings a newer one, so nothing swaps the board
    before Welcome's POST goes out, with its CSRF header, and Welcome shows
    on (#344 review: the board applied in the abort detached Welcome
    mid-send, which then posted with no CSRF header and was refused)."""
    page_errors.allow(ABORTED_SCENE)
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene_button(page, scene(event, "Racing"))
    welcome = scene_button(page, scene(event, "Welcome"))
    first = []
    page.route(
        "**/kiosks/scene/**",
        lambda route: route.continue_() if first else first.append(route),
    )
    polls = a_press_spans_a_swap(page, event, racing)
    page.mouse.up()
    wait_for_held(page, first, "Racing POST")
    with htmx_post(page, "/kiosks/scene/"):
        if second == "keyboard":
            welcome.focus()
            page.keyboard.press("Enter")
        else:
            press(page, welcome)
            page.mouse.up()
    expect(welcome).to_have_attribute("aria-pressed", "true")
    log = board_log(page)
    click = after(log, "click", after(log, "down"))
    posts = [
        i
        for i, r in enumerate(log)
        if r["what"] == "send" and r["path"] == "/kiosks/scene/"
    ]
    assert len(posts) == 2, log
    assert swaps_since(log[: posts[1]], click) == [], log
    polls.end()


@pytest.mark.sizes("phone", reason="touch input is checked at the phone's size only")
def test_a_second_finger_keeps_the_hold(login, event):
    """A finger is down on Racing when a poll's reply (Lobby) comes, and a
    second finger then goes down on Welcome. The second pointerdown keeps
    the board held for the newer press (#344 review); the touch's cancel
    then releases it, and the board swaps once, to Lobby. Checked at the
    phone's size only."""
    page = login("RaceCoordinator")
    kiosk(event, "tv", name="Main")
    open_dashboard(page)
    racing = scene_button(page, scene(event, "Racing"))
    welcome = scene_button(page, scene(event, "Welcome"))
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    cdp = touch(page)
    touch_start(cdp, page, racing)
    polls.answer(0)
    bound(page, welcome)
    one = racing.bounding_box()  # one read: the first finger's point
    two = welcome.bounding_box()  # one read: the second finger's point
    points = [
        {"x": b["x"] + b["width"] / 2, "y": b["y"] + b["height"] / 2, "id": n}
        for n, b in enumerate((one, two))
    ]
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": points})
    page.wait_for_function(
        "window.boardLog.filter((r) => r.what === 'down').length >= 2"
    )
    log = board_log(page)
    down = after(log, "down")
    assert swaps_since(log, down) == [], log
    cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
    wait_for_a_swap_after(page, down)
    htmx_settled(page)
    log = board_log(page)
    assert [r["name"] for r in swaps_since(log, down)] == ["Lobby"], log
    assert [r for r in log[down:] if r.get("verb") == "post"] == [], log
    polls.end()


def test_a_press_never_released_holds_the_board_one_poll_interval(login, event):
    """A pointer that never comes up holds the board no longer than one
    poll interval, `state.POLL_SECONDS`, from its pointerdown (#344's
    Risks). The swap comes no sooner than the pointerdown's timeStamp plus
    the bound, since a timer never fires early. It comes no later than one
    more poll interval of slack after the later of that and the reply: the
    reply comes after the bound under load, and then nothing holds it."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    polls = hold_the_polls(page, stretch=False)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    press(page, scene_button(page, scene(event, "Racing")))
    polls.answer(0)
    log = board_log(page)
    down = after(log, "down")
    wait_for_a_swap_after(page, down)
    log = board_log(page)
    bound_ms = state.POLL_SECONDS * 1000
    swap = swaps_since(log, down)[0]
    reply = log[after(log, "poll", down)]
    assert swap["at"] >= log[down]["at"] + bound_ms, log
    assert swap["at"] <= max(log[down]["at"] + bound_ms, reply["at"]) + bound_ms, log
    assert swap["name"] == "Lobby", log
    page.mouse.move(1, 1)
    page.mouse.up()
    polls.end()


RELOAD_BUTTON = '#kiosk-board [data-kiosk="tv"] button[data-action="reload"]'
POST_PATHS = {"scene": "/kiosks/scene/", "reload": "/kiosks/reload/"}


def key_target(page, event, control):
    return {
        "scene": scene_button(page, scene(event, "Racing")),
        "reload": page.locator(RELOAD_BUTTON),
    }[control]


def focus_on(page, control):
    bound(page, control)
    dismiss_toasts(page)
    control.focus()


def posts_since(log, start) -> list[str]:
    return [r["path"] for r in log[start:] if r.get("verb") == "post"]


def tapped(page, control):
    """What a tap on Racing or on Reload shows: Racing on, or the card
    Reloading."""
    shows, text = {
        "scene": (page.locator("#kiosk-board [aria-pressed=true]"), "Racing"),
        "reload": (card(page, "tv"), "Reloading"),
    }[control]
    expect(shows).to_contain_text(text)


@pytest.mark.parametrize("control", ["scene", "reload"])
def test_a_space_press_that_spans_a_board_swap_still_taps(login, event, control):
    """D125 (#369): Space goes down on a focused board button, a poll's
    reply (the kiosk renamed Lobby) comes, the key repeats twice, and Space
    comes up; the board ignores the repeats (D131), not the press. The button
    still taps: Racing's POST goes out and Racing shows on, or Reload's POST
    goes out and the card shows Reloading. No swap comes between the keydown
    and the click; the POST's reply then brings the board out of band, and
    the focus comes back to the swapped-in button (D127). Before the fix the
    poll's reply replaced the focused button, the focus fell to the page's
    body, and Space's keyup went to the body and sent no click (#344's probe;
    PHP's kiosk-dashboard.php loses the key on Reload the same way, the PR's
    probe)."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    target = key_target(page, event, control)
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    focus_on(page, target)
    page.keyboard.down(" ")
    polls.answer(0)
    page.keyboard.down(" ")  # two auto-repeats during the hold (D131)
    page.keyboard.down(" ")
    with htmx_post(page, POST_PATHS[control]):
        page.keyboard.up(" ")
    tapped(page, control)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    expect(target).to_be_focused()
    log = board_log(page)
    keydown = after(log, "keydown")
    keyup = after(log, "keyup", keydown)
    click = after(log, "click", keydown)
    assert after(log, "poll", keydown) < keyup < click, log
    assert log[keyup]["on"] != "BODY", log[keydown:]
    assert len([r for r in log if r["what"] == "keydown" and r["repeat"]]) == 2, log
    assert swaps_since(log[:click], keydown) == [], log
    assert posts_since(log, keydown) == [POST_PATHS[control]], log
    # one read: htmx applies the reply's board and ends the request in one
    # task, so a held board applied after it would be here too.
    assert [r["name"] for r in swaps_since(log, click)] == ["Lobby"], log
    polls.end()


@pytest.mark.parametrize("key, step", [("Tab", 1), ("Shift+Tab", -1)])
def test_a_space_press_whose_focus_moves_off_applies_the_held_board(
    login, event, key, step
):
    """D125: Space is down on Racing when a poll's reply (Lobby) comes, and
    the board waits. Tab or Shift+Tab then moves the focus off Racing to
    the scene button after or before it, which ends the press: the board
    swaps to Lobby, and the focus stays on the swapped-in copy of that
    button, where the crew put it (D127). The keyup that follows taps
    nothing, so no scene is posted and Racing stays off. Before the #369
    review the swap ran inside the focusout, which removed the button the
    focus was moving to, and D127 then gave the focus back to Racing."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene_button(page, scene(event, "Racing"))
    scenes = page.locator("#kiosk-board button[data-scene]")
    ids = scenes.evaluate_all("bs => bs.map((b) => b.dataset.scene)")
    neighbour = ids[ids.index(racing.get_attribute("data-scene")) + step]
    moved_to = page.locator(f'#kiosk-board button[data-scene="{neighbour}"]')
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    focus_on(page, racing)
    page.keyboard.down(" ")
    polls.answer(0)
    log = board_log(page)
    keydown = after(log, "keydown")
    assert swaps_since(log, keydown) == [], log
    page.keyboard.press(key)
    wait_for_a_swap_after(page, keydown)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    expect(moved_to).to_be_focused()
    page.keyboard.up(" ")
    page.wait_for_function("window.boardLog.some((r) => r.what === 'keyup')")
    htmx_settled(page)
    log = board_log(page)
    assert [r["name"] for r in swaps_since(log, keydown)] == ["Lobby"], log
    # one read: a click would have sent its POST before keyboard.up()
    # returned.
    assert posts_since(log, keydown) == [], log
    expect(racing).to_have_attribute("aria-pressed", "false")
    polls.end()


@pytest.mark.parametrize(
    "order, ends",
    [
        ("pointer then Space", "pointer moves off"),
        ("Space then pointer", "pointer moves off"),
        ("pointer then Space", "focus moves off"),
    ],
)
def test_a_pointer_and_space_on_one_button_hold_until_both_end(
    login, event, order, ends
):
    """D102 and D125 together (#369 review): the mouse and Space both go
    down on Racing, in either order, and a poll's reply (Lobby) comes. The
    mouse moving off Racing ends the pointer's part only, and Space still
    holds the board; or Tab moves the focus off Racing, which ends Space's
    part only, and the mouse still holds it. Nothing swaps. The other one
    then taps Racing: Space's keyup with the focus still on it, or the
    mouse's release over it. The scene's reply is the one swap after the
    click."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene_button(page, scene(event, "Racing"))
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    focus_on(page, racing)
    first, second = {
        "pointer then Space": (
            lambda: press(page, racing),
            lambda: page.keyboard.down(" "),
        ),
        "Space then pointer": (
            lambda: page.keyboard.down(" "),
            lambda: press(page, racing),
        ),
    }[order]
    first()
    second()
    expect(racing).to_be_focused()
    polls.answer(0)
    end, tap = {
        "pointer moves off": (lambda: move_off(page), lambda: page.keyboard.up(" ")),
        "focus moves off": (lambda: page.keyboard.press("Tab"), page.mouse.up),
    }[ends]
    end()
    log = board_log(page)
    down = after(log, "down")
    start = min(down, after(log, "keydown"))
    # one read: a pointerout's release swaps the board before the step
    # returns, and a focusout's on a 0 ms timer queued before this one.
    page.evaluate("() => new Promise((r) => setTimeout(r, 0))")
    log = board_log(page)
    assert swaps_since(log, start) == [], log
    with htmx_post(page, "/kiosks/scene/"):
        tap()
    expect(racing).to_have_attribute("aria-pressed", "true")
    log = board_log(page)
    click = after(log, "click", start)
    assert swaps_since(log[:click], start) == [], log
    assert [r["name"] for r in swaps_since(log, click)] == ["Lobby"], log
    page.keyboard.up(" ")
    page.mouse.up()
    polls.end()


def test_a_space_press_never_released_holds_the_board_one_poll_interval(login, event):
    """D125: Space held on Racing holds the board no longer than one poll
    interval, `state.POLL_SECONDS`, from its first keydown, as D102's bound
    does for a pointer, for a keyup that never comes. The key's auto-repeat
    keydowns, sent every 250 ms here until the swap, do not extend it. The
    swap comes no sooner than the first keydown's timeStamp plus the bound,
    since a timer never fires early, and no later than one more poll
    interval of slack after the later of that and the reply. The focus then
    comes back to the swapped-in Racing (D127); one more repeat lands on it,
    and the board ignores it (D131), so the keyup taps nothing: no scene is
    posted and Racing stays off. Without D131, Chromium clicked the restored
    Racing on the keyup (the PR's stand-in probe, `repeat-on-new.log`)."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene_button(page, scene(event, "Racing"))
    polls = hold_the_polls(page, stretch=False)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    focus_on(page, racing)
    page.keyboard.down(" ")
    polls.answer(0)
    log = board_log(page)
    keydown = after(log, "keydown")
    for _ in range(40):  # 10 s of repeats at most
        if swaps_since(board_log(page), keydown):
            break
        page.keyboard.down(" ")
        page.wait_for_timeout(250)
    log = board_log(page)
    bound_ms = state.POLL_SECONDS * 1000
    swap = swaps_since(log, keydown)[0]
    reply = log[after(log, "poll", keydown)]
    assert swap["at"] >= log[keydown]["at"] + bound_ms, log
    assert swap["at"] <= max(log[keydown]["at"] + bound_ms, reply["at"]) + bound_ms, log
    assert swap["name"] == "Lobby", log
    assert any(r["what"] == "keydown" and r["repeat"] for r in log), log
    expect(racing).to_be_focused()
    page.keyboard.down(" ")
    page.keyboard.up(" ")
    page.wait_for_function("window.boardLog.some((r) => r.what === 'keyup')")
    log = board_log(page)
    # one read: a click would have sent its POST before keyboard.up()
    # returned.
    assert posts_since(log, keydown) == [], log
    expect(racing).to_have_attribute("aria-pressed", "false")
    polls.end()


def test_a_space_held_across_a_tab_taps_nothing(login, event):
    """D131's accepted cost: Space goes down on Racing, Tab moves the focus
    to the scene button after it, the key repeats once there, and comes up.
    The board ignores the repeat, so nothing is tapped and no scene is
    posted. Master's page, and Chromium on a plain page, tap the Tab target,
    since the repeat arms its click (the PR's probe, `spacetab/run.log`). No
    poll changes the board, so nothing swaps."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene_button(page, scene(event, "Racing"))
    htmx_settled(page)
    page.evaluate(BOARD_LOG)
    focus_on(page, racing)
    page.keyboard.down(" ")
    page.keyboard.press("Tab")
    page.wait_for_function(
        "id => document.activeElement.matches('#kiosk-board button[data-scene]')"
        " && document.activeElement.dataset.scene !== id",
        arg=racing.get_attribute("data-scene"),
    )
    page.keyboard.down(" ")
    page.keyboard.up(" ")
    page.wait_for_function("window.boardLog.some((r) => r.what === 'keyup')")
    htmx_settled(page)
    log = board_log(page)
    keydown = after(log, "keydown")
    repeats = [r for r in log if r["what"] == "keydown" and r["repeat"]]
    assert len(repeats) == 1 and repeats[0]["on"] != "BODY", log
    # one read: a click would have sent its POST before keyboard.up()
    # returned.
    assert posts_since(log, keydown) == [], log
    assert swaps_since(log, keydown) == [], log


def test_a_space_held_on_one_button_and_a_click_on_another_posts_once(login, event):
    """D131: Space is held on Racing, and the mouse clicks Reload. Reload's
    POST reply brings the board out of band, and the focus comes back to
    the swapped-in Reload (D127). The Space auto-repeat that then lands on
    it is ignored, so the keyup taps nothing: Reload's POST goes out once,
    and no scene is posted. Without D131 the repeat armed the restored
    Reload and the keyup clicked it, a second POST, where master's page,
    with the focus on the body, sent one (the PR's probe, `aclickb/`)."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene_button(page, scene(event, "Racing"))
    reload = page.locator(RELOAD_BUTTON)
    htmx_settled(page)
    page.evaluate(BOARD_LOG)
    focus_on(page, racing)
    page.keyboard.down(" ")
    with htmx_post(page, "/kiosks/reload/"):
        press(page, reload)
        page.mouse.up()
    log = board_log(page)
    keydown = after(log, "keydown")
    wait_for_a_swap_after(page, after(log, "click", keydown))
    expect(reload).to_be_focused()
    page.keyboard.down(" ")
    page.keyboard.up(" ")
    page.wait_for_function("window.boardLog.some((r) => r.what === 'keyup')")
    htmx_settled(page)
    log = board_log(page)
    repeats = [r for r in log if r["what"] == "keydown" and r["repeat"]]
    assert len(repeats) == 1 and repeats[0]["on"] != "BODY", log
    # one read: a click would have sent its POST before keyboard.up()
    # returned.
    assert posts_since(log, keydown) == ["/kiosks/reload/"], log
    expect(racing).to_have_attribute("aria-pressed", "false")


# A button outside the board, which counts its clicks: no page control off
# the board is a button at the phone's size.
OUTSIDE_BUTTON = """() => {
  const b = document.createElement('button');
  b.type = 'button';
  b.id = 'outside';
  b.textContent = 'Outside';
  b.dataset.clicks = '0';
  b.addEventListener('click', () => {
    b.dataset.clicks = String(Number(b.dataset.clicks) + 1);
  });
  document.querySelector('main').prepend(b);
}"""


@pytest.mark.parametrize(
    "where", ["shift on Racing", "space off the board", "space on Preview"]
)
def test_a_key_off_the_space_hold_holds_nothing(login, event, where):
    """Near misses for D125's hold: Shift held on a focused board button,
    Space held on a button off the board, and Space held on a card's
    Preview link, which Space does not activate (the PR's probe,
    `space-preview-*.log`). The poll's reply swaps the board while the key
    is down."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    page.evaluate(OUTSIDE_BUTTON)
    target, key = {
        "shift on Racing": (scene_button(page, scene(event, "Racing")), "Shift"),
        "space off the board": (page.locator("#outside"), " "),
        "space on Preview": (page.locator(PREVIEW_LINK), " "),
    }[where]
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    focus_on(page, target)
    page.keyboard.down(key)
    polls.answer(0)
    log = board_log(page)
    wait_for_a_swap_after(page, after(log, "keydown"))
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    page.keyboard.up(key)
    polls.end()


def test_a_held_enter_off_the_board_still_repeats(login, event):
    """A near miss for D126: a held Enter on a button off the board clicks
    on its first keydown and on each of its two repeats, 3 clicks, as
    Chromium does."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    page.evaluate(OUTSIDE_BUTTON)
    outside = page.locator("#outside")
    focus_on(page, outside)
    page.keyboard.down("Enter")
    page.keyboard.down("Enter")
    page.keyboard.down("Enter")
    page.keyboard.up("Enter")
    expect(outside).to_have_attribute("data-clicks", "3")


def test_a_held_tab_on_the_board_still_repeats(login, event):
    """A near miss for D126 and D131: a held Tab on a board button moves
    the focus on its first keydown and on its repeat, so 2 elements take
    the focus and it ends on the scene button 2 after Racing: only Enter's
    and Space's repeats are ignored."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene_button(page, scene(event, "Racing"))
    ids = page.locator("#kiosk-board button[data-scene]").evaluate_all(
        "bs => bs.map((b) => b.dataset.scene)"
    )
    two_on = ids[ids.index(racing.get_attribute("data-scene")) + 2]
    htmx_settled(page)
    page.evaluate(BOARD_LOG)
    focus_on(page, racing)
    start = len(board_log(page))
    page.keyboard.down("Tab")
    page.keyboard.down("Tab")
    page.keyboard.up("Tab")
    page.wait_for_function(
        "n => window.boardLog.slice(n).filter((r) => r.what === 'focus').length >= 2",
        arg=start,
    )
    log = board_log(page)
    keydowns = [r["repeat"] for r in log[start:] if r["what"] == "keydown"]
    assert keydowns == [False, True], log
    expect(page.locator(f'#kiosk-board button[data-scene="{two_on}"]')).to_be_focused()


@pytest.mark.parametrize("control", ["scene", "reload"])
def test_a_held_enter_is_one_tap(login, event, control):
    """D126: Enter goes down on Racing or Reload and taps it; the POST's
    reply brings the board out of band, and the focus comes back to the
    swapped-in button (D127). Two auto-repeat keydowns then land on that
    button, and neither taps it: one POST in all. A fresh Enter press after
    the keyup taps again, a second POST: only the repeats are ignored."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    target = key_target(page, event, control)
    htmx_settled(page)
    page.evaluate(BOARD_LOG)
    focus_on(page, target)
    with htmx_post(page, POST_PATHS[control]):
        page.keyboard.down("Enter")
    log = board_log(page)
    first = after(log, "keydown")
    wait_for_a_swap_after(page, first)
    expect(target).to_be_focused()
    page.keyboard.down("Enter")
    page.keyboard.down("Enter")
    page.keyboard.up("Enter")
    page.wait_for_function("window.boardLog.some((r) => r.what === 'keyup')")
    htmx_settled(page)
    log = board_log(page)
    repeats = [r for r in log if r["what"] == "keydown" and r["repeat"]]
    assert len(repeats) == 2, log
    assert all(r["on"] != "BODY" for r in repeats), log
    # one read: a click would have sent its POST before keyboard.down()
    # returned.
    assert posts_since(log, first) == [POST_PATHS[control]], log
    tapped(page, control)
    with htmx_post(page, POST_PATHS[control]):
        page.keyboard.press("Enter")
    log = board_log(page)
    assert posts_since(log, first) == [POST_PATHS[control]] * 2, log


@pytest.mark.parametrize("control", ["scene", "identify", "page", "preview"])
def test_the_focus_comes_back_after_a_board_swap(login, event, control):
    """D127: the focus is on a board control, Racing, the card's Identify,
    its Page or its Preview link, with no key held, when a poll's reply
    (Lobby) swaps the board. The focus comes back to the swapped-in copy of
    it: the same name, value, data-action and card. Identify shares its name
    and value with Reload, and Page and Preview have neither; a second
    kiosk, unnamed, whose card comes first on the board, has the same
    controls as the tv's. Shift+Tab then moves the focus to the control
    before it, and the next swap (Hall) gives the focus back to that one:
    each control that takes the focus, a restored one too, is the one the
    next swap gives it back to. The page reports no script error."""
    kiosk(event, "hall")
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    target = {
        "scene": scene_button(page, scene(event, "Racing")),
        "identify": page.locator(IDENTIFY_BUTTON),
        "page": page.locator(PAGE_BUTTON),
        "preview": page.locator(PREVIEW_LINK),
    }[control]
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    focus_on(page, target)
    old = target.element_handle()
    polls.answer(0)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    assert not old.evaluate("e => e.isConnected")
    expect(page.locator("[data-kiosk]").first).to_have_attribute("data-kiosk", "hall")
    expect(target).to_be_focused()
    page.keyboard.press("Shift+Tab")
    page.wait_for_function("document.activeElement !== document.body")
    before = page.evaluate_handle("document.activeElement")
    markup = before.evaluate("e => e.outerHTML")
    Kiosk.objects.filter(address="tv").update(name="Hall")
    polls.wait_for(2)
    polls.answer(1)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Hall")
    assert not before.evaluate("e => e.isConnected")
    page.wait_for_function(
        "m => document.activeElement.isConnected"
        " && document.activeElement.outerHTML === m",
        arg=markup,
    )
    assert [r for r in board_log(page) if r["what"] == "error"] == []
    polls.end()


@pytest.mark.parametrize("where", ["off the board", "blurred", "gone"])
def test_the_focus_stays_where_the_crew_put_it(login, event, where):
    """Near misses for D127: the focus is on the Scene editor link, off the
    board; or on Racing and then blurred to the page's body, as a tap on the
    page's background does; or on the card's Identify, and the kiosk drops
    off the board. A poll's reply swaps the board, a second poll answers
    after it, and no element takes the focus and the page reports no script
    error in all that time. The second
    poll may have gone out before the first reply, with the old board's
    signature, and then it swaps the board again."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    target = {
        "off the board": page.locator('[data-action="scene-editor"]'),
        "blurred": scene_button(page, scene(event, "Racing")),
        "gone": page.locator(IDENTIFY_BUTTON),
    }[where]
    focused_after = {"off the board": target}.get(where, page.locator("body"))
    tv = Kiosk.objects.filter(address="tv")
    change = {"gone": tv.delete}.get(where, lambda: tv.update(name="Lobby"))
    then = {"blurred": "document.activeElement.blur()"}.get(where, "0")
    polls = hold_the_polls(page)
    change()
    polls.wait_for(1)
    focus_on(page, target)
    page.evaluate(then)
    log = board_log(page)
    start = len(log)
    polls.answer(0)
    polls.wait_for(2)
    polls.answer(1, status=None)
    log = board_log(page)
    assert swaps_since(log, start), log
    assert [r for r in log[start:] if r["what"] in ("focus", "error")] == [], log
    expect(focused_after).to_be_focused()
    polls.end()


def test_the_focus_comes_back_without_scrolling_the_page(login, event):
    """D127: ten unnamed kiosks' cards come before the tv's, so its Identify
    is below the screen once the page is scrolled to the top. The focus is
    on it there when a poll's reply (Lobby) swaps the board: the focus
    comes back to the swapped-in Identify and the page does not scroll to
    it (`preventScroll`), so the crew's view stays where it was."""
    for n in range(10):
        kiosk(event, f"spare{n}")
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    identify = page.locator(IDENTIFY_BUTTON)
    polls = hold_the_polls(page)
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    polls.wait_for(1)
    focus_on(page, identify)
    page.evaluate("scrollTo(0, 0)")
    page.wait_for_function("scrollY === 0")
    assert identify.evaluate("e => e.getBoundingClientRect().top > innerHeight")
    polls.answer(0)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    expect(identify).to_be_focused()
    # one read: a focus() that scrolls does so before it returns, inside
    # the swap's task, which has run once the focus is back.
    assert page.evaluate("scrollY") == 0
    polls.end()


@pytest.mark.parametrize("where", ["card name", "scene editor"])
def test_a_press_off_the_board_controls_holds_nothing(login, event, where):
    """Near misses for D102's hold: a press on the card's name, inside the
    board but no control, and on the Scene editor link, a control outside
    the board. The poll's reply swaps the board while the mouse is down."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    target = {
        "card name": card(page, "tv").locator("[data-name]"),
        "scene editor": page.locator('[data-action="scene-editor"]'),
    }[where]
    polls = a_press_spans_a_swap(page, event, target)
    log = board_log(page)
    down = after(log, "down")
    wait_for_a_swap_after(page, down)
    expect(card(page, "tv").locator("[data-name]")).to_have_text("Lobby")
    page.mouse.move(1, 1)
    page.mouse.up()
    polls.end()
