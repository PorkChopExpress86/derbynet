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
from media.tests.conftest import ONE_PIXEL, image_bytes, log_in, shape, upload
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


def interrupt(monkeypatch, attribute, when, action):
    """Make `photos.<attribute>` start `action` in another thread the first
    time `when(*args)` holds, and pause long enough for it to finish if
    nothing holds it back. Returns the thread, to join."""
    real = getattr(photos, attribute)
    started = []

    def run():
        try:
            action()
        finally:
            connection.close()

    def interrupted(*args, **kwargs):
        if not started and when(*args):
            thread = threading.Thread(target=run)
            started.append(thread)
            thread.start()
            time.sleep(PAUSE)
        return real(*args, **kwargs)

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

    cropped = crew.post(
        "/action.php",
        {
            "action": "photo.crop",
            "image_name": "p.jpg",
            "left": "0",
            "top": "0",
            "right": "10",
            "bottom": "10",
            "original_width": "20",
            "original_height": "20",
        },
    ).json()
    started[0].join()

    assert cropped["outcome"]["code"] == "success"
    # The rotation came second: it dropped the crop and the racer with it.
    headshot = Racer.objects.get(pk=racer.pk).headshot
    assert headshot == "p.jpg"
    assert (folders.head / headshot).is_file()
    served = body(crew.get(f"/photo.php/head/racer/{racer.pk}/1"))
    assert served != ONE_PIXEL.read_bytes()
