"""A crop, rotation or delete that lands while a render or a crop is in
progress (#35 review of PR #149). Each change holds the photo's lock until
its transaction has committed and the stale renders are gone, and a render
reads the photo's state under the same lock, so nothing made from the old
state outlives the change.

Each test pauses one request at the dangerous point, starts the other
action in a second thread, and gives it time to finish before going on.
Without the lock the second action completes inside the pause."""

import threading
import time

import pytest
from django.db import connection
from django.test import Client

from accounts.permissions import Perm
from media import photos
from media.tests.conftest import (
    HEADSHOTS,
    ONE_PIXEL,
    image_bytes,
    log_in,
    shape,
    upload,
)
from roster.models import Racer
from roster.tests.factories import RacerFactory

# serialized_rollback restores the seeded rows after the flush.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

CREW = Perm.PHOTO_UPLOAD | Perm.ASSIGN_RACER_IMAGE
PAUSE = 0.5


def body(response) -> bytes:
    return b"".join(response.streaming_content)


@pytest.fixture
def crew(folders):
    client = Client()
    log_in(client, CREW)
    return client


def interrupt(monkeypatch, attribute, when, action, after=False):
    """Make `photos.<attribute>` start `action` in another thread the first
    time `when(*args)` holds, before the call or, with `after`, once it has
    returned, and pause long enough for the action to finish if nothing holds
    it back. Returns a list that then holds the thread, to join."""
    real = getattr(photos, attribute)
    started = []

    def run():
        try:
            action()
        finally:
            connection.close()

    def start(args):
        if not started and when(*args):
            thread = threading.Thread(target=run)
            started.append(thread)
            thread.start()
            time.sleep(PAUSE)

    def interrupted(*args, **kwargs):
        if not after:
            start(args)
        result = real(*args, **kwargs)
        if after:
            start(args)
        return result

    monkeypatch.setattr(photos, attribute, interrupted)
    return started


def test_a_rotation_during_a_render_leaves_no_stale_render(
    crew, folders, event, monkeypatch
):
    upload(crew, "p.jpg", image_bytes((480, 320)))
    repo = photos.repository(event, "head")
    started = interrupt(
        monkeypatch,
        "_open_upright",
        lambda path, rotation=0.0: True,
        lambda: photos.rotate_photo(event, repo, "p.jpg", 90),
    )

    first = body(crew.get("/photo.php/head/file/200x200/p.jpg/1"))
    started[0].join()
    later = body(crew.get("/photo.php/head/file/200x200/p.jpg/2"))

    assert shape(first)[0] == (200, 133)
    assert shape(later)[0] == (133, 200)


def test_a_delete_during_a_render_leaves_no_orphan(crew, folders, event, monkeypatch):
    upload(crew, "p.jpg", image_bytes((480, 320)))
    repo = photos.repository(event, "head")
    # After the photo is decoded, so the render no longer needs the file.
    started = interrupt(
        monkeypatch,
        "_encode",
        lambda image, fmt, target: True,
        lambda: photos.delete_photo(event, repo, "p.jpg"),
    )

    crew.get("/photo.php/head/file/200x200/p.jpg/1")
    started[0].join()

    assert list(folders.head.rglob("p.jpg")) == []


def test_a_rotation_during_a_crop_leaves_the_racer_a_photo(
    crew, folders, event, monkeypatch
):
    racer = RacerFactory(group__event=event)
    upload(crew, "p.jpg", image_bytes((480, 320)), racerid=str(racer.pk))
    repo = photos.repository(event, "head")
    started = interrupt(
        monkeypatch,
        "_write_atomically",
        lambda target, write: target.parent.name == "0-cropped",
        lambda: photos.rotate_photo(event, repo, "p.jpg", 90),
    )

    cropped = crew.post("/action.php", CROP).json()
    started[0].join()

    assert cropped["outcome"]["code"] == "success"
    # The rotation came second: it dropped the crop and the racer with it.
    headshot = Racer.objects.get(pk=racer.pk).headshot
    assert headshot == "p.jpg"
    assert (folders.head / headshot).is_file()
    served = body(crew.get(f"/photo.php/head/racer/{racer.pk}/1"))
    assert served != ONE_PIXEL.read_bytes()


CROP = {
    "action": "photo.crop",
    "image_name": "p.jpg",
    "left": "0",
    "top": "0",
    "right": "10",
    "bottom": "10",
    "original_width": "20",
    "original_height": "20",
}


@pytest.mark.parametrize("change", ["rotate", "delete"])
def test_a_change_during_photo_assign_leaves_the_racer_a_real_photo(
    crew, folders, event, monkeypatch, change
):
    """photo.assign reads whether the photo has a crop, then writes the
    racer. A rotation in between takes the crop away; a delete, the photo."""
    racer = RacerFactory(group__event=event)
    upload(crew, "p.jpg", image_bytes((480, 320)))
    crew.post("/action.php", CROP)
    repo = photos.repository(event, "head")
    action = {
        "rotate": lambda: photos.rotate_photo(event, repo, "p.jpg", 90),
        "delete": lambda: photos.delete_photo(event, repo, "p.jpg"),
    }[change]
    started = interrupt(
        monkeypatch, "_stored_path", lambda repo, name: True, action, after=True
    )

    assigned = crew.post(
        "/action.php",
        {"action": "photo.assign", "racer": str(racer.pk), "photo": "p.jpg"},
    ).json()
    started[0].join()

    assert assigned["outcome"]["code"] == "success"
    headshot = Racer.objects.get(pk=racer.pk).headshot
    # The change came second, so it decides what the racer is left with.
    assert headshot == ("p.jpg" if change == "rotate" else "")
    if headshot:
        assert (folders.head / headshot).is_file()


def test_a_rotation_during_an_autocropped_upload_leaves_the_racer_a_photo(
    crew, folders, event, monkeypatch
):
    """photo.upload crops to the face, then assigns the crop. A rotation in
    between takes the crop away."""
    racer = RacerFactory(group__event=event)
    repo = photos.repository(event, "head")
    started = interrupt(
        monkeypatch,
        "_assign_upload",
        lambda *args: True,
        lambda: photos.rotate_photo(event, repo, "Cub-0033.jpg", 90),
    )

    reply = upload(
        crew,
        "Cub-0033.jpg",
        (HEADSHOTS / "Cub-0033.jpg").read_bytes(),
        racerid=str(racer.pk),
        autocrop="1",
    )
    started[0].join()

    assert reply["cropped"] is True
    headshot = Racer.objects.get(pk=racer.pk).headshot
    assert headshot == "Cub-0033.jpg"
    assert (folders.head / headshot).is_file()
