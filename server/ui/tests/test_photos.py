"""Photo management's views (#47): the guard and the photo-thumbs.php
redirect, the repository badge with a read-only folder, the racer card
states, sort and search, tap to assign both ways, unassign with Undo, crop,
rotate and delete, bulk upload and Take photo."""

import io
import json
import os
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE, Perm
from core.models import RaceSettings
from media.models import PhotoRotation
from roster.models import Racer
from ui.tests.race import add_racers
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db


@pytest.fixture
def crew(login):
    return login("RaceCrew")


@pytest.fixture
def folders(event, tmp_path):
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(tmp_path / "racers"),
        car_photo_directory=str(tmp_path / "cars"),
    )
    (tmp_path / "racers").mkdir()
    (tmp_path / "cars").mkdir()
    return tmp_path


def jpeg(size=(600, 800)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(stream, "JPEG")
    return stream.getvalue()


def put(folder, name, size=(600, 800)):
    (folder / name).write_bytes(jpeg(size))


def get(client, url, data=None):
    return client.get(url, data or {}, HTTP_HX_REQUEST="true")


def post(client, url, data=None, **extra):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", **extra)


def html(response) -> str:
    assert response.status_code == 200, response.content[:500]
    return response.content.decode()


def card_ids(text: str) -> list[int]:
    return [int(n) for n in re.findall(r'data-racer="(\d+)"', text)]


def states(text: str) -> dict[int, str]:
    return {
        int(pk): state
        for pk, state in re.findall(
            r'data-racer="(\d+)" data-photo-state="(\w+)"', text
        )
    }


def as_permissions(client, bits):
    session = client.session
    session[SESSION_ROLE] = "Tester"
    session[SESSION_PERMISSIONS] = int(bits)
    session.save()
    return client


# ----- the page, the guard, the redirect -----


def test_the_page_needs_assign_racer_image(login, event, client):
    assert login("anonymous").get("/photos/racers/").status_code == 302
    photo_role = login("Photo")
    assert photo_role.get("/photos/cars/").status_code == 302
    assert get(photo_role, "/photos/car/list/").status_code == 403
    assert login("RaceCrew").get("/photos/cars/").status_code == 200
    as_permissions(client, Perm.ASSIGN_RACER_IMAGE)
    assert client.get("/photos/racers/").status_code == 200


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/photo-thumbs.php", "/photos/racers/"),
        ("/photo-thumbs.php?repo=head", "/photos/racers/"),
        ("/photo-thumbs.php?repo=car", "/photos/cars/"),
        (
            "/photo-thumbs.php?repo=car&order=class&racerid=12&back=checkin.php",
            "/photos/cars/?order=class&racerid=12&back=checkin.php",
        ),
        ("/photo-thumbs.php?order=car&repo=head", "/photos/racers/?order=car"),
        ("/derbynet/photo-thumbs.php?repo=car", "/photos/cars/"),
    ],
)
def test_photo_thumbs_redirects_by_repo(client, old, new):
    response = client.get(old)
    assert response.status_code == 301
    assert response["Location"] == new


def test_the_back_button_follows_make_banner(crew, event, folders):
    """photo-thumbs.php passes `$_GET['back']` or index.php to make_banner()."""

    def back(query=""):
        page = html(crew.get(f"/photos/racers/{query}"))
        found = re.search(r'<a href="([^"]*)" aria-label="Back"', page)
        return found.group(1) if found else None

    assert back() == "/"
    assert back("?back=checkin.php") == "/checkin/"
    assert back("?back=coordinator.php") == "/dashboard/"
    assert back("?back=https://example.com/") is None


def test_the_page_and_its_list_write_nothing(crew, event, folders):
    add_racers(event, "Scouts", 2, 101)
    put(folders / "racers", "a.jpg")
    with no_session_writes():
        html(crew.get("/photos/racers/"))
        html(get(crew, "/photos/head/list/"))


def test_an_unknown_repository_is_404(crew, event, folders):
    assert get(crew, "/photos/video/list/").status_code == 404
    assert get(crew, "/photos/video/crop/?photo=a.jpg").status_code == 404


# ----- the badge and the diagnostics -----


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a read-only folder")
def test_a_read_only_folder_shows_its_badge_and_hides_upload(crew, event, folders):
    racers = folders / "racers"
    put(racers, "a.jpg")
    racers.chmod(0o555)
    try:
        page = html(crew.get("/photos/racers/"))
    finally:
        racers.chmod(0o755)
    assert 'data-status="read-only"' in page
    assert "Read-only" in page and f"Directory {racers} is not writable." in page
    assert "data-not-writable" in page
    assert "data-upload" not in page
    assert 'data-action="take-photo"' not in page

    writable = html(crew.get("/photos/racers/"))
    assert 'data-status="ok"' in writable and "data-upload" in writable


def test_each_diagnostic(crew, event, folders):
    def status(url="/photos/racers/"):
        page = html(crew.get(url))
        kind = re.search(r'data-status="([\w-]+)"', page).group(1)
        message = re.search(r"data-diagnostic>([^<]*)<", page)
        return kind, message.group(1) if message else ""

    assert status() == ("empty", "There are no photos in the photo directory yet.")
    (folders / "racers").rmdir()
    assert status() == ("missing", f"Directory {folders / 'racers'} does not exist.")
    (folders / "racers").write_text("")
    assert status()[1] == (
        f"Directory path {folders / 'racers'} exists but is not a directory."
    )
    RaceSettings.objects.filter(event=event).update(
        photo_directory="", car_photo_directory=""
    )
    assert status("/photos/cars/") == ("missing", "No photo directory is set.")


def test_the_settings_link_shows_for_a_role_that_can_open_settings(
    login, event, folders
):
    (folders / "racers").rmdir()
    assert 'href="/settings/"' in html(login("RaceCoordinator").get("/photos/racers/"))
    crew = html(login("RaceCrew").get("/photos/racers/"))
    assert 'href="/settings/"' not in crew
    assert "Ask the race coordinator to check the settings." in crew


# ----- the racer cards, sort, search, deep link -----


def test_assigned_no_photo_and_lost_states(crew, event, folders):
    kept, none, lost, cropped = add_racers(event, "Scouts", 4, 101)
    put(folders / "racers", "kept.jpg")
    put(folders / "racers", "crop.jpg")
    put(folders / "racers", "free.jpg")
    Racer.objects.filter(pk=kept.pk).update(headshot="kept.jpg")
    Racer.objects.filter(pk=lost.pk).update(headshot="gone.jpg")
    Racer.objects.filter(pk=cropped.pk).update(headshot="0-cropped/crop.jpg")
    page = html(crew.get("/photos/racers/"))
    assert states(page) == {
        kept.pk: "assigned",
        none.pk: "none",
        lost.pk: "lost",
        cropped.pk: "assigned",
    }
    assert "Lost photo: gone.jpg" in page
    assert re.findall(r'data-pool-photo="([^"]+)"', page) == ["free.jpg"]
    assert 'loading="lazy"' in page
    assert "/photo.php/head/file/200x200/kept.jpg/" in page
    assert "/photo.php/head/file/200x200/crop.jpg/" in page
    # The car repository reads the car column.
    assert set(states(html(crew.get("/photos/cars/"))).values()) == {"none"}


def test_sort_by_name_class_and_car(crew, event, folders):
    wolves = add_racers(event, "Wolves", 2, 301)
    bears = add_racers(event, "Bears", 2, 201)
    Racer.objects.filter(pk=wolves[0].pk).update(last_name="Aaron")
    Racer.objects.filter(pk=bears[1].pk).update(car_number=100)

    def ids(order):
        return card_ids(html(get(crew, f"/photos/head/list/?order={order}")))

    by_name = sorted(
        Racer.objects.filter(event=event),
        key=lambda r: (r.last_name.lower(), r.first_name.lower()),
    )
    assert ids("name") == [r.pk for r in by_name]
    assert ids("car") == [bears[1].pk, bears[0].pk, wolves[0].pk, wolves[1].pk]
    first_class = min((wolves[0], bears[0]), key=lambda r: r.group.sort_order)
    assert ids("class")[0] in {
        r.pk for r in Racer.objects.filter(group=first_class.group)
    }
    page = html(crew.get("/photos/racers/"))
    labels = re.findall(r'name="order" value="(\w+)"', page)
    assert labels == ["name", "class", "car"]


def test_the_order_is_kept_by_the_repository_links(crew, event, folders):
    reply = html(get(crew, "/photos/head/list/?order=car&racerid=5"))
    assert 'id="photos-repos" aria-label="Photo repository" hx-swap-oob="true"' in reply
    page = html(crew.get("/photos/racers/?order=class&racerid=5&back=checkin.php"))
    assert 'href="/photos/cars/?order=class&amp;racerid=5&amp;back=checkin.php"' in page


def test_search_narrows_the_racers(crew, event, folders):
    racers = add_racers(event, "Scouts", 3, 101)
    Racer.objects.filter(pk=racers[2].pk).update(first_name="Zelda")
    assert card_ids(html(get(crew, "/photos/head/list/?q=102"))) == [racers[1].pk]
    assert card_ids(html(get(crew, "/photos/head/list/?q=zel"))) == [racers[2].pk]


def test_racerid_marks_the_racer(crew, event, folders):
    racers = add_racers(event, "Scouts", 2, 101)
    page = html(crew.get(f"/photos/racers/?racerid={racers[1].pk}"))
    assert re.search(rf'data-racer="{racers[1].pk}"[^>]*data-highlight', page)
    assert page.count("data-highlight>") + page.count("data-highlight ") == 1
    assert "data-highlight>" not in html(crew.get("/photos/racers/?racerid=x"))


# ----- tap to assign, unassign, Undo -----


def test_tap_a_racer_then_a_photo(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    put(folders / "racers", "a.jpg")
    sheet = html(get(crew, f"/photos/head/racer/{racer.pk}/photos/"))
    assert 'data-choice="a.jpg"' in sheet
    reply = post(
        crew,
        "/photos/head/assign/",
        {"racer": racer.pk, "photo": "a.jpg"},
        HTTP_HX_TARGET="photos-sheet-body",
    )
    assert reply.status_code == 200
    assert json.loads(reply["HX-Trigger"]) == {"photos-refresh": True}
    racer.refresh_from_db()
    assert racer.headshot == "a.jpg"
    assert "No unassigned photos" in html(
        get(crew, f"/photos/head/racer/{racer.pk}/photos/")
    )


def test_tap_a_photo_then_a_searched_racer(crew, event, folders):
    first, second = add_racers(event, "Scouts", 2, 101)
    put(folders / "cars", "car.jpg")
    put(folders / "cars", "other.jpg")
    Racer.objects.filter(pk=first.pk).update(car_photo="other.jpg")
    sheet = html(get(crew, "/photos/car/racers/?photo=car.jpg"))
    assert f'data-choice="{second.pk}"' in sheet
    assert f'data-choice="{first.pk}"' not in sheet  # it has a photo
    found = html(get(crew, "/photos/car/racers/list/?photo=car.jpg&q=102"))
    assert card_ids_in_choices(found) == [second.pk]
    assert "No racer without a photo matches." in html(
        get(crew, "/photos/car/racers/list/?photo=car.jpg&q=zzz")
    )
    post(crew, "/photos/car/assign/", {"racer": second.pk, "photo": "car.jpg"})
    second.refresh_from_db()
    assert second.car_photo == "car.jpg"


def card_ids_in_choices(text):
    return [int(n) for n in re.findall(r'data-choice="(\d+)"', text)]


def test_assign_refuses_a_missing_photo_and_another_events_racer(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]

    def assign(racer_id, photo):
        data = {"racer": racer_id, "photo": photo}
        return post(crew, "/photos/head/assign/", data).status_code

    assert assign(racer.pk, "no.jpg") == 422
    put(folders / "racers", "a.jpg")
    assert assign("999999", "a.jpg") == 422
    assert assign(racer.pk, "../a.jpg") == 422


def test_unassign_with_undo_twice_and_after_a_new_photo(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    put(folders / "racers", "a.jpg")
    put(folders / "racers", "b.jpg")
    Racer.objects.filter(pk=racer.pk).update(headshot="a.jpg")
    reply = html(post(crew, f"/photos/head/racer/{racer.pk}/unassign/"))
    racer.refresh_from_db()
    assert racer.headshot == ""
    undo = re.search(r'hx-post="([^"]+)"', reply).group(1).replace("&amp;", "&")
    assert undo == f"/photos/head/assign/?racer={racer.pk}&photo=a.jpg&restore=1"

    assert post(crew, undo).status_code == 200
    racer.refresh_from_db()
    assert racer.headshot == "a.jpg"
    assert post(crew, undo).status_code == 200  # the same Undo changes nothing
    racer.refresh_from_db()
    assert racer.headshot == "a.jpg"

    Racer.objects.filter(pk=racer.pk).update(headshot="b.jpg")
    assert post(crew, undo).status_code == 422
    racer.refresh_from_db()
    assert racer.headshot == "b.jpg"


# ----- crop, rotate, delete -----


def test_crop_rotate_and_delete_a_pool_photo(
    crew, event, folders, django_capture_on_commit_callbacks
):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    put(folders / "racers", "a.jpg")
    sheet = html(get(crew, "/photos/head/crop/?photo=a.jpg"))
    assert 'data-crop-sheet="head"' in sheet
    assert "/photo.php/head/file/work/a.jpg/" in sheet
    assert 'data-action="delete"' in sheet

    turned = post(crew, "/photos/head/rotate/left/?photo=a.jpg")
    assert 'data-crop-sheet="head"' in html(turned)
    assert PhotoRotation.objects.get(name="a.jpg").degrees == 90
    box = {
        "left": "0",
        "top": "0",
        "right": "300",
        "bottom": "400",
        "original_width": "800",
        "original_height": "600",
    }
    assert post(crew, "/photos/head/crop/save/?photo=a.jpg", box).status_code == 200
    assert (folders / "racers" / "0-cropped" / "a.jpg").is_file()
    assert post(crew, "/photos/head/crop/save/?photo=a.jpg", {}).status_code == 422
    assert post(crew, "/photos/head/rotate/sideways/?photo=a.jpg").status_code == 404

    post(crew, "/photos/head/assign/", {"racer": racer.pk, "photo": "a.jpg"})
    racer.refresh_from_db()
    assert racer.headshot == "0-cropped/a.jpg"
    confirm = html(get(crew, "/photos/head/delete/?photo=a.jpg"))
    assert "Delete 1 photo" in confirm
    with django_capture_on_commit_callbacks(execute=True):
        assert post(crew, "/photos/head/delete/?photo=a.jpg").status_code == 200
    racer.refresh_from_db()
    assert racer.headshot == ""
    assert not (folders / "racers" / "a.jpg").exists()
    assert not (folders / "racers" / "0-cropped" / "a.jpg").exists()


def test_photo_changes_need_assign_racer_image(client, event, folders):
    as_permissions(client, Perm.PHOTO_UPLOAD | Perm.CHECK_IN_RACERS)
    put(folders / "racers", "a.jpg")
    for url in (
        "/photos/head/crop/save/?photo=a.jpg",
        "/photos/head/rotate/left/?photo=a.jpg",
        "/photos/head/delete/?photo=a.jpg",
        "/photos/head/assign/",
        "/photos/head/upload/",
    ):
        assert post(client, url).status_code == 403, url
    assert PhotoRotation.objects.count() == 0
    assert (folders / "racers" / "a.jpg").is_file()


# ----- upload and Take photo -----


def test_bulk_upload_one_file_at_a_time(crew, event, folders):
    reply = post(
        crew, "/photos/head/upload/", {"photo": SimpleUploadedFile("one.jpg", jpeg())}
    )
    assert reply.status_code == 200 and reply.json() == {"name": "one.jpg"}
    again = post(
        crew, "/photos/head/upload/", {"photo": SimpleUploadedFile("one.jpg", jpeg())}
    )
    assert again.json() == {"name": "one_1.jpg"}
    bad = post(
        crew, "/photos/head/upload/", {"photo": SimpleUploadedFile("x.jpg", b"text")}
    )
    assert bad.status_code == 422 and "Unacceptable" in bad.json()["message"]
    page = html(crew.get("/photos/racers/"))
    assert re.findall(r'data-pool-photo="([^"]+)"', page) == ["one.jpg", "one_1.jpg"]


def test_bulk_upload_needs_photo_upload(client, event, folders):
    as_permissions(client, Perm.ASSIGN_RACER_IMAGE)
    reply = post(
        client, "/photos/head/upload/", {"photo": SimpleUploadedFile("a.jpg", jpeg())}
    )
    assert reply.status_code == 403
    assert not (folders / "racers" / "a.jpg").exists()
    page = html(client.get("/photos/racers/"))
    assert "data-upload" not in page
    assert get(client, "/photos/head/racer/1/capture/").status_code == 422


def test_take_photo_assigns_it_and_opens_the_crop_sheet(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101, passed=False)[0]
    sheet = html(get(crew, f"/photos/car/racer/{racer.pk}/capture/"))
    assert 'accept="image/*" capture="environment"' in sheet
    assert 'data-action="save-photo"' in sheet
    assert "Capture and check in" not in sheet
    reply = html(
        post(
            crew,
            f"/photos/car/racer/{racer.pk}/capture/upload/",
            {"photo": SimpleUploadedFile("car.jpg", jpeg())},
            HTTP_HX_TARGET="photos-sheet-body",
        )
    )
    racer.refresh_from_db()
    assert racer.car_photo == "car.jpg" and not racer.passed_inspection
    assert 'data-crop-sheet="car"' in reply
    assert "/photo.php/car/file/work/car.jpg/" in reply


# ----- review of 0a5dd304 -----


def test_delete_of_a_bad_or_missing_name_is_refused(crew, event, folders):
    for query in ("", "?photo=", "?photo=../a.jpg", "?photo=gone.jpg"):
        reply = post(crew, f"/photos/head/delete/{query}")
        assert reply.status_code == 422, query
        assert "deleted" not in reply.content.decode()


def test_a_forged_restore_with_no_photo_unassigns_nothing(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    put(folders / "racers", "a.jpg")
    Racer.objects.filter(pk=racer.pk).update(headshot="a.jpg")
    reply = post(crew, f"/photos/head/assign/?racer={racer.pk}&photo=&restore=1")
    assert reply.status_code == 422
    racer.refresh_from_db()
    assert racer.headshot == "a.jpg"


def test_unassign_of_an_unknown_racer_is_refused(crew, event, folders):
    assert post(crew, "/photos/head/racer/999999/unassign/").status_code in (404, 422)


def test_a_hidden_photo_is_in_the_pool(crew, event, folders):
    put(folders / "racers", ".h.jpg")
    page = html(crew.get("/photos/racers/"))
    assert re.findall(r'data-pool-photo="([^"]+)"', page) == [".h.jpg"]
