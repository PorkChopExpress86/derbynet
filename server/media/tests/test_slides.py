"""`query=slide.next`, `slide.php` and `image.php` (#35)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import RaceSettings
from core.services import event_dir
from media.slides import IMAGE_SETS
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db


def body(response) -> bytes:
    return b"".join(response.streaming_content)


def next_slide(client, **fields):
    return client.get("/action.php", {"query": "slide.next", **fields}).json()


def test_classids_with_sql_fail_and_run_none_of_it(client, folders, event):
    """S10: PHP pastes `classids` into `classid IN (...)` for anyone."""
    RacerFactory(group__event=event, passed_inspection=True, headshot="a.jpg")

    with CaptureQueriesContext(connection) as queries:
        reply = client.get(
            "/action.php?query=slide.next&mode=racer&racerid=0&classids=1)%20OR%201=1--"
        ).json()

    assert reply["outcome"]["summary"] == "failure"
    assert reply["outcome"]["code"] == "classids"
    assert "photo" not in reply
    assert not any("OR 1=1" in query["sql"] for query in queries.captured_queries)


@pytest.mark.parametrize("classids", ["", "1,", "1;2", "x", "1 2"])
def test_classids_that_are_not_integers_fail(client, folders, classids):
    assert next_slide(client, classids=classids)["outcome"]["code"] == "classids"


def test_racer_mode_follows_racer_order_and_the_class_filter(client, folders, event):
    cubs, wolves = GroupFactory(event=event), GroupFactory(event=event)
    first = RacerFactory(
        group=cubs, passed_inspection=True, car_photo="c1.jpg", headshot="h1.jpg"
    )
    RacerFactory(group=cubs, passed_inspection=False, car_photo="c2.jpg")
    RacerFactory(group=cubs, passed_inspection=True)
    third = RacerFactory(group=wolves, passed_inspection=True, headshot="h3.jpg")

    reply = next_slide(client, mode="racer", racerid="0")
    only_wolves = next_slide(
        client, mode="racer", racerid="0", classids=f" {wolves.pk} "
    )
    after_last = next_slide(client, mode="racer", racerid=str(third.pk))

    assert reply["photo"]["next"] == {"mode": "racer", "racerid": first.pk}
    assert reply["photo"]["photo"].startswith("photo.php/car/file/cropped/c1.jpg/")
    assert reply["photo"]["inset"].startswith("photo.php/head/file/cropped/h1.jpg/")
    assert only_wolves["photo"]["next"]["racerid"] == third.pk
    assert only_wolves["photo"]["photo"].startswith("photo.php/head/file/cropped/")
    assert "inset" not in only_wolves["photo"]
    assert after_last["photo"] == {
        "photo": "slide.php/title",
        "title": True,
        "next": {"mode": "slide", "file": ""},
    }


def test_slide_mode_walks_the_event_slides(client, folders, event):
    slides = event_dir(event.slug) / "slides"
    (slides / "alt").mkdir(parents=True)
    for name in ("b.png", "a b.png", "title.png", ".hidden.png", "alt/x.png"):
        (slides / name).write_bytes(b"png")

    first = next_slide(client, mode="slide")
    second = next_slide(client, mode="slide", file="a b.png")
    alt = next_slide(client, mode="slide", subdir="alt")
    bad_subdir = next_slide(client, mode="slide", subdir="../..")

    assert first["photo"]["photo"] == "slide.php/a%20b.png"
    assert second["photo"]["next"] == {"mode": "slide", "file": "b.png"}
    assert alt["subdir"] == "alt"
    assert alt["photo"]["photo"] == "slide.php/alt/x.png"
    assert "subdir" not in bad_subdir


def test_slide_php_serves_the_event_slide_then_the_image_sets_title(
    client, folders, event
):
    slides = event_dir(event.slug) / "slides"
    slides.mkdir(parents=True)
    (slides / "a b.png").write_bytes(b"event slide")

    slide = client.get("/slide.php/a%20b.png")
    title = client.get("/derbynet/slide.php/title")

    assert body(slide) == b"event slide"
    assert slide["Cache-Control"] == "max-age=120, public"
    assert body(title) == (IMAGE_SETS / "Generic" / "slides" / "title.png").read_bytes()
    assert title["Content-Type"] == "image/png"


@pytest.mark.parametrize(
    "tail", ["..", "../../etc/passwd", "alt/../../x", "nothing.png", "a/b/c"]
)
def test_slide_php_stays_in_the_slide_folders(client, folders, tail):
    assert client.get(f"/slide.php/{tail}").status_code == 404


def test_image_php_follows_the_event_then_the_image_set(client, folders, event):
    emblem = client.get("/image.php/emblem")
    RaceSettings.objects.filter(event=event).update(images_dir="Cub Scouts")
    scouts = client.get("/image.php/emblem")
    RaceSettings.objects.filter(event=event).update(images_dir="../../..")
    fallback = client.get("/image.php/emblem")
    imagery = event_dir(event.slug) / "imagery"
    imagery.mkdir(parents=True)
    (imagery / "emblem.jpg").write_bytes(b"our own")
    own = client.get("/derbynet/image.php/emblem")

    generic = (IMAGE_SETS / "Generic" / "emblem.png").read_bytes()
    assert body(emblem) == body(fallback) == generic
    assert body(scouts) == (IMAGE_SETS / "Cub Scouts" / "emblem.png").read_bytes()
    assert body(own) == b"our own"
    assert own["Content-Type"] == "image/jpeg"
    assert emblem["Cache-Control"] == "max-age=120, public"


@pytest.mark.parametrize("tail", ["*", "../emblem", "emblem/x", "nothing"])
def test_image_php_serves_only_a_named_image(client, folders, tail):
    assert client.get(f"/image.php/{tail}").status_code == 404
