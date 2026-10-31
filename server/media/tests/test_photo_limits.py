"""Limits on what a request can make the server do (#35 review of PR #149):
pixel counts, crop size, name length, out-of-range integers, and leftovers
of a file that went away."""

import os
import shutil
import struct
import zlib

import pytest
from PIL import Image

from accounts.permissions import Perm
from media.tests.conftest import ONE_PIXEL, image_bytes, log_in, shape, upload
from roster.models import Racer
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning"),
]

CREW = Perm.PHOTO_UPLOAD | Perm.ASSIGN_RACER_IMAGE
CROP = dict(
    action="photo.crop",
    image_name="p.jpg",
    left="0",
    top="0",
    right="10",
    bottom="10",
    original_width="20",
    original_height="20",
)
TOO_BIG = 2**63


def body(response) -> bytes:
    return b"".join(response.streaming_content)


def huge_png(width=13000, height=13000) -> bytes:
    """A small file whose header says it is `width` x `height`, as a
    decompression bomb's is. Decoding it would take gigabytes."""

    def chunk(kind, data):
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\0" * (width * 3 + 1) * 4)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def crew(client, folders):
    log_in(client, CREW)
    return client


def test_an_upload_past_the_pixel_limit_is_refused_before_decoding(crew, folders):
    data = huge_png()
    assert len(data) < 1_000_000

    reply = upload(crew, "big.png", data, autocrop="1")

    assert reply["outcome"]["code"] == "upload_image_size"
    assert "13000x13000" in reply["outcome"]["description"]
    assert list(folders.head.iterdir()) == []


def test_the_pixel_limit_is_a_setting(crew, folders, settings):
    settings.PHOTO_MAX_PIXELS = 480 * 320 - 1

    assert upload(crew, "p.jpg", image_bytes())["outcome"]["code"] == (
        "upload_image_size"
    )
    settings.PHOTO_MAX_PIXELS = 480 * 320
    assert upload(crew, "p.jpg", image_bytes())["outcome"]["code"] == "success"


def test_a_stored_photo_past_the_limit_is_not_decoded(crew, folders):
    """A file put in the folder some other way gets no render and no crop."""
    (folders.head / "big.png").write_bytes(huge_png())

    render = crew.get("/photo.php/head/file/200x200/big.png/1")
    work = crew.get("/photo.php/head/file/work/big.png/1")
    cropped = crew.post("/action.php", dict(CROP, image_name="big.png")).json()

    assert render.status_code == work.status_code == 404
    assert cropped["outcome"]["code"] == "crop"
    assert not (folders.head / "0-cropped").exists()


def test_a_crop_box_is_clipped_to_the_photo(crew, folders):
    upload(crew, "p.jpg", image_bytes((480, 320)))

    reply = crew.post(
        "/action.php", dict(CROP, left="-5", top="-5", right="500", bottom="500")
    ).json()

    assert reply["outcome"]["code"] == "success"
    with Image.open(folders.head / "0-cropped" / "p.jpg") as crop:
        assert crop.size == (480, 320)


def test_a_crop_box_outside_the_photo_is_empty(crew, folders):
    upload(crew, "p.jpg", image_bytes((480, 320)))

    reply = crew.post(
        "/action.php", dict(CROP, left="30", top="30", right="40", bottom="40")
    ).json()

    assert reply["outcome"]["code"] == "crop"
    assert not (folders.head / "0-cropped").exists()


def test_the_longest_name_renders_crops_and_takes_a_suffix(crew, folders, event):
    name = "a" * 241 + ".jpg"
    racer = RacerFactory(group__event=event)

    first = upload(crew, name, image_bytes(), racerid=str(racer.pk))
    second = upload(crew, name, image_bytes(), racerid=str(racer.pk))
    render = crew.get(f"/photo.php/head/file/200x200/{name}/1")
    cropped = crew.post("/action.php", dict(CROP, image_name=name)).json()

    assert first["uploaded"] == name
    assert len(second["uploaded"]) == len(name)
    assert second["uploaded"].endswith("_1.jpg")
    assert render.status_code == 200
    assert cropped["outcome"]["code"] == "success"
    assert Racer.objects.get(pk=racer.pk).headshot == second["uploaded"]


def test_a_new_upload_takes_no_rotation_or_render_from_a_vanished_file(
    crew, folders, django_capture_on_commit_callbacks
):
    upload(crew, "p.jpg", image_bytes((480, 320)))
    with django_capture_on_commit_callbacks(execute=True):
        crew.post(
            "/action.php",
            {"action": "photo.rotate", "image_name": "p.jpg", "rotation": "90"},
        )
    crew.get("/photo.php/head/file/200x200/p.jpg/1")
    os.remove(folders.head / "p.jpg")

    upload(crew, "p.jpg", image_bytes((480, 320), color=(0, 0, 200)))
    original = body(crew.get("/photo.php/head/file/original/p.jpg/2"))
    thumb = body(crew.get("/photo.php/head/file/200x200/p.jpg/2"))

    assert shape(original)[0] == (480, 320)
    assert shape(thumb)[0] == (200, 133)
    with Image.open(folders.head / "200x200" / "p.jpg") as render:
        assert render.convert("RGB").getpixel((5, 5))[2] > 150


@pytest.mark.parametrize(
    "fields",
    [
        {"classids": str(TOO_BIG)},
        {"classids": f"1,{-TOO_BIG - 1}"},
        {"classids": "99999999999999999999"},
    ],
)
def test_a_class_id_past_the_id_column_fails(client, folders, event, fields):
    RacerFactory(
        group=GroupFactory(event=event), passed_inspection=True, headshot="a.jpg"
    )

    reply = client.get(
        "/action.php", {"query": "slide.next", "mode": "racer", **fields}
    ).json()

    assert reply["outcome"]["code"] == "classids"


def test_a_racer_id_past_the_id_column_names_no_racer(client, folders, event):
    RacerFactory(group__event=event, passed_inspection=True, headshot="a.jpg")

    slide = client.get(
        "/action.php",
        {"query": "slide.next", "mode": "racer", "racerid": str(TOO_BIG)},
    ).json()
    photo = client.get(f"/photo.php/head/racer/{TOO_BIG}/1")

    assert slide["photo"]["photo"] == "slide.php/title"
    assert (photo.status_code, body(photo)) == (200, ONE_PIXEL.read_bytes())


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("racerid", str(TOO_BIG), "noracer"),
        ("carnumber", str(2**31), "no-such-car"),
        ("carnumber", "99999999999999999999", "no-such-car"),
        ("barcode", f"PWD{2**31}", "barcode"),
    ],
)
def test_an_upload_for_a_number_past_its_column_fails(
    crew, folders, event, field, value, code
):
    RacerFactory(group__event=event, car_number=101)

    reply = upload(crew, "p.jpg", image_bytes(), **{field: value})

    assert reply["outcome"]["code"] == code


def test_photo_assign_with_a_racer_id_past_the_column_fails(crew, folders):
    upload(crew, "p.jpg", image_bytes())

    reply = crew.post(
        "/action.php",
        {"action": "photo.assign", "racer": str(TOO_BIG), "photo": "p.jpg"},
    ).json()

    assert reply["outcome"]["code"] == "noracer"


def test_a_turn_that_widens_a_photo_past_the_limit_is_not_decoded(
    crew, folders, settings, django_capture_on_commit_callbacks
):
    """A rotation that isn't a right angle widens the canvas: a 1000x1000
    photo turned 45 degrees is 1415x1415, past a 1 MP limit it fits."""
    settings.PHOTO_MAX_PIXELS = 1_000_000
    upload(crew, "p.jpg", image_bytes((1000, 1000)))
    assert crew.get("/photo.php/head/file/200x200/p.jpg/1").status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        crew.post(
            "/action.php",
            {"action": "photo.rotate", "image_name": "p.jpg", "rotation": "45"},
        )

    render = crew.get("/photo.php/head/file/200x200/p.jpg/2")
    original = crew.get("/photo.php/head/file/original/p.jpg/2")
    cropped = crew.post("/action.php", CROP).json()

    assert render.status_code == original.status_code == 404
    assert cropped["outcome"]["code"] == "crop"
    assert not (folders.head / "0-rotated" / "p.jpg").exists()


def test_a_lock_that_cannot_be_opened_is_logged_and_a_render_is_404(
    crew, folders, settings, caplog
):
    upload(crew, "p.jpg", image_bytes())
    # The upload took the lock; now a file stands where the lock folder was.
    shutil.rmtree(settings.DATA_DIR / "locks")
    (settings.DATA_DIR / "locks").write_text("not a folder")

    response = crew.get("/photo.php/head/file/200x200/p.jpg/1")

    assert response.status_code == 404
    assert "can't open the photo lock" in caplog.text
