"""Removing the images that fake racers get (#63, C28)."""

import os

import pytest

from core.fake_images import remove_fake_images
from core.models import RaceSettings
from core.services import reset_event
from core.tests.factories import EventFactory

pytestmark = pytest.mark.django_db


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<svg/>")
    return path


def test_only_files_named_fake_digits_svg_are_removed(tmp_path):
    fake = touch(tmp_path / "fake_12.svg")
    bare = touch(tmp_path / "fake_.svg")
    real = [
        touch(tmp_path / "fake_12.png"),
        touch(tmp_path / "xfake_12.svg"),
        touch(tmp_path / "fake_1a.svg"),
        touch(tmp_path / "head.jpg"),
    ]

    remove_fake_images(tmp_path)

    assert not fake.exists() and not bare.exists()
    assert all(path.exists() for path in real)


def test_subdirectories_are_searched(tmp_path):
    nested = touch(tmp_path / "a" / "b" / "fake_3.svg")
    kept = touch(tmp_path / "a" / "keep.svg")

    remove_fake_images(tmp_path)

    assert not nested.exists()
    assert kept.exists()


def test_a_linked_directory_is_not_followed(tmp_path):
    outside = touch(tmp_path / "outside" / "fake_1.svg")
    root = tmp_path / "photos"
    root.mkdir()
    os.symlink(outside.parent, root / "link")

    remove_fake_images(root)

    assert outside.exists()


def test_a_missing_directory_is_ignored(tmp_path):
    remove_fake_images(tmp_path / "nothing")
    remove_fake_images(None)
    remove_fake_images("")


def test_a_reset_removes_fake_images_from_both_photo_folders(
    tmp_path, django_capture_on_commit_callbacks
):
    event = EventFactory()
    racers, cars = tmp_path / "racers", tmp_path / "cars"
    videos = tmp_path / "videos"
    fakes = [touch(racers / "fake_1.svg"), touch(cars / "fake_2.svg")]
    elsewhere = touch(videos / "fake_3.svg")
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(racers),
        car_photo_directory=str(cars),
        video_directory=str(videos),
    )

    with django_capture_on_commit_callbacks(execute=True):
        reset_event(event)

    assert not any(path.exists() for path in fakes)
    assert elsewhere.exists()
