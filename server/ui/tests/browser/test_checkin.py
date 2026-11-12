"""Race check-in in a browser (#42): the acceptance flows at 390x844 and
1280x800, the checkin.php and mcheckin.php parity boxes, and the screenshots."""

import os
import re
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import expect

from awards.models import Award
from core.models import RaceSettings
from media.models import PhotoRotation
from racing.models import FinalRequest, HeatLane, Round
from racing.services import rounds
from racing.services.rounds import FollowOn
from roster.models import Partition, Racer
from roster.services import groups
from roster.services.racers import pass_racer
from roster.services.weigh_in import record_weigh_in, set_partition_weigh_in
from ui.tests.browser.conftest import (
    PLAIN_HTTP_HOST,
    SIZES,
    assert_accessible,
    dismiss_toasts,
    htmx_post,
    htmx_settled,
    log_in,
    no_sideways_scroll,
    shoot,
)
from ui.tests.race import add_racers, chart, race_setup, run_heat, scheduled

TESTING = Path(__file__).resolve().parents[4] / "testing" / "data"
CAR_PHOTO = TESTING / "carphotos" / "Car-1637.jpg"
EXIF_6 = TESTING / "carphotos" / "pattern-o6.jpg"
ABORTED = r"request failed: \S*/checkin/(list|number)/\S* net::ERR_ABORTED$"


@pytest.fixture(autouse=True)
def _polls_may_be_cut_short(page_errors):
    """A filter change or a mutation aborts a poll still in flight
    (`hx-sync`), which the browser reports as a failed request."""
    page_errors.allow(ABORTED)


@pytest.fixture
def folders(event, tmp_path):
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(tmp_path / "racers"),
        car_photo_directory=str(tmp_path / "cars"),
    )
    (tmp_path / "racers").mkdir()
    (tmp_path / "cars").mkdir()
    return tmp_path


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check is at 390x844")


def open_checkin(page):
    page.goto("/checkin/")
    expect(page.locator("#checkin-list")).to_be_visible()
    return page


def sheet(page):
    return page.locator('[data-sheet="checkin"]')


def card(page, racer):
    return page.locator(f'[data-racer="{racer.pk}"]')


def take_photo(page, path, button="Capture only"):
    """Pick a photo through the file input, as a phone's camera hands it
    over, and upload it."""
    photo = sheet(page)
    photo.locator("[data-pick]").set_input_files(str(path))
    # The browser decodes and downscales the photo before the preview shows
    # (photo-sheets.js `downscale`), which is CPU work: under load it
    # outran the 5 s default once in 250 runs (#70). Reproduced by delaying
    # `createImageBitmap` 6 s.
    expect(photo.locator("[data-preview]")).to_be_visible(timeout=20_000)
    photo.get_by_role("button", name=button).click()


def crop(page):
    crop_sheet = sheet(page).locator("[data-crop-sheet]")
    expect(crop_sheet).to_be_visible()
    button = crop_sheet.get_by_role("button", name="Crop", exact=True)
    expect(button).to_be_enabled()
    button.click()
    expect(sheet(page)).to_be_hidden()


# ----- the owner's station flow (D6) -----------------------------------------------


def add_racer(page, first, last, car_name, partition):
    page.get_by_role("button", name="New racer").click()
    form = sheet(page)
    # The class's change refreshes the car number only once htmx has bound
    # the hint's trigger.
    expect(form.locator('[data-racer-sheet="new"]')).to_be_visible()
    htmx_settled(page)
    form.get_by_label("First name").fill(first)
    form.get_by_label("Last name").fill(last)
    form.get_by_label("Car name").fill(car_name)
    form.get_by_label("Class").select_option(label=partition)
    return form


def test_the_owners_station_flow(login, event, folders):
    """For a scouts racer and a non-scouts racer: name, car name and class,
    the class's next car number, a car photo, and the pass. With
    photos-on-now-racing set to car, the photo is in the data the now-racing
    kiosk reads."""
    add_racers(event, "Scouts", 3, 101)
    groups.find_or_create_partition(event.id, "Non-Scouts")
    page = open_checkin(login("RaceCrew"))

    for first, partition, number, button in (
        ("Sam", "Scouts", "104", "Capture and check in"),
        ("Nora", "Non-Scouts", "201", "Capture only"),
    ):
        form = add_racer(page, first, "Station", f"{first}'s Rocket", partition)
        expect(form.locator("#f-carno")).to_have_value(number)
        if first == "Sam":
            shoot(page, "checkin-add-sheet", full_page=False)
        dismiss_toasts(page)
        form.get_by_role("button", name="Add racer").click()
        expect(form.locator("[data-notice]")).to_have_text(
            f"Added {number} {first} Station."
        )
        form.get_by_role("button", name="Take car photo").click()
        take_photo(page, CAR_PHOTO, button)
        crop(page)
        racer = Racer.objects.get(event=event, first_name=first)
        expect(card(page, racer).locator('[data-thumb="car"]')).to_be_visible()
        if button == "Capture only":
            dismiss_toasts(page)
            card(page, racer).get_by_role(
                "switch", name=f"Passed, {first} Station"
            ).click()
            expect(card(page, racer)).to_have_attribute("data-passed", "1")
        racer.refresh_from_db()
        assert racer.passed_inspection and racer.car_number == int(number)
        assert racer.car_photo.endswith(".jpg")
    sam = Racer.objects.get(event=event, first_name="Sam")
    assert sam.partition.name == "Scouts"

    RaceSettings.objects.filter(event=event).update(
        photos_on_now_racing="car", lane_count=4
    )
    scheduled(Round.objects.get(event=event, group=sam.group, number=1))
    reply = page.request.get(
        "/action.php?query=poll&values=current-heat,racers&row-height=100"
    ).json()
    photos = {r["racerid"]: r.get("photo", "") for r in reply["racers"]}
    assert "photo.php/car/" in photos[sam.pk], photos


def test_an_empty_roster_adds_the_first_racer_as_101_in_default(login, event):
    """Replaces testing/puppeteer/checkin-empty-test.js."""
    page = open_checkin(login("RaceCoordinator"))
    expect(page.locator("[data-empty]")).to_contain_text("No racers yet")
    page.get_by_role("button", name="New racer").click()
    form = sheet(page)
    expect(form.get_by_label("Class")).to_have_value("")
    expect(form.get_by_label("Class").locator("option")).to_have_text(["Default"])
    expect(form.locator("#f-carno")).to_have_value("101")
    form.get_by_label("First name").fill("Fred")
    form.get_by_label("Last name").fill("Flintstone")
    form.get_by_role("button", name="Add racer").click()
    expect(form.locator("[data-notice]")).to_have_text("Added 101 Fred Flintstone.")
    form.locator("[data-racer-form]").get_by_role("button", name="Close").click()
    expect(page.locator("[data-racer]")).to_have_count(1)
    racer = Racer.objects.get(event=event)
    assert (racer.car_number, racer.partition.name) == (101, "Default")


def test_an_empty_roster_refuses_a_nameless_den_and_renames_default(login, event):
    """The rest of testing/puppeteer/checkin-empty-test.js: on an empty
    roster a first den with no name is refused and none is made (#166);
    then the first racer's Default den, a real partition, renamed to
    Unique Partition, shows so on the racer's card."""
    page = open_checkin(login("RaceCoordinator"))
    page.get_by_role("button", name="New racer").click()
    sheet(page).get_by_role("link", name="Edit partitions").click()
    page.wait_for_url(re.compile(r"/groups/partitions/$"))
    partitions = page.locator('[data-sheet="groups"] [data-partitions-sheet]')
    expect(partitions).to_be_visible()
    expect(partitions.locator("[data-partition-row]")).to_have_count(0)

    partitions.get_by_role("button", name="Add", exact=True).click()

    expect(partitions.locator('[data-error="no-name"]')).to_be_visible()
    expect(partitions.locator("[data-partition-row]")).to_have_count(0)
    assert not Partition.objects.filter(event=event).exists()

    open_checkin(page)
    page.get_by_role("button", name="New racer").click()
    form = sheet(page)
    expect(form.get_by_label("Class")).to_have_value("")
    form.get_by_label("First name").fill("Fred")
    form.get_by_label("Last name").fill("Flintstone")
    form.get_by_role("button", name="Add racer").click()
    expect(form.locator("[data-notice]")).to_have_text("Added 101 Fred Flintstone.")
    default = Partition.objects.get(event=event)
    assert default.name == "Default"

    page.goto("/groups/partitions/")
    partitions = page.locator('[data-sheet="groups"] [data-partitions-sheet]')
    rows = partitions.locator("[data-partition-row]")
    expect(rows).to_have_count(1)
    expect(rows).to_have_attribute("data-label", "Default")
    expect(rows.locator("[data-edit-partition]")).to_have_text("Default (1)")
    rows.locator("[data-edit-partition]").click()
    body = page.locator('[data-sheet="groups"]')
    body.locator("#pe-name").fill("Unique Partition")
    body.get_by_role("button", name="Rename").click()
    expect(body.locator("[data-sheet-notice]")).to_have_text(
        "Renamed to Unique Partition."
    )
    assert Partition.objects.get(pk=default.pk).name == "Unique Partition"

    open_checkin(page)
    racer = Racer.objects.get(event=event)
    expect(card(page, racer).locator("[data-partition]")).to_have_text(
        "Unique Partition"
    )


# ----- D36: the question at check-in ------------------------------------------------


@pytest.fixture
def round_running(event):
    """Scouts round 1 with one heat raced and heats to come, and a racer
    who has not checked in."""
    wolves = race_setup(event, groups=(("Scouts", 8),))["Scouts"]
    scheduled(wolves)
    run_heat(event)
    late = add_racers(event, "Scouts", 1, 150, passed=False)[0]
    return wolves, late


@pytest.mark.parametrize("answer", ["Yes", "No"])
def test_a_pass_during_round_1_asks_to_add_the_car(
    login, browser, live_server, event, round_running, answer
):
    """Yes puts the car into the remaining heats with no preview and leaves
    the running and staged heats; No leaves the chart, and the dashboard
    lists the racer as not added (D36)."""
    wolves, late = round_running
    page = login("RaceCrew")
    phone_only(page)
    open_checkin(page)
    before = chart(wolves)
    page.get_by_label("Find a racer by name or car number").fill("150")
    expect(page.locator("[data-racer]")).to_have_count(1)

    card(page, late).get_by_role("switch", name="Passed, Scouts1 Racer").click()
    question = sheet(page).locator(f'[data-question="{late.pk}"]')
    expect(question).to_contain_text(
        "Scouts, Round 1 is in progress. Add this car to the current round?"
    )
    if answer == "Yes":
        shoot(page, "checkin-question", full_page=False)
    question.get_by_role("button", name=answer, exact=True).click()
    expect(sheet(page)).to_be_hidden()
    expect(page.locator("#toasts")).to_contain_text(
        "added to Scouts, Round 1"
        if answer == "Yes"
        else "stays out of Scouts, Round 1"
    )

    after = chart(wolves)
    if answer == "Yes":
        # Heat 2 runs and heat 3 is staged: both keep their cars.
        assert after[:3] == before[:3]
        assert any(150 in heat for heat in after[3:])
        assert not any(150 in heat for heat in before)
        return
    assert after == before
    expect(card(page, late).locator("[data-left-out]")).to_contain_text(
        "Not added to Scouts, Round 1"
    )
    coordinator = browser.new_context(**SIZES["phone"], base_url=live_server.url)
    dashboard = coordinator.new_page()
    log_in(dashboard, "RaceCoordinator")
    dashboard.goto("/dashboard/")
    expect(dashboard.locator(f'[data-not-added-row="{late.pk}"]')).to_contain_text(
        "150 Scouts1 Racer"
    )
    coordinator.close()


# ----- D32: a request for a place in the final -----


def test_a_racer_after_round_1_requests_a_place_in_the_final(
    login, browser, live_server, event
):
    wolves = race_setup(event, groups=(("Scouts", 8),))["Scouts"]
    scheduled(wolves)
    while HeatLane.objects.filter(round=wolves, finish_time__isnull=True).exists():
        run_heat(event)
    late = add_racers(event, "Scouts", 1, 150)[0]
    page = login("RaceCrew")
    phone_only(page)
    open_checkin(page)

    late_card = card(page, late)
    expect(late_card).not_to_contain_text("Late!")
    late_card.get_by_role("button", name="Request place in final").click()
    expect(late_card.locator('[data-final="requested"]')).to_contain_text(
        "waiting for the race coordinator"
    )
    expect(late_card.locator("[data-awards]")).to_have_text(
        "Not eligible for speed awards"
    )
    shoot(page, "checkin-final-request")
    request = FinalRequest.objects.get(racer=late)

    final = rounds.new_round_roster(FollowOn(wolves), 3).round
    assert not final.roster.filter(racer=late).exists()
    coordinator = browser.new_context(**SIZES["phone"], base_url=live_server.url)
    dashboard = coordinator.new_page()
    log_in(dashboard, "RaceCoordinator")
    dashboard.goto("/dashboard/")
    request_card = dashboard.locator(f'[data-final-request="{request.pk}"]')
    expect(request_card).to_contain_text("150 Scouts1 Racer")
    assert not final.roster.filter(racer=late).exists()
    request_card.get_by_role("button", name="Allow").click()
    expect(request_card).to_have_count(0)
    assert final.roster.filter(racer=late).exists()
    coordinator.close()


# ----- the racer sheet (D33, D34) -----


def test_the_racer_sheet_saves_both_award_settings(login, event):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    page = open_checkin(login("RaceCrew"))
    card(page, racer).get_by_role("button", name="Scouts1 Racer", exact=True).click()
    form = sheet(page)
    form.get_by_label("Not eligible for any award").check()
    form.get_by_label("Not eligible for speed awards").check()
    form.get_by_role("button", name="Save").click()
    expect(sheet(page)).to_be_hidden()
    expect(card(page, racer).locator("[data-awards]")).to_have_text(
        "Not eligible for any award"
    )
    racer.refresh_from_db()
    assert racer.exclude and racer.not_eligible_for_speed_awards


def test_the_partition_field_lists_exactly_the_partitions(login, event):
    """A picker of the existing partitions with no typed entry, on the add
    and the edit sheet; picking another moves the racer (D34)."""
    racer = add_racers(event, "Scouts", 1, 101)[0]
    add_racers(event, "Siblings", 1, 201)
    groups.find_or_create_partition(event.id, "Parents")
    page = login("RaceCoordinator")
    phone_only(page)
    open_checkin(page)
    names = list(
        Partition.objects.filter(event=event)
        .order_by("sort_order")
        .values_list("name", flat=True)
    )

    page.get_by_role("button", name="New racer").click()
    field = sheet(page).get_by_label("Class")
    expect(field).to_have_js_property("tagName", "SELECT")
    expect(field.locator("option")).to_have_text(names)
    assert sheet(page).locator('input[name="partitionid"]').count() == 0
    expect(sheet(page).get_by_role("link", name="Edit partitions")).to_have_attribute(
        "href", "/groups/partitions/"
    )
    sheet(page).get_by_role("button", name="Cancel").click()

    card(page, racer).get_by_role("button", name="Scouts1 Racer", exact=True).click()
    field = sheet(page).get_by_label("Class")
    expect(field.locator("option")).to_have_text(names)
    field.select_option(label="Parents")
    sheet(page).get_by_role("button", name="Save").click()
    expect(card(page, racer).locator("[data-partition]")).to_have_text("Parents")
    racer.refresh_from_db()
    assert racer.partition.name == "Parents"


# ----- stations in sync, un-pass with undo, search ---------------------------------


def test_a_pass_on_one_station_shows_on_another_within_12_s(
    login, browser, live_server, event, size
):
    racer = add_racers(event, "Scouts", 1, 101, passed=False)[0]
    page = open_checkin(login("RaceCrew"))
    other = browser.new_context(**SIZES[size], base_url=live_server.url)
    second = other.new_page()
    log_in(second, "RaceCrew")
    open_checkin(second)
    expect(card(second, racer)).to_have_attribute("data-passed", "0")

    card(page, racer).get_by_role("switch").click()
    expect(card(page, racer)).to_have_attribute("data-passed", "1")
    expect(card(second, racer)).to_have_attribute("data-passed", "1", timeout=12_000)
    other.close()


def test_a_pass_whose_card_a_poll_replaced_still_refreshes_the_list(login, event):
    """A poll's swap can replace the card while its switch's POST is out. The
    reply's checkin-refresh must still reach the list, or the card shows
    "not passed" until the next poll, 10 s later (#45 retro). The test stands
    in for the poll by swapping the card for a copy right after the click."""
    racer = add_racers(event, "Scouts", 1, 101, passed=False)[0]
    page = login("RaceCrew")
    phone_only(page)
    open_checkin(page)
    expect(card(page, racer)).to_have_attribute("data-passed", "0")
    page.evaluate("derbynet.pollable = () => false")  # only the refresh may fetch

    with page.expect_response(f"**/checkin/racer/{racer.pk}/pass/") as reply:
        card(page, racer).evaluate(
            """(li) => {
                li.querySelector('[role=switch]').click();
                li.replaceWith(li.cloneNode(true));
            }"""
        )

    assert reply.value.ok
    expect(card(page, racer)).to_have_attribute("data-passed", "1", timeout=3_000)


def test_unpass_then_undo_leaves_the_racer_passed(login, event):
    """No confirm() dialog: the page fixture fails on any dialog."""
    racer = add_racers(event, "Scouts", 1, 101)[0]
    page = login("RaceCrew")
    phone_only(page)
    open_checkin(page)
    switch = card(page, racer).get_by_role("switch", name="Passed, Scouts1 Racer")
    expect(switch).to_have_attribute("aria-checked", "true")

    switch.click()
    toast = page.locator("#toasts [data-toast]").filter(has_text="is not passed")
    expect(toast).to_be_visible()
    expect(card(page, racer)).to_have_attribute("data-passed", "0")
    racer.refresh_from_db()
    assert not racer.passed_inspection
    toast.get_by_role("button", name="Undo").click()
    expect(card(page, racer)).to_have_attribute("data-passed", "1")
    racer.refresh_from_db()
    assert racer.passed_inspection


def test_search_narrows_the_list(login, event):
    scouts = add_racers(event, "Scouts", 3, 101)
    Racer.objects.filter(pk=scouts[2].pk).update(first_name="Zelda")
    page = open_checkin(login("RaceCrew"))
    search = page.get_by_label("Find a racer by name or car number")
    expect(page.locator("[data-racer]")).to_have_count(3)

    search.fill("102")
    expect(page.locator("[data-racer]")).to_have_count(1)
    expect(card(page, scouts[1])).to_be_visible()
    search.fill("zel")
    expect(page.locator("[data-racer]")).to_have_count(1)
    expect(card(page, scouts[2])).to_be_visible()
    search.fill("")
    expect(page.locator("[data-racer]")).to_have_count(3)


# ----- photos -----


@pytest.fixture
def plain_http(browser, live_server, size, page_errors):
    """A page on a plain HTTP origin, where the browser refuses the camera,
    as on a venue's LAN."""
    port = live_server.url.rsplit(":", 1)[1]
    context = browser.new_context(
        **SIZES[size], base_url=f"http://{PLAIN_HTTP_HOST}:{port}"
    )
    page = context.new_page()
    page.size = size
    page.on("pageerror", lambda error: page_errors.add(f"pageerror: {error}"))
    page.on(
        "console",
        lambda msg: msg.type == "error" and page_errors.add(f"console: {msg.text}"),
    )
    page.on("dialog", lambda dialog: page_errors.add(f"dialog: {dialog.message}"))
    yield page
    context.close()
    assert page_errors.errors == []


def big_photo(path: Path) -> Path:
    """A phone-sized photo larger than 8 MB: noise does not compress."""
    image = Image.frombytes("RGB", (3600, 2700), os.urandom(3600 * 2700 * 3))
    image.save(path, "JPEG", quality=95)
    assert path.stat().st_size > 8 * 1024 * 1024
    return path


def test_a_photo_over_plain_http_uploads_crops_and_shows(plain_http, event, folders):
    """The file input covers the photo over plain HTTP: no live camera is
    offered, nothing logs an error, no capture control can upload before a
    photo is picked, the sheet fits the screen, and a photo over 8 MB is
    downscaled in the browser."""
    racer = add_racers(event, "Scouts", 1, 101, passed=False)[0]
    page = plain_http
    log_in(page, "RaceCrew")
    open_checkin(page)
    assert page.evaluate("window.isSecureContext") is False

    card(page, racer).get_by_role("button", name="Car photo of Scouts1 Racer").click()
    photo = sheet(page).locator("[data-photo-sheet]")
    expect(photo).to_be_visible()
    expect(photo.locator("[data-live]")).to_have_count(0)
    for name in ("Capture and check in", "Capture only"):
        expect(photo.get_by_role("button", name=name)).to_be_disabled()
    no_sideways_scroll(page)
    for button in photo.get_by_role("button").all():
        button.scroll_into_view_if_needed()
        box = button.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]

    take_photo(page, big_photo(folders / "phone.jpg"), "Capture and check in")
    crop_sheet = sheet(page).locator("[data-crop-sheet]")
    expect(crop_sheet).to_be_visible()
    no_sideways_scroll(page)
    expect(crop_sheet.get_by_role("button", name="Crop", exact=True)).to_be_enabled()
    shoot(page, "checkin-crop-sheet", full_page=False)
    crop(page)
    expect(card(page, racer).locator('[data-thumb="car"]')).to_be_visible()
    racer.refresh_from_db()
    assert racer.passed_inspection
    assert racer.car_photo == "0-cropped/phone.jpg"
    stored = folders / "cars" / "phone.jpg"
    assert stored.stat().st_size < 2 * 1024 * 1024
    with Image.open(stored) as uploaded:
        assert max(uploaded.size) == 1600
    with Image.open(folders / "cars" / "0-cropped" / "phone.jpg") as cropped:
        width, height = cropped.size
        assert abs(width / height - 3 / 4) < 0.02


def test_an_exif_6_photo_renders_upright(login, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    page = open_checkin(login("RaceCrew"))
    card(page, racer).get_by_role("button", name="Car photo of Scouts1 Racer").click()
    take_photo(page, EXIF_6)
    expect(sheet(page).locator("[data-crop-sheet]")).to_be_visible()
    with Image.open(folders / "cars" / "pattern-o6.jpg") as stored:
        # Stored 320x480 with orientation 6, so upright it is 480 wide.
        assert stored.size == (480, 320)
        assert stored.getexif().get(0x0112) in (None, 1)
    image = sheet(page).locator("[data-crop-image]")
    natural = image.evaluate(
        """img => img.decode().then(() => [img.naturalWidth, img.naturalHeight])"""
    )
    assert natural[0] > natural[1]


def test_the_crop_sheet_turns_the_photo_left_and_right(login, event, folders):
    """Turn left and Turn right on the crop sheet turn the stored photo a
    quarter each way, and the sheet redraws it turned."""
    racer = add_racers(event, "Scouts", 1, 101)[0]
    landscape = folders / "wide.jpg"
    Image.new("RGB", (600, 450), (30, 120, 200)).save(landscape, "JPEG")
    page = open_checkin(login("RaceCrew"))
    phone_only(page)
    card(page, racer).get_by_role("button", name="Car photo of Scouts1 Racer").click()
    take_photo(page, landscape)
    crop_sheet = sheet(page).locator("[data-crop-sheet]")
    expect(crop_sheet).to_be_visible()

    def drawn(shape):
        """Wait until the sheet draws the photo in `shape`."""
        page.wait_for_function(
            """shape => {
                const img = document.querySelector('[data-crop-image]');
                if (!img || !img.complete || !img.naturalWidth) return false;
                return (img.naturalWidth > img.naturalHeight
                        ? 'landscape' : 'portrait') === shape;
            }""",
            arg=shape,
        )

    drawn("landscape")
    for button, degrees, turned in (
        ("Turn left", 90, "portrait"),
        ("Turn right", None, "landscape"),
    ):
        with htmx_post(page, f"/checkin/racer/{racer.pk}/photo/car/rotate/"):
            crop_sheet.get_by_role("button", name=button).click()
        # A rotation of 0 degrees is never kept (`PhotoRotation`).
        assert (
            PhotoRotation.objects.filter(name="wide.jpg")
            .values_list("degrees", flat=True)
            .first()
            == degrees
        )
        drawn(turned)


def test_the_live_camera_captures_with_autocrop(login, event, folders):
    """In a secure context (localhost) the sheet offers the live camera; its
    Capture waits for the stream (Chromium's fake camera)."""
    racer = add_racers(event, "Scouts", 1, 101, passed=False)[0]
    page = open_checkin(login("RaceCrew"))
    card(page, racer).get_by_role("button", name="Racer photo of Scouts1 Racer").click()
    photo = sheet(page).locator("[data-photo-sheet]")
    live = photo.locator("[data-live]")
    expect(live).to_be_visible()
    expect(photo.get_by_label("Autocrop to the face")).to_be_checked()
    live.get_by_role("button", name="Use the live camera").click()
    snap = live.get_by_role("button", name="Capture", exact=True)
    expect(snap).to_be_enabled(timeout=10_000)
    expect(live.get_by_label("Camera").locator("option")).not_to_have_count(0)
    snap.click()
    expect(photo.locator("[data-preview]")).to_be_visible()
    photo.get_by_role("button", name="Capture and check in").click()
    expect(sheet(page).locator("[data-crop-sheet]")).to_be_visible()
    racer.refresh_from_db()
    assert "camera" in racer.headshot and racer.passed_inspection
    expect(sheet(page).locator("[data-sheet-notice]")).to_contain_text(
        "Racer photo saved"
    )


# ----- parity with checkin.php ------------------------------------------------------


def test_sort_by_name_partition_car_and_class(login, event):
    a = add_racers(event, "Bears", 1, 300)[0]
    b = add_racers(event, "Wolves", 1, 100)[0]
    Racer.objects.filter(pk=a.pk).update(last_name="Adams")
    Racer.objects.filter(pk=b.pk).update(last_name="Zimmer")
    page = open_checkin(login("RaceCrew"))
    sort = page.get_by_role("group", name="Sort by")
    order = page.locator("[data-racer]")

    def ids():
        return [
            int(x) for x in order.evaluate_all("els => els.map(e => e.dataset.racer)")
        ]

    expect(order.first).to_have_attribute("data-racer", str(a.pk))
    sort.get_by_text("Car", exact=True).click()
    expect(order.first).to_have_attribute("data-racer", str(b.pk))
    sort.get_by_text("Den", exact=True).click()
    expect(order.first).to_have_attribute("data-racer", str(a.pk))
    sort.get_by_text("Class", exact=True).click()
    expect(order.first).to_have_attribute("data-racer", str(a.pk))
    sort.get_by_text("Name", exact=True).click()
    expect(order.first).to_have_attribute("data-racer", str(a.pk))
    assert ids() == [a.pk, b.pk]


def test_the_xbs_switch_when_enabled(login, event):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    page = open_checkin(login("RaceCrew"))
    expect(
        page.get_by_role("switch", name=re.compile("Exclusively By Scout"))
    ).to_have_count(0)
    RaceSettings.objects.filter(event=event).update(use_xbs=True)
    open_checkin(page)
    xbs = card(page, racer).get_by_role(
        "switch", name="Exclusively By Scout, Scouts1 Racer"
    )
    xbs.click()
    expect(page.locator("#toasts")).to_contain_text("Exclusively By Scout on")
    assert Award.objects.filter(racer=racer, name="Exclusively By Scout").exists()


def test_add_edit_and_delete_a_racer(login, event):
    add_racers(event, "Scouts", 1, 101)
    page = open_checkin(login("RaceCrew"))
    form = add_racer(page, "Del", "Me", "", "Scouts")
    expect(form.locator("#f-carno")).to_have_value("102")
    form.get_by_role("button", name="Add racer").click()
    expect(form.locator("[data-notice]")).to_be_visible()
    form.get_by_label("Car name").fill("Edited")
    form.get_by_role("button", name="Save").click()
    racer = Racer.objects.get(event=event, first_name="Del")
    expect(card(page, racer).locator("[data-car-name]")).to_have_text("Edited")

    card(page, racer).get_by_role("button", name="Del Me", exact=True).click()
    dismiss_toasts(page)
    sheet(page).get_by_role("button", name="Delete racer").click()
    sheet(page).get_by_role("button", name="Delete 1 racer").click()
    expect(card(page, racer)).to_have_count(0)


def test_a_duplicate_car_number_warns_as_it_is_typed(login, event):
    add_racers(event, "Scouts", 1, 101)
    page = open_checkin(login("RaceCrew"))
    form = add_racer(page, "Two", "Stations", "", "Scouts")
    form.locator("#f-carno").fill("101")
    expect(form.locator("[data-number-warnings]")).to_contain_text(
        "Car number 101 belongs to Scouts1 Racer too."
    )
    form.get_by_role("button", name="Add racer").click()
    expect(form.locator("[data-warnings]")).to_contain_text("Duplicate car number 101")
    expect(form.locator("[data-racer-form]")).to_be_visible()


def test_bulk_check_in_undo_renumber_and_eligibility(login, event):
    racers = add_racers(event, "Scouts", 3, 101, passed=False)
    page = open_checkin(login("RaceCoordinator"))

    def bulk(what, **choices):
        page.get_by_role("button", name="Bulk").click()
        form = sheet(page)
        form.get_by_label(what, exact=True).check()
        for label in choices.get("check", ()):
            form.get_by_label(label).check()
        if "start" in choices:
            form.get_by_label("First car number").fill(choices["start"])
        form.get_by_role("button", name="Apply").click()
        expect(sheet(page)).to_be_hidden()
        dismiss_toasts(page)

    bulk("Check in")
    expect(page.locator('[data-passed="1"]')).to_have_count(3)
    shoot_bulk = page.get_by_role("button", name="Bulk")
    shoot_bulk.click()
    expect(sheet(page).locator("[data-bulk]")).to_be_visible()
    shoot(page, "checkin-bulk-sheet", full_page=False)
    sheet(page).get_by_role("button", name="Cancel").click()
    bulk("Undo check-in")
    expect(page.locator('[data-passed="0"]')).to_have_count(3)
    bulk("Renumber", check=["Count up from"], start="500")
    expect(card(page, racers[0]).locator("[data-car]")).to_have_text("500")
    bulk("Award eligibility", check=["Not eligible for any award"])
    expect(page.locator("[data-awards]")).to_have_count(3)


def test_the_qr_code_for_another_phone(login, browser, live_server, event, size):
    page = open_checkin(login("RaceCrew"))
    page.get_by_role("button", name="Phone QR").click()
    qr = sheet(page).locator("[data-qr]")
    expect(qr.locator("svg")).to_be_visible()
    url = qr.locator("[data-qr-url]").inner_text()
    other = browser.new_context(**SIZES[size])
    phone = other.new_page()
    phone.goto(url)
    expect(phone).to_have_url(re.compile(r"/checkin/$"))
    expect(phone.locator("#checkin-list")).to_be_visible()
    other.close()


def test_phone_landscape_layout(browser, live_server, event, page_errors):
    add_racers(event, "Scouts", 4, 101)
    context = browser.new_context(
        viewport={"width": 844, "height": 390},
        has_touch=True,
        is_mobile=True,
        device_scale_factor=2,
        base_url=live_server.url,
    )
    page = context.new_page()
    page.size = "landscape"
    log_in(page, "RaceCrew")
    open_checkin(page)
    expect(page.locator("[data-racer]")).to_have_count(4)
    no_sideways_scroll(page)
    expect(page.locator("[data-racer]").first).to_be_in_viewport()
    shoot(page, "checkin-list")
    context.close()


# ----- screenshots and accessibility -----


def test_screenshots_and_accessibility(login, event, folders):
    RaceSettings.objects.filter(event=event).update(use_xbs=True)
    racers = add_racers(event, "Scouts", 4, 101, passed=False)
    add_racers(event, "Non-Scouts", 2, 201)
    Racer.objects.filter(pk=racers[0].pk).update(
        car_name="Blue Lightning", note="Pack 12"
    )
    pass_racer(racers[1], True)
    page = open_checkin(login("RaceCrew"))
    shoot(page, "checkin-list")
    assert_accessible(page)

    card(page, racers[0]).get_by_role(
        "button", name="Scouts1 Racer", exact=True
    ).click()
    expect(sheet(page).locator("[data-racer-form]")).to_be_visible()
    shoot(page, "checkin-racer-sheet", full_page=False)
    assert_accessible(page)
    sheet(page).get_by_role("button", name="Take car photo").click()
    expect(sheet(page).locator("[data-photo-sheet]")).to_be_visible()
    shoot(page, "checkin-photo-sheet", full_page=False)
    assert_accessible(page)


def test_the_status_filter(login, event):
    wolves = race_setup(event, groups=(("Scouts", 4),))["Scouts"]
    scheduled(wolves, race=False)
    late = add_racers(event, "Scouts", 1, 150)[0]
    page = open_checkin(login("RaceCrew"))
    expect(page.locator("[data-racer]")).to_have_count(5)
    page.get_by_label("Status").select_option(label="Late!")
    expect(page.locator("[data-racer]")).to_have_count(1)
    expect(card(page, late)).to_be_visible()
    page.get_by_label("Status").select_option(label="Racing!")
    expect(page.locator("[data-racer]")).to_have_count(4)


# ----- the weigh-in (#70, D8, D54) -------------------------------------------------


def weigh_partitions(event):
    """Scouts weigh pass or fail with a 5 oz maximum, Outlaws record each
    weight in ounces with a 5 oz maximum, and Tigers have no weigh-in."""
    add_racers(event, "Scouts", 2, 101, passed=False)
    add_racers(event, "Outlaws", 2, 201, passed=False)
    add_racers(event, "Tigers", 1, 301, passed=False)
    for name, mode in (("Scouts", "pass_fail"), ("Outlaws", "recorded")):
        partition = Partition.objects.get(event=event, name=name)
        set_partition_weigh_in(partition, mode, "oz", "5")


@pytest.mark.parametrize("partition", ["Scouts", "Outlaws"])
def test_the_owners_station_flow_with_a_weigh_in(login, event, folders, partition):
    """D6 with D8 and D54, on a phone: the volunteer weighs the car, enters
    the racer and car names, gets the class's next car number, takes the
    car photo, and passes the racer. Scouts weigh pass or fail, Outlaws
    record the weight."""
    page = login("RaceCrew")
    phone_only(page)
    weigh_partitions(event)
    open_checkin(page)

    page.get_by_role("button", name="New racer").click()
    form = sheet(page)
    expect(form.locator('[data-racer-sheet="new"]')).to_be_visible()
    htmx_settled(page)
    form.get_by_label("Class").select_option(label=partition)
    htmx_settled(page)
    number = "103" if partition == "Scouts" else "203"
    expect(form.locator("#f-carno")).to_have_value(number)
    if partition == "Scouts":
        weigh_fields = form.locator('[data-weigh-fields="pass_fail"]')
        expect(weigh_fields).to_contain_text("(max 5.0 oz)")
        weigh_fields.get_by_label("Pass").check()
    else:
        expect(form.locator('[data-weigh-fields="recorded"] [data-unit]')).to_have_text(
            "oz"
        )
        form.get_by_label("Car weight").fill("4.9")
    form.get_by_label("First name").fill("Wendy")
    form.get_by_label("Last name").fill("Weigh")
    form.get_by_label("Car name").fill("Feather")
    shoot(page, f"checkin-weigh-add-{partition.lower()}", full_page=False)
    form.get_by_role("button", name="Add racer").click()
    expect(form.locator("[data-notice]")).to_have_text(f"Added {number} Wendy Weigh.")
    racer = Racer.objects.get(event=event, first_name="Wendy")
    if partition == "Scouts":
        expect(form.locator("[data-weigh-result]")).to_have_text("Passed")
        assert (racer.weigh_in_passed, racer.weight_grams) == (True, None)
    else:
        expect(form.locator("[data-weight]")).to_have_text("4.9 oz")
        assert racer.weight_grams == Decimal("138.91")

    form.get_by_role("button", name="Take car photo").click()
    take_photo(page, CAR_PHOTO, "Capture and check in")
    crop(page)
    expect(card(page, racer).locator('[data-thumb="car"]')).to_be_visible()
    expect(card(page, racer)).to_have_attribute("data-passed", "1")
    racer.refresh_from_db()
    assert racer.passed_inspection and racer.car_number == int(number)
    assert racer.car_photo.endswith(".jpg")


def test_a_card_weighs_with_pass_and_fail_or_a_weight(login, event):
    page = open_checkin(login("RaceCrew"))
    weigh_partitions(event)
    page.reload()
    scout = Racer.objects.get(event=event, car_number=101)
    outlaw = Racer.objects.get(event=event, car_number=201)

    weigh_group = card(page, scout).get_by_role("group", name="Weigh-in, Scouts1 Racer")
    weigh_group.get_by_role("button", name="Weigh-in Fail").click()
    expect(card(page, scout).locator("[data-weigh-result]")).to_have_text("Failed")
    expect(weigh_group.get_by_role("button", name="Weigh-in Fail")).to_have_attribute(
        "aria-pressed", "true"
    )
    dismiss_toasts(page)

    weight = card(page, outlaw).get_by_label("Weight, Outlaws1 Racer")
    weight.fill("5.4")
    card(page, outlaw).get_by_role("button", name="Save weight").click()
    expect(page.locator("#toasts")).to_contain_text(
        "5.4 oz is over the 5.0 oz maximum for Outlaws."
    )
    expect(card(page, outlaw).locator("[data-over]")).to_have_text(
        re.compile(r"Over the 5\.0 oz max")
    )
    expect(card(page, outlaw).locator("[data-weight]")).to_have_text("5.4 oz")
    scout.refresh_from_db()
    outlaw.refresh_from_db()
    assert scout.weigh_in_passed is False
    assert outlaw.weight_grams == Decimal("153.09")

    page.locator("#f-weigh").select_option(label="Failed weigh-in")
    expect(page.locator("[data-racer]")).to_have_count(2)
    page.locator("#f-weigh").select_option(label="Not weighed")
    expect(page.locator("[data-racer]")).to_have_count(2)


def test_a_partition_with_no_weigh_in_shows_no_controls(login, event):
    page = login("RaceCrew")
    add_racers(event, "Tigers", 2, 101, passed=False)
    open_checkin(page)
    expect(page.locator("[data-racer]")).to_have_count(2)
    expect(page.locator("[data-weigh-in]")).to_have_count(0)
    expect(page.locator("#f-weigh")).to_have_count(0)
    racer = Racer.objects.get(event=event, car_number=101)
    card(page, racer).get_by_role("button", name="Tigers1 Racer", exact=True).click()
    expect(sheet(page).locator("[data-racer-form]")).to_be_visible()
    expect(sheet(page).locator("[data-weigh-in], [data-weigh-fields]")).to_have_count(0)
    sheet(page).locator("[data-racer-form]").get_by_role("button", name="Close").click()
    page.get_by_role("button", name="New racer").click()
    expect(sheet(page).locator('[data-racer-sheet="new"]')).to_be_visible()
    htmx_settled(page)
    expect(sheet(page).locator("[data-weigh-fields]")).to_have_count(0)


def test_weigh_in_screenshots_and_accessibility(login, event):
    """The card and the racer sheet in each weigh-in mode (D75)."""
    page = login("RaceCrew")
    weigh_partitions(event)
    record_weigh_in(Racer.objects.get(event=event, car_number=101), passed=True)
    record_weigh_in(Racer.objects.get(event=event, car_number=201), weight="5.2")
    open_checkin(page)
    expect(page.locator("[data-weigh-in]")).to_have_count(4)
    shoot(page, "checkin-weigh-cards")
    assert_accessible(page)
    for car_number, mode in ((101, "pass-fail"), (201, "recorded"), (301, "none")):
        racer = Racer.objects.get(event=event, car_number=car_number)
        card(page, racer).get_by_role(
            "button", name=f"{racer.first_name} Racer", exact=True
        ).click()
        expect(sheet(page).locator("[data-racer-form]")).to_be_visible()
        htmx_settled(page)
        shoot(page, f"checkin-weigh-sheet-{mode}", full_page=False)
        assert_accessible(page)
        sheet(page).locator("[data-racer-form]").get_by_role(
            "button", name="Close"
        ).click()
        expect(sheet(page)).to_be_hidden()
