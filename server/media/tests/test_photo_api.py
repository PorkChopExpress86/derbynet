"""`photo.upload`, `photo.assign` and `photo.crop` on the wire (#35)."""

import io
import random
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from accounts.permissions import Perm
from media.tests.conftest import HEADSHOTS, image_bytes, log_in, upload
from roster.models import Racer
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db

PHOTO_ROLE = Perm.PHOTO_UPLOAD
CREW = Perm.PHOTO_UPLOAD | Perm.ASSIGN_RACER_IMAGE


def post(client, **fields):
    return client.post("/action.php", fields).json()


def test_an_upload_needs_photo_upload(client, folders):
    reply = upload(client, "p.jpg", image_bytes())

    assert reply["outcome"]["code"] == "notauthorized"
    assert list(folders.head.iterdir()) == []


def test_an_upload_keeps_the_bytes_and_reports_phps_keys(client, folders):
    log_in(client, PHOTO_ROLE)
    data = image_bytes()

    reply = upload(client, "My Photo!.jpg", data)

    assert reply["outcome"]["code"] == "success"
    assert reply["uploaded"] == "My Photo_.jpg"
    assert reply["thumbnail"].startswith("photo.php/head/file/200x200/My+Photo_.jpg/")
    assert "filename-change" not in reply
    assert (folders.head / "My Photo_.jpg").read_bytes() == data


def test_a_second_upload_of_a_name_reports_the_new_name(client, folders):
    """C27: PHP echoes an undefined variable, so `filename-change` is null."""
    log_in(client, PHOTO_ROLE)
    upload(client, "p.jpg", image_bytes())

    second = upload(client, "p.jpg", image_bytes(color=(0, 0, 200)))
    third = upload(client, "p.jpg", image_bytes(color=(0, 200, 0)))

    assert (second["filename-change"], second["uploaded"]) == ("p_1.jpg", "p_1.jpg")
    assert third["filename-change"] == "p_2.jpg"
    assert sorted(p.name for p in folders.head.iterdir()) == [
        "p.jpg",
        "p_1.jpg",
        "p_2.jpg",
    ]


@pytest.mark.parametrize(
    ("name", "data"),
    [
        # Not an image, whatever the name and the client's MIME type say.
        ("p.jpg", b"<?php system($_GET['c']); ?>"),
        ("p.png", b"GIF89a but not really"),
        # An image under a name that would be served as something else.
        ("shell.php", image_bytes()),
        ("page.html", image_bytes()),
        ("vector.svg", image_bytes()),
        ("noextension", image_bytes()),
        # A format GD could not read.
        ("p.jpg", image_bytes(fmt="BMP")),
        ("p.jpg", image_bytes(fmt="WEBP")),
    ],
    ids=["php-text", "fake-gif", "php", "html", "svg", "no-extension", "bmp", "webp"],
)
def test_an_upload_is_checked_by_decoding_it(client, folders, name, data):
    log_in(client, PHOTO_ROLE)

    reply = upload(client, name, data)

    assert reply["outcome"]["code"] == "upload_image_type"
    assert list(folders.head.iterdir()) == []


def test_an_upload_honors_max_file_size_and_the_cap(client, folders, settings):
    log_in(client, PHOTO_ROLE)
    data = image_bytes()

    over_form = upload(client, "p.jpg", data, MAX_FILE_SIZE=str(len(data) - 1))
    settings.PHOTO_UPLOAD_MAX_BYTES = len(data) - 1
    over_cap = upload(client, "p.jpg", data)
    nothing = client.post("/action.php", {"action": "photo.upload"}).json()

    assert over_form["outcome"]["code"] == "upload_2"
    assert over_cap["outcome"]["code"] == "upload_1"
    assert nothing["outcome"]["code"] == "upload_4"
    assert list(folders.head.iterdir()) == []


def test_a_large_phone_photo_gets_through(client, folders):
    """Django's limits pass a photo past FILE_UPLOAD_MAX_MEMORY_SIZE, which
    goes to a temporary file. A real phone photo runs 4 to 12 MB."""
    log_in(client, PHOTO_ROLE)
    noise = random.Random(35).randbytes(1800 * 1800 * 3)
    stream = io.BytesIO()
    Image.frombytes("RGB", (1800, 1800), noise).save(stream, "JPEG", quality=100)
    data = stream.getvalue()
    assert len(data) > 4_000_000

    reply = upload(client, "IMG_2573.jpeg", data, MAX_FILE_SIZE="30000000")

    assert reply["outcome"]["code"] == "success"
    assert (folders.head / "IMG_2573.jpeg").read_bytes() == data


def test_an_upload_names_its_racer_by_id_car_number_or_barcode(client, folders, event):
    log_in(client, PHOTO_ROLE)
    by_id, by_car, by_code = (
        RacerFactory(group__event=event, car_number=n) for n in (101, 102, 103)
    )

    first = upload(client, "a.jpg", image_bytes(), repo="car", racerid=str(by_id.pk))
    upload(client, "b.jpg", image_bytes(), repo="car", carnumber="102")
    upload(client, "c.jpg", image_bytes(), repo="car", barcode=f"PWDid{by_code.pk:03d}")

    assert first["photo-url"].startswith("photo.php/car/file/200x200/a.jpg/")
    assert [Racer.objects.get(pk=r.pk).car_photo for r in (by_id, by_car, by_code)] == [
        "a.jpg",
        "b.jpg",
        "c.jpg",
    ]


def test_an_upload_for_no_racer_keeps_the_file_and_fails(client, folders, event):
    log_in(client, PHOTO_ROLE)

    no_car = upload(client, "a.jpg", image_bytes(), carnumber="999")
    no_code = upload(client, "b.jpg", image_bytes(), barcode="PWD999")
    no_id = upload(client, "c.jpg", image_bytes(), racerid="999999")

    assert no_car["outcome"]["code"] == "no-such-car"
    assert no_code["outcome"]["code"] == "barcode"
    assert no_id["outcome"]["code"] == "noracer"
    assert "uploaded" not in no_car
    assert sorted(p.name for p in folders.head.iterdir()) == ["a.jpg", "b.jpg", "c.jpg"]


def test_the_photo_stands_test_and_upload_only_barcodes(client, folders, event):
    log_in(client, PHOTO_ROLE)
    racer = RacerFactory(group__event=event)

    test = upload(client, "t.jpg", image_bytes(), barcode="PWDuploadtest")
    only = upload(client, "o.jpg", image_bytes(), barcode="PWDuploadonly")

    assert test["outcome"]["code"] == only["outcome"]["code"] == "success"
    assert test["uploaded"] == "t.jpg"
    assert [p.name for p in folders.head.iterdir()] == ["o.jpg"]
    racer.refresh_from_db()
    assert racer.headshot == ""


def test_autocrop_crops_to_the_face(client, folders, event):
    log_in(client, PHOTO_ROLE)
    racer = RacerFactory(group__event=event)

    reply = upload(
        client,
        "Cub-0033.jpg",
        (HEADSHOTS / "Cub-0033.jpg").read_bytes(),
        racerid=str(racer.pk),
        autocrop="1",
    )

    assert reply["cropped"] is True
    assert "warning" not in reply
    racer.refresh_from_db()
    assert racer.headshot == "0-cropped/Cub-0033.jpg"
    with Image.open(folders.head / "0-cropped" / "Cub-0033.jpg") as crop:
        width, height = crop.size
    with Image.open(HEADSHOTS / "Cub-0033.jpg") as whole:
        assert width < whole.width and height <= whole.height
    # Widened 10%, then 4/3 as tall, unless the edge of the photo clips it.
    assert height <= round(width * 4 / 3) + 1


def test_autocrop_reports_a_miss_and_leaves_the_photo_uncropped(client, folders, event):
    """#74: PHP's detector reports a miss as a box of zeros. Django reports it
    as a miss, with PHP's warning for one."""
    log_in(client, PHOTO_ROLE)
    racer = RacerFactory(group__event=event)

    reply = upload(
        client,
        "no-photo.jpg",
        (HEADSHOTS / "no-photo.jpg").read_bytes(),
        racerid=str(racer.pk),
        autocrop="1",
    )

    assert reply["outcome"]["code"] == "success"
    assert reply["warning"] == "No face detected for autocrop."
    assert "cropped" not in reply
    assert not (folders.head / "0-cropped").exists()
    racer.refresh_from_db()
    assert racer.headshot == "no-photo.jpg"


def test_the_face_detect_hook_runs_with_no_shell(
    client, folders, settings, tmp_path, monkeypatch
):
    """PHP runs `DERBYNET_FACEDETECT '<path>'` through a shell. A folder name
    with a quote in it then runs a command; Django passes the path as one
    argument, so it runs nothing."""
    mark = tmp_path / "pwned"
    monkeypatch.setenv("PWNED_MARK", str(mark))
    quoted = tmp_path / """x'; touch "$PWNED_MARK"; echo '"""
    quoted.mkdir()
    from core.models import RaceSettings

    RaceSettings.objects.update(photo_directory=str(quoted))
    seen = tmp_path / "argv.txt"
    hook = tmp_path / "detect"
    hook.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$#" "$1" > "{seen}"\n'
        "echo debug\necho 40 40 80 80\n"
    )
    hook.chmod(0o755)
    # The payload works under PHP's call: exec("<hook> '<path>'").
    php_call = f"{hook} '{quoted / 'p.jpg'}'"
    subprocess.run(["sh", "-c", php_call], check=False, capture_output=True)
    assert mark.exists()
    mark.unlink()
    settings.FACE_DETECT_COMMAND = str(hook)
    log_in(client, PHOTO_ROLE)

    reply = upload(client, "p.jpg", image_bytes((400, 400)), autocrop="1")

    count, path = seen.read_text().splitlines()
    assert (count, path) == ("1", str(quoted / "p.jpg"))
    assert not mark.exists()
    assert reply["cropped"] is True
    with Image.open(quoted / "0-cropped" / "p.jpg") as crop:
        # x 40 w 80 widened 10% to 88 from 36; height 4/3 of that, from 21.
        assert crop.size == (88, 117)


def test_photo_assign_gives_the_crop_and_takes_the_photo_away(client, folders, event):
    log_in(client, CREW)
    racer = RacerFactory(group__event=event)
    upload(client, "p.jpg", image_bytes())

    missing = post(client, action="photo.assign", racer=str(racer.pk), photo="x.jpg")
    assigned = post(client, action="photo.assign", racer=str(racer.pk), photo="p.jpg")
    racer.refresh_from_db()
    after_assign = racer.headshot
    post(
        client,
        action="photo.crop",
        image_name="p.jpg",
        left="0",
        top="0",
        right="10",
        bottom="10",
        original_width="20",
        original_height="20",
    )
    other = RacerFactory(group__event=event)
    post(client, action="photo.assign", racer=str(other.pk), photo="p.jpg")
    removed = post(client, action="photo.assign", racer=str(racer.pk), photo="")

    assert missing["outcome"]["code"] == "nosuchfile"
    assert assigned["outcome"]["code"] == removed["outcome"]["code"] == "success"
    assert after_assign == "p.jpg"
    assert Racer.objects.get(pk=other.pk).headshot == "0-cropped/p.jpg"
    assert Racer.objects.get(pk=racer.pk).headshot == ""


@pytest.mark.parametrize("photo", ["../cars/p.jpg", "0-cropped/p.jpg", "/etc/passwd"])
def test_photo_assign_takes_a_file_name_only(client, folders, event, photo):
    log_in(client, CREW)
    racer = RacerFactory(group__event=event)

    reply = post(client, action="photo.assign", racer=str(racer.pk), photo=photo)

    assert reply["outcome"]["code"] == "nosuchfile"


def test_photo_crop_needs_assign_racer_image(client, folders):
    """S19: PHP's photo.crop has no permission check."""
    log_in(client, PHOTO_ROLE)
    upload(client, "p.jpg", image_bytes())
    fields = dict(
        action="photo.crop",
        image_name="p.jpg",
        left="0",
        top="0",
        right="10",
        bottom="10",
        original_width="20",
        original_height="20",
    )

    refused = post(client, **fields)
    log_in(client, CREW)
    allowed = post(client, **fields)

    assert refused["outcome"]["code"] == "notauthorized"
    assert allowed["outcome"]["code"] == "success"


@pytest.mark.parametrize(
    "fields",
    [
        {"image_name": "missing.jpg"},
        {"original_width": "0"},
        {"right": "0"},
        {"left": "abc"},
        {"top": "1e999"},
    ],
)
def test_a_bad_crop_fails(client, folders, fields):
    log_in(client, CREW)
    upload(client, "p.jpg", image_bytes())
    crop = dict(
        action="photo.crop",
        image_name="p.jpg",
        left="0",
        top="0",
        right="10",
        bottom="10",
        original_width="20",
        original_height="20",
    )

    reply = post(client, **(crop | fields))

    assert reply["outcome"]["summary"] == "failure"
    assert not (folders.head / "0-cropped").exists()


@pytest.mark.parametrize("rotation", ["", "abc", "1e999", "-1e999"])
def test_a_rotation_must_be_a_number(client, folders, rotation):
    log_in(client, CREW)
    upload(client, "p.jpg", image_bytes())

    reply = post(client, action="photo.rotate", image_name="p.jpg", rotation=rotation)

    assert reply["outcome"]["code"] == "rotation"


def test_rotate_and_delete_need_assign_racer_image(client, folders):
    log_in(client, PHOTO_ROLE)
    upload(client, "p.jpg", image_bytes())

    rotate = post(client, action="photo.rotate", image_name="p.jpg", rotation="90")
    delete = post(client, action="photo.delete", photo="p.jpg")

    assert rotate["outcome"]["code"] == delete["outcome"]["code"] == "notauthorized"
    assert (folders.head / "p.jpg").exists()


def test_photo_delete_succeeds_when_a_file_cannot_be_removed(
    client, folders, monkeypatch, django_capture_on_commit_callbacks
):
    """C17: PHP's error path calls json_failure() with one argument, a fatal
    error on PHP 8. Django skips a file it can't remove and removes the rest.
    A folder that happens to have the photo's name is searched, not removed."""
    log_in(client, CREW)
    upload(client, "p.jpg", image_bytes())
    client.get("/photo.php/head/file/200x200/p.jpg/1")
    client.get("/photo.php/head/file/work/p.jpg/1")
    stuck = folders.head / "200x200" / "p.jpg"
    odd = folders.head / "other" / "p.jpg"
    odd.mkdir(parents=True)
    (odd / "p.jpg").write_bytes(b"inside")
    refused, real_unlink = [], Path.unlink

    def unlink(self, *args, **kwargs):
        if self == stuck:
            refused.append(self)
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with django_capture_on_commit_callbacks(execute=True):
        reply = post(client, action="photo.delete", photo="p.jpg")

    assert reply["outcome"]["code"] == "success"
    assert refused == [stuck]
    assert stuck.is_file()
    assert not (folders.head / "p.jpg").exists()
    assert not (folders.head / "0-work" / "p.jpg").exists()
    assert odd.is_dir()
    assert not (odd / "p.jpg").exists()


def test_an_unknown_repository_fails(client, folders):
    log_in(client, CREW)

    reply = post(client, action="photo.assign", repo="secrets", racer="1", photo="p")

    assert reply["outcome"]["code"] == "unknown-repo"
