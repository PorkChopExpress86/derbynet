"""A crop, rotation or delete that lands while a render or a crop is in
progress (#35 review of PR #149), or while `photo.php` serves a photo (#302).
Each change holds the photo's lock until its transaction has committed and
the stale renders are gone, and a render reads the photo's state under the
same lock, so nothing made from the old state outlives the change.

The #35 tests pause a request at the dangerous point with `interrupt()`,
start the change in a second thread, and give it `PAUSE` to finish before
going on: without the lock the change completes inside the pause.

The #302 tests run the change in a second thread at a point where nothing
holds it back, and wait for it to finish (`in_other_thread()`): as the
request lets go of the photo's lock, as the view starts to serve, just
before a file is opened, or while the response streams. The request must
still serve a photo, never a 404."""

import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.db import connection
from django.test import Client

from accounts.permissions import Perm
from media import photos, views
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
JOIN_TIMEOUT = 30


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


def in_other_thread(action) -> None:
    """Run `action` in another thread, as another request would, and wait for
    it: a thread that holds a photo's lock takes it again freely (`_photo_lock`),
    so the same thread would not wait on it. Nothing holds the action back at
    the points the tests call this, so it finishes; a version of the view that
    still held the lock there fails on the timeout instead of hanging."""
    thread = threading.Thread(target=lambda: _closing(action), daemon=True)
    thread.start()
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "the action waited on a lock the view still held"


def _closing(action) -> None:
    try:
        action()
    finally:
        connection.close()


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


# Each request the view serves, after the setup that leads to it, and the
# size of what the photo looked like before the rotation the tests start.
# The upload is 480x320; CROP keeps a quarter of it, 240x160.
SERVED = {
    "a sized render made now": ([], "file/200x200/p.jpg", (200, 133)),
    "a sized render made before": (
        ["file/200x200/p.jpg"],
        "file/200x200/p.jpg",
        (200, 133),
    ),
    "the work render made before": (["file/work/p.jpg"], "file/work/p.jpg", (600, 400)),
    "the crop": (["crop"], "file/cropped/p.jpg", (240, 160)),
    "a turned original made now": (["rotate"], "file/original/p.jpg", (320, 480)),
    "a turned original made before": (
        ["rotate", "file/original/p.jpg"],
        "file/original/p.jpg",
        (320, 480),
    ),
    "a racer's crop": (["crop"], "racer/{racer}", (240, 160)),
    "a racer's turned original": (["rotate"], "racer/{racer}", (320, 480)),
    "a racer's sized render": ([], "racer/{racer}/200x200", (200, 133)),
}


def prepare(crew, event, steps) -> str:
    """Upload p.jpg for a new racer, take `steps`, and return the racer's id."""
    racer = RacerFactory(group__event=event)
    upload(crew, "p.jpg", image_bytes((480, 320)), racerid=str(racer.pk))
    repo = photos.repository(event, "head")
    for step in steps:
        if step == "crop":
            assert crew.post("/action.php", CROP).json()["outcome"]["code"] == "success"
        elif step == "rotate":
            photos.rotate_photo(event, repo, "p.jpg", 90)
        else:
            assert crew.get(f"/photo.php/head/{step}/0").status_code == 200
    return str(racer.pk)


@pytest.mark.parametrize("case", SERVED)
def test_a_rotation_after_the_lookup_still_serves_what_it_found(
    crew, folders, event, monkeypatch, case
):
    """#302: a rotation that runs once the view has its file, and before the
    view reads it, unlinks every render of the photo (`rotate_photo`'s
    `tidy()`). The view still serves what it found, made from the photo as it
    was, where it answered 404 "Can't read file." when it opened the file
    only after letting go of the photo's lock. The rotation runs as the
    request first lets go of the lock, or else as the view starts to serve."""
    steps, url, before = SERVED[case]
    racer = prepare(crew, event, steps)
    repo = photos.repository(event, "head")
    turned = photos.rotation_of(repo, "p.jpg")
    rotate = lambda: photos.rotate_photo(event, repo, "p.jpg", 90)  # noqa: E731
    rotated_when_the_lock_goes(monkeypatch, rotate)
    real = views._serve

    def rotated_first(*args, **kwargs):
        if not rotate.done:
            rotate.done = True
            in_other_thread(rotate)
        return real(*args, **kwargs)

    monkeypatch.setattr(views, "_serve", rotated_first)

    response = crew.get(f"/photo.php/head/{url.format(racer=racer)}/1")

    assert response.status_code == 200, response.content
    assert shape(body(response))[0] == before
    assert photos.rotation_of(repo, "p.jpg") == turned + 90


def rotated_when_the_lock_goes(monkeypatch, rotate) -> None:
    """Run `rotate` in another thread, once, as this thread first lets go of
    the photo's lock it took last: the moment a rotation waiting on the lock
    would take it. `rotate.done` tells whether it has run."""
    rotate.done = False
    real = photos._photo_lock
    me = threading.current_thread()
    depth = [0]

    @contextmanager
    def lock(repo, name):
        if threading.current_thread() is not me:
            with real(repo, name):
                yield
            return
        depth[0] += 1
        try:
            with real(repo, name):
                yield
        finally:
            depth[0] -= 1
        if depth[0] == 0 and not rotate.done:
            rotate.done = True
            in_other_thread(rotate)

    monkeypatch.setattr(photos, "_photo_lock", lock)


# A file already made, which the view finds without the photo's lock, and
# what the request gets once a rotation has unlinked it before the open.
MADE = {
    "a sized render": ("file/200x200/p.jpg", ["file/200x200/p.jpg"], (133, 200)),
    "the work render": ("file/work/p.jpg", ["file/work/p.jpg"], (400, 600)),
    # The rotation drops the crop, so `cropped` serves the turned original.
    "the crop": ("file/cropped/p.jpg", ["crop"], (320, 480)),
    "a turned original": (
        "file/original/p.jpg",
        ["rotate", "file/original/p.jpg"],
        (480, 320),
    ),
}


@pytest.mark.parametrize("case", MADE)
def test_a_made_file_unlinked_before_its_open_is_found_again(
    crew, folders, event, monkeypatch, case
):
    """#302: a render already made is found without the photo's lock. A
    rotation that unlinks it between that lookup and the open leaves the view
    to look again under the lock, and serve the photo as it is now."""
    url, steps, after = MADE[case]
    prepare(crew, event, steps)
    rotated_before_open(monkeypatch, event, folders)

    response = crew.get(f"/photo.php/head/{url}/1")

    assert response.status_code == 200, response.content
    assert shape(body(response))[0] == after


@pytest.mark.parametrize("change", ["rotate", "delete"])
def test_a_racer_whose_crop_goes_before_its_open_gets_what_it_shows_now(
    crew, folders, event, monkeypatch, change
):
    """#302: a racer shown by the crop, whose crop a change unlinks between
    the view's lookup and its open, gets what its column names after the
    change: the turned original after a rotation, the transparent pixel
    after a delete. The view answered 404 there."""
    racer = prepare(crew, event, ["crop"])
    repo = photos.repository(event, "head")
    changed_before_open(
        monkeypatch,
        folders,
        {
            "rotate": lambda: photos.rotate_photo(event, repo, "p.jpg", 90),
            "delete": lambda: photos.delete_photo(event, repo, "p.jpg"),
        }[change],
    )

    response = crew.get(f"/photo.php/head/racer/{racer}/1")

    assert response.status_code == 200, response.content
    if change == "rotate":
        assert shape(body(response))[0] == (320, 480)
    else:
        assert body(response) == ONE_PIXEL.read_bytes()


def rotated_before_open(monkeypatch, event, folders) -> None:
    """Rotate p.jpg by 90 degrees just before the first open of one of its
    renders."""
    repo = photos.repository(event, "head")
    changed_before_open(
        monkeypatch, folders, lambda: photos.rotate_photo(event, repo, "p.jpg", 90)
    )


def changed_before_open(monkeypatch, folders, change) -> None:
    """Run `change` just before the first open of one of p.jpg's renders."""
    real = Path.open
    done = []

    def changed_first(path, mode="r", *args, **kwargs):
        render = path.parent.parent == folders.head and path.name == "p.jpg"
        if render and not done:
            done.append(path)
            in_other_thread(change)
        return real(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", changed_first)


def test_a_rotation_while_the_file_streams_does_not_wait_for_it(
    crew, folders, event, monkeypatch
):
    """#302's risk: the view lets go of the photo's lock before the response
    streams, so a rotation runs while a viewer is still reading, and the
    viewer reads the render it opened."""
    prepare(crew, event, [])
    repo = photos.repository(event, "head")

    response = crew.get("/photo.php/head/file/200x200/p.jpg/1")
    in_other_thread(lambda: photos.rotate_photo(event, repo, "p.jpg", 90))

    assert response.status_code == 200
    assert not (folders.head / "200x200" / "p.jpg").exists()
    assert shape(body(response))[0] == (200, 133)
