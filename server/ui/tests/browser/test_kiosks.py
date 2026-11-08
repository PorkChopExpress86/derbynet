"""The kiosk dashboard and the scene editor in a browser (#46): a scene
switch that holds through the polls (C19), three fake kiosks that follow a
scene tapped on the phone, as `testing/test-scenes.sh` drives them, the
slideshow Configure sheet showing what was saved (C20), the parity boxes of
`kiosk-dashboard.php` and `scenes.php`, and the screenshots."""

import json
import re
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from kiosks.madlib import madlib
from kiosks.models import Kiosk, Scene, SceneKiosk
from roster.models import Group
from ui.tests.browser.conftest import (
    assert_accessible,
    close_tab,
    dismiss_toasts,
    htmx_post,
    htmx_settled,
    shoot,
)

ADDRESSES = ("kiosk1-test46", "kiosk2-test46", "kiosk3-test46")
ABORTED_POLL = r"request failed: \S*/kiosks/board/\S* net::ERR_ABORTED$"
# What a thumbnail loads (#237): its stylesheets and images.
ABORTED_THUMBNAIL = (
    r"request failed: \S*/(static/ui/\S+\.(css|png)|(image|slide|photo)\.php/\S+)"
    r" net::ERR_ABORTED$"
)


@pytest.fixture(autouse=True)
def _polls_may_be_cut_short(page_errors):
    """A change aborts a board poll still in flight
    (`hx-sync="#kiosk-board:replace"`), so the tap is sent at once and no
    stale poll swaps after its reply. The browser reports the aborted poll
    as a failed request. Each swap also replaces every thumbnail, and the
    browser reports a stylesheet or image a replaced thumbnail was still
    loading as failed too
    (`test_kiosk_thumbnails.py::test_a_board_swap_while_a_thumbnail_loads`)."""
    page_errors.allow(ABORTED_POLL)
    page_errors.allow(ABORTED_THUMBNAIL)


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check is at 390x844")


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


def test_three_fake_kiosks_follow_a_scene_tapped_on_the_phone(login, event):
    """The steps of `testing/test-scenes.sh`: the kiosks check in and get
    identify, are named, and then each scene tapped gives each named kiosk
    its page, read back through `query=poll.kiosk`."""
    phone_only(page := login("RaceCoordinator"))
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


def test_a_tap_while_a_poll_is_out_is_not_lost(login, event):
    """A scene tapped while a board poll is in flight reaches the server,
    even when that poll's reply changes the board. Queued behind the poll,
    the tap belonged to a button the reply then replaced, and htmx drops
    the request of a detached element (#46 CI: the switch never posted)."""
    kiosk(event, "tv", name="Main")
    page = open_dashboard(login("RaceCoordinator"))
    racing = scene(event, "Racing")
    htmx_settled(page)
    held = []
    page.route("**/kiosks/board/**", lambda route: held.append(route))
    Kiosk.objects.filter(address="tv").update(name="Lobby")
    # Wait for the held poll itself, not the board's htmx-request class: a
    # poll sent before the route can still be out, and its reply, carrying
    # the new name, could swap the switcher between the click's mousedown
    # and mouseup. The click then lands on the board, not the button, and
    # no POST goes out (CI on #193 and #199, held open by a mouse.down()
    # with the reply released before mouse.up()). The board polls one at a
    # time (`hx-sync="this:drop"`), so with this one held no reply comes.
    for _ in range(500):
        if held:
            break
        page.wait_for_timeout(20)
    assert held, "no poll was out within 10 s"
    expect(page.locator("#kiosk-board.htmx-request")).to_have_count(1)
    with htmx_post(page, "/kiosks/scene/"):
        scene_button(page, racing).click()
        page.unroute("**/kiosks/board/**")
        for route in held:
            try:
                route.continue_()
            except Exception:
                pass  # the tap aborted it
    expect(scene_button(page, racing)).to_have_attribute("aria-pressed", "true")
    assert held, "no poll was out"
