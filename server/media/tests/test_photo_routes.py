"""`photo.php` (#35): the URL forms, #8's byte and orientation rules, the
render allowlist, the per-file lock and the no-photo pixel."""

import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection
from django.test import Client
from PIL import Image

from accounts.permissions import Perm
from media import photos
from media.photos import ALLOWED_BOXES, scaled_size
from media.tests.conftest import (
    ONE_PIXEL,
    PATTERN_O6,
    image_bytes,
    log_in,
    quarters,
    shape,
    upload,
)
from roster.models import Racer
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db

KNOWN_FOLDERS = {box.folder for box in ALLOWED_BOXES} | {
    "0-cropped",
    "0-work",
    "0-rotated",
}


def body(response) -> bytes:
    return b"".join(response.streaming_content)


def get(client, tail):
    response = client.get(f"/photo.php/{tail}")
    return response, (body(response) if response.status_code == 200 else b"")


@pytest.fixture
def crew(client, folders):
    log_in(client, Perm.PHOTO_UPLOAD | Perm.ASSIGN_RACER_IMAGE)
    return client


@pytest.fixture
def act(crew, django_capture_on_commit_callbacks):
    """Post an action and run what it left for after the commit, as a real
    request does."""

    def post(**fields):
        with django_capture_on_commit_callbacks(execute=True):
            return crew.post("/action.php", fields).json()

    return post


def test_php_truncates_render_sizes():
    """A 921x299 photo is 200x64 at 200x200, as #8 measured PHP."""
    assert scaled_size(921, 299, 200, 200) == (200, 64)
    assert scaled_size(921, 299, 240, 120) == (240, 77)
    assert scaled_size(480, 320, 200, 200) == (200, 133)
    assert scaled_size(320, 480, 0, 60) == (40, 60)


def test_a_render_has_phps_truncated_size(crew, folders):
    upload(crew, "wide.jpg", image_bytes((921, 299)))

    response, data = get(crew, "head/file/200x200/wide.jpg/1")

    assert response["Content-Type"] == "image/jpeg"
    assert shape(data) == ((200, 64), None)


def test_an_orientation_6_photo_renders_upright_and_its_original_is_unchanged(
    crew, folders
):
    """#8's rule, and the fix for #73: the original route serves the upload
    byte for byte, tag and all; renders come out upright, with no tag."""
    stored = PATTERN_O6.read_bytes()
    assert upload(crew, "pattern-o6.jpg", stored, repo="car")["outcome"]["code"] == (
        "success"
    )

    _, original = get(crew, "car/file/original/pattern-o6.jpg/1")
    _, cropped = get(crew, "car/file/cropped/pattern-o6.jpg/1")
    _, thumb = get(crew, "car/file/200x200/pattern-o6.jpg/1")
    _, work = get(crew, "car/file/work/pattern-o6.jpg/1")

    assert hashlib.md5(original).digest() == hashlib.md5(stored).digest()
    assert cropped == stored
    assert shape(original) == ((320, 480), 6)
    assert quarters(original) == "RGBW"
    assert shape(thumb) == ((200, 133), None)
    assert quarters(thumb) == "RGBW"
    assert shape(work) == ((600, 400), None)
    assert quarters(work) == "RGBW"


def test_a_racers_photo_is_the_upload_until_it_is_cropped(crew, folders, event):
    racer = RacerFactory(group__event=event)
    stored = PATTERN_O6.read_bytes()
    upload(crew, "o6.jpg", stored, racerid=str(racer.pk))

    _, plain = get(crew, f"head/racer/{racer.pk}/1")
    _, padded = get(crew, f"head/racer/{racer.pk:03d}/1")
    _, sized = get(crew, f"head/racer/{racer.pk}/200x200/1")

    assert plain == padded == stored
    assert shape(sized) == ((200, 133), None)


def test_a_racer_with_no_photo_gets_the_transparent_pixel(crew, folders, event):
    """Check-in and kiosk <img> tags rely on a 200 with the 1x1 PNG."""
    racer = RacerFactory(group__event=event)

    for tail in (f"head/racer/{racer.pk}/1", f"car/racer/{racer.pk}/200x200/1"):
        response, data = get(crew, tail)
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert data == ONE_PIXEL.read_bytes()


def test_a_racer_whose_file_is_missing_gets_the_transparent_pixel(crew, folders, event):
    racer = RacerFactory(group__event=event, headshot="gone.jpg")

    for tail in (f"head/racer/{racer.pk}/1", f"head/racer/{racer.pk}/work/1"):
        response, data = get(crew, tail)
        assert response.status_code == 200
        assert data == ONE_PIXEL.read_bytes()


def test_an_unknown_racer_gets_the_transparent_pixel(crew, folders):
    for tail in ("head/racer/999999/1", "head/racer/abc/1"):
        response, data = get(crew, tail)
        assert (response.status_code, data) == (200, ONE_PIXEL.read_bytes())


def test_photos_carry_phps_cache_headers_at_the_root_and_under_the_prefix(
    crew, folders
):
    upload(crew, "p.jpg", image_bytes())

    for path in (
        "/photo.php/head/file/original/p.jpg/1",
        "/derbynet/photo.php/head/file/original/p.jpg/1",
    ):
        response = crew.get(path)
        assert response.status_code == 200
        assert response["Cache-Control"] == "max-age=86400, public"
        assert response["Pragma"] == "public"
        assert "Expires" in response


def test_the_info_form_is_gone(client, folders):
    """S8: PHP serves the file named by any setting, or by the URL."""
    for tail in (
        "info/photo-directory/1/etc/passwd",
        "info/nonexistent/1/../../../etc/passwd",
        "info/x/1/derbynet.sqlite3",
    ):
        response = client.get(f"/photo.php/{tail}")
        assert response.status_code == 404
        assert b"root:" not in response.content


@pytest.mark.parametrize(
    "tail",
    [
        "nope/file/original/p.jpg/1",
        "head/file/bogus/p.jpg/1",
        "head/file/original/missing.jpg/1",
        "head/file/original/..%2Fcars%2Fp.jpg/1",
        "head/file/original/../p.jpg/1",
        "head",
        "head/other/1/2",
    ],
)
def test_bad_photo_urls_answer_404(crew, folders, tail):
    upload(crew, "p.jpg", image_bytes(), repo="car")

    assert crew.get(f"/photo.php/{tail}").status_code == 404


def test_fifty_sizes_make_no_folder_outside_the_allowlist(crew, folders):
    """A client can no longer create a render folder per size it asks for."""
    upload(crew, "p.jpg", image_bytes((900, 600)))
    labels = {f"{w}x{h}" for w in range(17, 1400, 97) for h in range(13, 900, 211)}
    labels = sorted(labels)[:50]
    assert len(labels) == 50

    for label in labels:
        response, data = get(crew, f"head/file/{label}/p.jpg/1")
        assert response.status_code == 200
        w, h = (float(n) for n in label.split("x"))
        (width, height), _ = shape(data)
        wanted = scaled_size(900, 600, w, h)
        smallest = min(scaled_size(900, 600, b.width, b.height) for b in ALLOWED_BOXES)
        # Never bigger than asked for, unless even the smallest render is.
        assert (width <= wanted[0] and height <= wanted[1]) or (width, height) == (
            smallest
        )
    for label in ("40h", "75h", "0x60", "100.5x100"):
        assert get(crew, f"head/file/{label}/p.jpg/1")[0].status_code == 200

    made = {p.name for p in folders.head.iterdir() if p.is_dir()}
    assert made <= KNOWN_FOLDERS
    assert len(made) < 30


def test_a_size_on_the_allowlist_is_used_as_asked(crew, folders):
    upload(crew, "p.jpg", image_bytes((900, 600)))

    _, data = get(crew, "head/file/400x400/p.jpg/1")

    assert shape(data)[0] == (400, 266)
    assert (folders.head / "400x400" / "p.jpg").is_file()


@pytest.mark.django_db(transaction=True)
def test_ten_first_requests_make_one_render(folders, monkeypatch):
    """Concurrent first requests for one render wait on its lock: one file is
    written once, and every response has its bytes."""
    client = Client()
    log_in(client, Perm.PHOTO_UPLOAD)
    upload(client, "p.jpg", image_bytes((1200, 800)))
    encoded = []
    real_encode = photos._encode

    def counting_encode(*args):
        encoded.append(threading.get_ident())
        real_encode(*args)

    monkeypatch.setattr(photos, "_encode", counting_encode)
    barrier = threading.Barrier(10)

    def fetch(_):
        try:
            barrier.wait()
            return body(Client().get("/photo.php/head/file/200x200/p.jpg/1"))
        finally:
            connection.close()

    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(fetch, range(10)))

    assert len(encoded) == 1
    assert len(set(results)) == 1 and results[0]
    assert [p.name for p in (folders.head / "200x200").iterdir()] == ["p.jpg"]


def test_a_rotation_is_stored_and_the_upload_kept(crew, act, folders):
    """#8's rule 4: `photo.rotate` turns the renders and the served original,
    and leaves the upload alone."""
    stored = PATTERN_O6.read_bytes()
    upload(crew, "o6.jpg", stored)
    get(crew, "head/file/200x200/o6.jpg/1")

    turned = act(
        **{
            "action": "photo.rotate",
            "repo": "head",
            "image_name": "o6.jpg",
            "rotation": "90",
        }
    )

    assert turned["outcome"]["code"] == "success"
    assert (folders.head / "o6.jpg").read_bytes() == stored
    _, original = get(crew, "head/file/original/o6.jpg/2")
    _, thumb = get(crew, "head/file/200x200/o6.jpg/2")
    # Counterclockwise, as GD's imagerotate(): RGBW becomes GWRB.
    assert shape(original) == ((320, 480), None)
    assert quarters(original) == "GWRB"
    assert shape(thumb) == ((133, 200), None)
    assert quarters(thumb) == "GWRB"

    act(
        **{
            "action": "photo.rotate",
            "repo": "head",
            "image_name": "o6.jpg",
            "rotation": "-90",
        }
    )

    assert get(crew, "head/file/original/o6.jpg/3")[1] == stored
    assert quarters(get(crew, "head/file/200x200/o6.jpg/3")[1]) == "RGBW"


def test_a_crop_box_is_in_the_upright_frame(crew, act, folders, event):
    """#8's rule 3: the box refers to the upright work render, here its left
    half, which holds the red and blue quarters."""
    racer = RacerFactory(group__event=event)
    upload(crew, "o6.jpg", PATTERN_O6.read_bytes(), racerid=str(racer.pk))

    cropped = act(
        **{
            "action": "photo.crop",
            "repo": "head",
            "image_name": "o6.jpg",
            "left": "0",
            "top": "0",
            "right": "300",
            "bottom": "400",
            "original_width": "600",
            "original_height": "400",
        }
    )

    assert cropped["outcome"]["code"] == "success"
    assert "cache-breaker" in cropped
    _, data = get(crew, "head/file/cropped/o6.jpg/1")
    assert shape(data) == ((240, 320), None)
    assert quarters(data) == "RRBB"
    racer.refresh_from_db()
    assert racer.headshot == "0-cropped/o6.jpg"
    assert get(crew, f"head/racer/{racer.pk}/1")[1] == data


def test_a_rotation_drops_the_crop(crew, act, folders, event):
    racer = RacerFactory(group__event=event)
    upload(crew, "p.jpg", image_bytes(), racerid=str(racer.pk))
    act(
        **{
            "action": "photo.crop",
            "repo": "head",
            "image_name": "p.jpg",
            "left": "0",
            "top": "0",
            "right": "10",
            "bottom": "10",
            "original_width": "20",
            "original_height": "20",
        }
    )
    racer.refresh_from_db()
    assert racer.headshot == "0-cropped/p.jpg"

    act(
        **{
            "action": "photo.rotate",
            "repo": "head",
            "image_name": "p.jpg",
            "rotation": "90",
        }
    )

    racer.refresh_from_db()
    assert racer.headshot == "p.jpg"
    assert not (folders.head / "0-cropped" / "p.jpg").exists()


def test_png_renders_are_compressed(crew, folders):
    """#8's rule 5: PHP writes level 0, which makes a 200x200 PNG of a flat
    480x320 image about 100 KB."""
    upload(crew, "flat.png", image_bytes(fmt="PNG"))

    response, data = get(crew, "head/file/200x200/flat.png/1")

    assert response["Content-Type"] == "image/png"
    assert shape(data) == ((200, 133), None)
    assert len(data) < 10_000


def test_a_gif_renders_as_a_gif(crew, folders):
    upload(crew, "g.gif", image_bytes(fmt="GIF"))

    response, data = get(crew, "head/file/200x200/g.gif/1")

    assert response["Content-Type"] == "image/gif"
    with Image.open(io.BytesIO(data)) as image:
        assert (image.format, image.size) == ("GIF", (200, 133))


def test_a_deleted_photo_leaves_no_render_and_no_racer(crew, act, folders, event):
    racer = RacerFactory(group__event=event)
    upload(crew, "p.jpg", image_bytes(), racerid=str(racer.pk), repo="car")
    get(crew, "car/file/200x200/p.jpg/1")
    get(crew, "car/file/work/p.jpg/1")

    deleted = act(**{"action": "photo.delete", "repo": "car", "photo": "p.jpg"})

    assert deleted["outcome"]["code"] == "success"
    assert list(folders.car.rglob("p.jpg")) == []
    assert Racer.objects.get(pk=racer.pk).car_photo == ""
    assert crew.get("/photo.php/car/file/original/p.jpg/1").status_code == 404
