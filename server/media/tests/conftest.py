import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE
from core.models import RaceSettings
from core.tests.factories import EventFactory

TESTING = Path(__file__).resolve().parents[3] / "testing"
HEADSHOTS = TESTING / "data" / "headshots"
PATTERN_O6 = TESTING / "data" / "carphotos" / "pattern-o6.jpg"
ONE_PIXEL = Path(__file__).resolve().parents[3] / "website" / "img" / "1x1.png"


@pytest.fixture
def event(db):
    return EventFactory()


@pytest.fixture
def folders(settings, tmp_path, event):
    """The event's photo and video folders, and the data and config folders,
    all under `tmp_path`."""
    settings.DATA_DIR = tmp_path / "data"
    settings.CONFIG_DIR = tmp_path / "config"
    found = SimpleNamespace(
        head=tmp_path / "racers", car=tmp_path / "cars", video=tmp_path / "videos"
    )
    for folder in (settings.DATA_DIR, settings.CONFIG_DIR, *vars(found).values()):
        folder.mkdir()
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(found.head),
        car_photo_directory=str(found.car),
        video_directory=str(found.video),
    )
    return found


def log_in(client, permissions, role="Tester"):
    session = client.session
    session[SESSION_ROLE] = role
    session[SESSION_PERMISSIONS] = int(permissions)
    session.save()


def image_bytes(size=(480, 320), fmt="JPEG", color=(200, 30, 30), **save) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, fmt, **save)
    return stream.getvalue()


def upload(client, name, data, **fields):
    fields.setdefault("repo", "head")
    return client.post(
        "/action.php",
        {"action": "photo.upload", "photo": SimpleUploadedFile(name, data), **fields},
    ).json()


def quarters(data: bytes) -> str:
    """The color of each quarter, after the orientation tag, as
    `testing/test-photo-manipulations.sh` reads it: upright, the pattern
    reads RGBW."""
    with Image.open(io.BytesIO(data)) as image:
        from PIL import ImageOps

        small = ImageOps.exif_transpose(image).convert("RGB").resize((2, 2))
    letters = ""
    for r, g, b in (small.getpixel(xy) for xy in ((0, 0), (1, 0), (0, 1), (1, 1))):
        if r > 150 and g > 150 and b > 150:
            letters += "W"
        elif r > g and r > b:
            letters += "R"
        elif g > b:
            letters += "G"
        else:
            letters += "B"
    return letters


def shape(data: bytes) -> tuple[tuple[int, int], int | None]:
    """The stored size and the EXIF orientation tag, or None."""
    with Image.open(io.BytesIO(data)) as image:
        return image.size, image.getexif().get(0x0112)
