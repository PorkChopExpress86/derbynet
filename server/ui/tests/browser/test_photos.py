"""Photo management in a browser (#47): tap to assign and unassign at
390x844, a check-in car photo cropped and turned here, bulk upload with a
failed file and its Retry, delete, the deep link, sort and search, Take
photo, and the screenshots for the PR."""

import io
import re
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import expect

from core.models import RaceSettings
from media.models import PhotoRotation
from roster.models import Racer
from ui.tests.browser.conftest import (
    assert_accessible,
    htmx_post,
    htmx_settled,
    shoot,
)
from ui.tests.browser.test_checkin import CAR_PHOTO, open_checkin, take_photo
from ui.tests.browser.test_checkin import card as checkin_card
from ui.tests.race import add_racers

HEADSHOTS = Path(__file__).resolve().parents[4] / "testing" / "data" / "headshots"
FAILED_UPLOAD = r"Failed to load resource: the server responded with a status of 503"


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


def jpeg(path: Path, color=(30, 120, 200), size=(600, 800)) -> Path:
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


def open_photos(page, repo="racers", query=""):
    page.goto(f"/photos/{repo}/{query}")
    expect(page.locator("#photos-list")).to_be_visible()
    htmx_settled(page)
    return page


def sheet(page):
    return page.locator('[data-sheet="photos"]')


def card(page, racer):
    return page.locator(f'#photos-list [data-racer="{racer.pk}"]')


def pool(page):
    return page.locator("#photos-list [data-pool-photo]")


def toast(page, text):
    return page.locator("#toasts [data-toast]").filter(has_text=text)


def render_size(page, url: str) -> tuple[int, int]:
    """The size of the image the server serves at `url` now."""
    reply = page.request.get(url)
    assert reply.ok, (url, reply.status)
    with Image.open(io.BytesIO(reply.body())) as image:
        return image.size


# ----- tap to assign, unassign with Undo -----------------------------------------


def test_a_phone_assigns_and_unassigns_with_no_drag(login, event, folders):
    """Acceptance: tap a racer, then a photo; Unassign and Undo; tap a
    photo, then a racer found by search. No step drags."""
    first, second = add_racers(event, "Scouts", 2, 101)
    jpeg(folders / "racers" / "blue.jpg")
    jpeg(folders / "racers" / "red.jpg", (200, 30, 30))
    page = login("RaceCrew")
    phone_only(page)
    open_photos(page)
    expect(card(page, first)).to_have_attribute("data-photo-state", "none")
    expect(pool(page)).to_have_count(2)
    shoot(page, "photos-racer-list")

    card(page, first).get_by_role(
        "button", name="Pick a photo for Scouts1 Racer"
    ).click()
    choices = sheet(page).locator("[data-photo-choices]")
    expect(choices).to_be_visible()
    shoot(page, "photos-assign-sheet", full_page=False)
    choices.get_by_role("button", name="Assign blue.jpg").click()
    expect(sheet(page)).to_be_hidden()
    expect(card(page, first)).to_have_attribute("data-photo-state", "assigned")
    expect(card(page, first).locator("[data-thumb]")).to_be_visible()
    expect(pool(page)).to_have_count(1)
    first.refresh_from_db()
    assert first.headshot == "blue.jpg"

    card(page, first).get_by_role(
        "button", name="Unassign the photo of Scouts1 Racer"
    ).click()
    undo = toast(page, "Racer photo taken from Scouts1 Racer.")
    expect(undo).to_be_visible()
    expect(card(page, first)).to_have_attribute("data-photo-state", "none")
    expect(pool(page)).to_have_count(2)
    undo.get_by_role("button", name="Undo").click()
    expect(card(page, first)).to_have_attribute("data-photo-state", "assigned")
    first.refresh_from_db()
    assert first.headshot == "blue.jpg"

    pool(page).first.click()
    racers = sheet(page).locator("[data-racer-choices]")
    expect(racers).to_be_visible()
    htmx_settled(page)
    expect(racers.locator(f'[data-choice="{first.pk}"]')).to_have_count(0)
    racers.get_by_label("Find a racer by name or car number").fill("102")
    expect(racers.locator("[data-choice]")).to_have_count(1)
    shoot(page, "photos-racer-sheet", full_page=False)
    racers.locator(f'[data-choice="{second.pk}"]').click()
    expect(sheet(page)).to_be_hidden()
    expect(card(page, second)).to_have_attribute("data-photo-state", "assigned")
    expect(page.locator("#photos-list [data-pool-empty]")).to_be_visible()
    second.refresh_from_db()
    assert second.headshot == "red.jpg"


# ----- the check-in car photo, cropped and turned here ---------------------------


def test_a_check_in_car_photo_shows_assigned_and_crops_and_turns(login, event, folders):
    """Acceptance: the car photo taken at check-in (#42) is the racer's in
    the Cars repository with no other step, and Turn and Crop change the
    render the server serves."""
    racer = add_racers(event, "Scouts", 1, 101, passed=False)[0]
    page = login("RaceCrew")
    phone_only(page)
    open_checkin(page)
    checkin_card(page, racer).get_by_role(
        "button", name="Car photo of Scouts1 Racer"
    ).click()
    take_photo(page, CAR_PHOTO, "Capture and check in")
    check_in_crop = page.locator('[data-sheet="checkin"] [data-crop-sheet]')
    expect(check_in_crop.get_by_role("button", name="Crop", exact=True)).to_be_enabled()
    page.locator('[data-sheet="checkin"]').get_by_role(
        "button", name="Keep as is"
    ).click()
    expect(checkin_card(page, racer).locator('[data-thumb="car"]')).to_be_visible()
    racer.refresh_from_db()
    assert racer.car_photo == "Car-1637.jpg" and racer.passed_inspection

    open_photos(page, "cars")
    racer_card = card(page, racer)
    expect(racer_card).to_have_attribute("data-photo-state", "assigned")
    thumb = racer_card.locator("[data-thumb]")
    expect(thumb).to_be_visible()
    before_src = thumb.get_attribute("src")
    before = render_size(page, before_src)

    racer_card.get_by_role("button", name="Crop the photo of Scouts1 Racer").click()
    crop_sheet = sheet(page).locator('[data-crop-sheet="car"]')
    expect(crop_sheet).to_be_visible()
    shoot(page, "photos-crop-sheet", full_page=False)
    with htmx_post(page, "/photos/car/rotate/left/"):
        crop_sheet.get_by_role("button", name="Turn left").click()
    expect(crop_sheet.get_by_role("button", name="Crop", exact=True)).to_be_enabled()
    assert PhotoRotation.objects.get(name="Car-1637.jpg").degrees == 90
    turned = render_size(page, f"/photo.php/car/racer/{racer.pk}/200x200/1")
    assert turned == (before[1], before[0]), (before, turned)

    with page.expect_response(lambda r: "/photos/car/crop/save/" in r.url) as reply:
        crop_sheet.get_by_role("button", name="Crop", exact=True).click()
    assert reply.value.ok
    expect(sheet(page)).to_be_hidden()
    racer.refresh_from_db()
    assert racer.car_photo == "0-cropped/Car-1637.jpg"
    # The list shows the new crop at once, though PHP's breaker counts
    # seconds and all of this takes less than one.
    new_thumb = card(page, racer).locator("[data-thumb]")
    expect(new_thumb).not_to_have_attribute("src", before_src)
    expect(new_thumb).to_have_attribute(
        "src", re.compile(r"/photo\.php/car/file/200x200/Car-1637\.jpg/\d+\?v=\d+$")
    )
    # The crop box is 3:4 in the turned, now portrait photo.
    width, height = render_size(page, f"/photo.php/car/racer/{racer.pk}/200x200/2")
    assert abs(width / height - 3 / 4) < 0.05, (width, height)


# ----- bulk upload ----------------------------------------------------------------


@pytest.mark.parametrize("count", [3, 10])
def test_bulk_upload_retries_a_failed_file(
    login, event, folders, tmp_path, page_errors, count
):
    """Acceptance: of `count` files the server fails one request; the others
    upload, the failed one shows Retry, and Retry succeeds. Files go one at a
    time."""
    page_errors.allow(FAILED_UPLOAD)
    files = [
        jpeg(tmp_path / f"photo-{n:02d}.jpg", (20 * n, 100, 200 - 10 * n))
        for n in range(count)
    ]
    failing = files[1].name
    page = login("RaceCrew")
    open_photos(page)
    seen = []
    out = {"now": 0, "most": 0}

    def started(request):
        if "/photos/head/upload/" in request.url:
            out["now"] += 1
            out["most"] = max(out["most"], out["now"])

    def ended(request):
        if "/photos/head/upload/" in request.url:
            out["now"] -= 1

    page.on("request", started)
    page.on("requestfinished", ended)
    page.on("requestfailed", ended)

    def handle(route):
        body = route.request.post_data_buffer or b""
        name = re.search(rb'filename="([^"]+)"', body).group(1).decode()
        seen.append(name)
        if name == failing and seen.count(name) == 1:
            route.fulfill(status=503, content_type="application/json", body="{}")
        else:
            route.continue_()

    page.route("**/photos/head/upload/", handle)
    page.locator("[data-upload-input]").set_input_files([str(f) for f in files])
    items = page.locator("[data-upload-item]")
    expect(items).to_have_count(count)
    expect(page.locator('[data-upload-item][data-state="done"]')).to_have_count(
        count - 1, timeout=20_000
    )
    failed = page.locator('[data-upload-item][data-state="failed"]')
    expect(failed).to_have_count(1)
    expect(failed).to_contain_text(failing)
    expect(page.locator("[data-upload-summary]")).to_have_text(
        f"{count - 1} of {count} uploaded, 1 failed."
    )
    expect(pool(page)).to_have_count(count - 1)
    shoot(page, f"photos-upload-progress-{count}")

    failed.get_by_role("button", name=f"Retry {failing}").click()
    expect(page.locator('[data-upload-item][data-state="done"]')).to_have_count(count)
    expect(pool(page)).to_have_count(count)
    stored = sorted(p.name for p in (folders / "racers").glob("*.jpg"))
    assert stored == sorted(f.name for f in files)
    assert seen.count(failing) == 2
    assert out["most"] == 1, "the uploads overlapped"
    with Image.open(folders / "racers" / files[0].name) as uploaded:
        assert max(uploaded.size) <= 1600


def test_a_big_photo_is_downscaled_before_upload(login, event, folders, tmp_path):
    big = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (10, 200, 10)).save(big, "JPEG")
    page = login("RaceCrew")
    phone_only(page)
    open_photos(page)
    page.locator("[data-upload-input]").set_input_files(str(big))
    expect(page.locator('[data-upload-item][data-state="done"]')).to_have_count(1)
    with Image.open(folders / "racers" / "big.jpg") as uploaded:
        assert uploaded.size == (1600, 1200)


# ----- delete ----------------------------------------------------------------------


def test_delete_removes_the_photo_from_the_pool_and_the_racer(login, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    jpeg(folders / "racers" / "a.jpg")
    jpeg(folders / "racers" / "b.jpg")
    Racer.objects.filter(pk=racer.pk).update(headshot="a.jpg")
    page = login("RaceCrew")
    phone_only(page)
    open_photos(page)
    card(page, racer).get_by_role(
        "button", name="Crop the photo of Scouts1 Racer"
    ).click()
    crop_sheet = sheet(page).locator("[data-crop-sheet]")
    expect(crop_sheet).to_be_visible()
    crop_sheet.get_by_role("button", name="Delete photo…").click()
    confirm = sheet(page).locator('[data-delete-photo="a.jpg"]')
    expect(confirm).to_be_visible()
    confirm.get_by_role("button", name="Cancel").click()
    expect(sheet(page).locator("[data-crop-sheet]")).to_be_visible()
    sheet(page).get_by_role("button", name="Delete photo…").click()
    sheet(page).get_by_role("button", name="Delete 1 photo").click()
    expect(sheet(page)).to_be_hidden()
    expect(toast(page, "a.jpg deleted.")).to_be_visible()
    expect(card(page, racer)).to_have_attribute("data-photo-state", "none")
    expect(pool(page)).to_have_count(1)
    racer.refresh_from_db()
    assert racer.headshot == ""
    assert not (folders / "racers" / "a.jpg").exists()

    pool(page).first.click()
    sheet(page).get_by_role("button", name="Crop, turn or delete").click()
    sheet(page).get_by_role("button", name="Delete photo…").click()
    sheet(page).get_by_role("button", name="Delete 1 photo").click()
    expect(page.locator("#photos-list [data-pool-empty]")).to_be_visible()
    assert not (folders / "racers" / "b.jpg").exists()


# ----- deep link, sort, search, states ------------------------------------------


def test_racerid_scrolls_to_and_highlights_the_racer(login, event, folders):
    racers = add_racers(event, "Scouts", 40, 101)
    target = racers[-1]
    page = open_photos(login("RaceCrew"), query=f"?racerid={target.pk}")
    marked = card(page, target)
    expect(marked).to_have_attribute("data-highlight", "")
    expect(marked).to_be_in_viewport()
    expect(page.locator("#photos-list [data-highlight]")).to_have_count(1)


def test_sort_search_and_the_repository_switch(login, event, folders):
    racers = add_racers(event, "Scouts", 3, 101)
    Racer.objects.filter(pk=racers[0].pk).update(last_name="Zimmer", car_number=1)
    page = open_photos(login("RaceCrew"))

    def ids():
        return [
            int(n)
            for n in page.locator("#photos-list [data-racer]").evaluate_all(
                "cards => cards.map((c) => c.dataset.racer)"
            )
        ]

    assert ids()[-1] == racers[0].pk
    with page.expect_response(lambda r: "/photos/head/list/" in r.url):
        page.locator("#photos-filters").get_by_text("Car", exact=True).click()
    htmx_settled(page)
    assert ids()[0] == racers[0].pk
    cars = page.get_by_role("link", name="Cars")
    expect(cars).to_have_attribute("href", "/photos/cars/?order=car")

    page.get_by_label("Find a racer by name or car number").fill("zimm")
    expect(page.locator("#photos-list [data-racer]")).to_have_count(1)
    cars.click()
    page.wait_for_url("**/photos/cars/?order=car")
    expect(page.get_by_role("link", name="Cars")).to_have_attribute(
        "aria-current", "page"
    )
    expect(page.locator('#photos-filters input[value="car"]')).to_be_checked()


def test_lost_and_no_photo_states(login, event, folders):
    lost, none = add_racers(event, "Scouts", 2, 101)
    Racer.objects.filter(pk=lost.pk).update(headshot="gone.jpg")
    page = open_photos(login("RaceCrew"))
    expect(card(page, lost)).to_have_attribute("data-photo-state", "lost")
    expect(card(page, lost).locator("[data-lost-photo]")).to_have_text(
        "Lost photo: gone.jpg"
    )
    expect(card(page, none).locator("[data-no-photo]")).to_be_visible()
    expect(page.locator('[data-status="empty"]')).to_contain_text("Empty")


def test_a_missing_folder_hides_upload(login, event, folders):
    (folders / "racers").rmdir()
    page = open_photos(login("RaceCoordinator"))
    expect(page.locator('[data-status="missing"]')).to_contain_text("does not exist")
    expect(page.locator("[data-upload]")).to_have_count(0)
    expect(page.locator("[data-not-writable]").get_by_role("link")).to_have_attribute(
        "href", "/settings/"
    )
    shoot(page, "photos-not-writable")


# ----- Take photo ------------------------------------------------------------------


def test_take_photo_from_a_racer_card(login, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    page = login("RaceCrew")
    phone_only(page)
    open_photos(page)
    card(page, racer).get_by_role(
        "button", name="Take a photo of Scouts1 Racer"
    ).click()
    capture = sheet(page).locator('[data-photo-sheet="head"]')
    expect(capture).to_be_visible()
    capture.locator("[data-pick]").set_input_files(str(next(HEADSHOTS.glob("*.jpg"))))
    expect(capture.locator("[data-preview]")).to_be_visible()
    capture.get_by_label("Autocrop to the face").uncheck()
    capture.get_by_role("button", name="Save photo").click()
    crop_sheet = sheet(page).locator('[data-crop-sheet="head"]')
    expect(crop_sheet).to_be_visible()
    expect(crop_sheet.get_by_role("button", name="Crop", exact=True)).to_be_enabled()
    crop_sheet.get_by_role("button", name="Crop", exact=True).click()
    expect(sheet(page)).to_be_hidden()
    expect(card(page, racer)).to_have_attribute("data-photo-state", "assigned")
    racer.refresh_from_db()
    assert racer.headshot.startswith("0-cropped/")


# ----- screenshots and accessibility ------------------------------------------------


def test_screenshots_and_accessibility(login, event, folders):
    racers = add_racers(event, "Scouts", 4, 101)
    add_racers(event, "Siblings", 2, 201)
    for n, path in enumerate(sorted(HEADSHOTS.glob("*.jpg"))[:4]):
        (folders / "racers" / path.name).write_bytes(path.read_bytes())
        if n < 2:
            Racer.objects.filter(pk=racers[n].pk).update(headshot=path.name)
    Racer.objects.filter(pk=racers[2].pk).update(headshot="gone.jpg")
    page = open_photos(login("RaceCrew"))
    shoot(page, "photos-racer-list")
    assert_accessible(page)

    card(page, racers[3]).get_by_role(
        "button", name="Pick a photo for Scouts4 Racer"
    ).click()
    expect(sheet(page).locator("[data-photo-choices]")).to_be_visible()
    shoot(page, "photos-assign-sheet", full_page=False)
    assert_accessible(page)
    sheet(page).get_by_role("button", name="Close").click()

    card(page, racers[0]).get_by_role(
        "button", name="Crop the photo of Scouts1 Racer"
    ).click()
    crop_sheet = sheet(page).locator("[data-crop-sheet]")
    expect(crop_sheet.get_by_role("button", name="Crop", exact=True)).to_be_enabled()
    shoot(page, "photos-crop-sheet", full_page=False)
    assert_accessible(page)
