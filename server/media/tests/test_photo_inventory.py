"""The photo page's inventory (#47): the files photo-thumbs.php lists, the
directory's diagnostics, each racer's stored photo with its `lost_photo`
flag, and the unassign Undo's `restore`."""

import os
from pathlib import Path

import pytest

from core.models import RaceSettings
from media import photos
from media.tests.conftest import image_bytes
from racing.models import AuditEvent
from roster.models import Racer
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db


def put(folder, name, data=None):
    (folder / name).write_bytes(data or image_bytes())


def test_the_listed_files_match_php_scan_directory(event, folders):
    for name in ("b.jpg", "a.PNG", "c.jpeg", "d.gif", "e.bmp", "f.svg", "gjpg"):
        put(folders.head, name)
    put(folders.head, "notes.txt")
    put(folders.head, ".hidden.jpg")
    (folders.head / "0-cropped").mkdir()
    (folders.head / "dir.jpg").mkdir()
    # The face hook's upright copy, which lives only while autocrop runs.
    put(folders.head, ".0123456789ab.upright.jpg")
    repo = photos.repository(event, "head")
    # PHP lists a hidden photo too (checked with php-scratch.sh: an upload
    # named .h.jpg shows in photo-thumbs.php).
    assert photos.photo_files(repo) == [
        ".hidden.jpg",
        "a.PNG",
        "b.jpg",
        "c.jpeg",
        "d.gif",
        "e.bmp",
        "f.svg",
        "gjpg",
    ]


def test_lost_and_unassigned(event, folders):
    for name in ("kept.jpg", "crop.jpg", "free.jpg"):
        put(folders.head, name)
    kept = RacerFactory(group__event=event, headshot="kept.jpg")
    cropped = RacerFactory(group__event=event, headshot="0-cropped/crop.jpg")
    lost = RacerFactory(group__event=event, headshot="gone.jpg")
    none = RacerFactory(group__event=event)
    found = photos.inventory(event, photos.repository(event, "head"))
    assert found.stored == {
        kept.pk: photos.StoredPhoto("kept.jpg", "kept.jpg", False),
        cropped.pk: photos.StoredPhoto("0-cropped/crop.jpg", "crop.jpg", False),
        lost.pk: photos.StoredPhoto("gone.jpg", "gone.jpg", True),
    }
    assert none.pk not in found.stored
    assert found.unassigned == ("free.jpg",)
    assert found.status.kind == "ok" and found.status.writable


def test_the_car_repository_reads_the_car_column(event, folders):
    put(folders.car, "car.jpg")
    racer = RacerFactory(group__event=event, car_photo="car.jpg", headshot="car.jpg")
    found = photos.inventory(event, photos.repository(event, "car"))
    assert found.stored[racer.pk].lost is False
    assert found.unassigned == ()
    head = photos.inventory(event, photos.repository(event, "head"))
    assert head.stored[racer.pk].lost is True


def status(event):
    return photos.inventory(event, photos.repository(event, "head")).status


def test_an_empty_folder(event, folders):
    found = status(event)
    assert (found.kind, found.writable, found.needs_fix) == ("empty", True, False)
    assert found.message == "There are no photos in the photo directory yet."


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a read-only folder")
def test_a_read_only_folder(event, folders):
    put(folders.head, "a.jpg")
    folders.head.chmod(0o555)
    try:
        found = status(event)
    finally:
        folders.head.chmod(0o755)
    assert (found.kind, found.writable, found.needs_fix) == ("read-only", False, True)
    assert found.message == f"Directory {folders.head} is not writable."


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any folder")
def test_an_unreadable_folder(event, folders):
    folders.head.chmod(0o000)
    try:
        found = status(event)
    finally:
        folders.head.chmod(0o755)
    assert (found.kind, found.label) == ("missing", "Unreadable")
    assert found.message == f"Directory {folders.head} cannot be read."


def test_a_missing_folder_a_file_and_no_setting(event, folders):
    folders.head.rmdir()
    assert status(event).message == f"Directory {folders.head} does not exist."
    folders.head.write_text("")
    assert status(event).message == (
        f"Directory path {folders.head} exists but is not a directory."
    )
    RaceSettings.objects.filter(event=event).update(photo_directory="")
    found = status(event)
    assert (found.kind, found.writable) == ("missing", False)
    assert photos.photo_files(photos.repository(event, "head")) == []


# ----- the unassign Undo -----


def test_restore_gives_the_photo_back_once(event, folders):
    put(folders.head, "a.jpg")
    racer = RacerFactory(group__event=event)
    repo = photos.repository(event, "head")
    photos.assign_photo(event, repo, str(racer.pk), "a.jpg", restore=True)
    racer.refresh_from_db()
    assert racer.headshot == "a.jpg"
    audits = AuditEvent.objects.filter(racer=racer).count()
    photos.assign_photo(event, repo, str(racer.pk), "a.jpg", restore=True)
    assert AuditEvent.objects.filter(racer=racer).count() == audits


def test_restore_never_replaces_a_photo_assigned_since(event, folders):
    put(folders.head, "a.jpg")
    put(folders.head, "b.jpg")
    racer = RacerFactory(group__event=event, headshot="b.jpg")
    repo = photos.repository(event, "head")
    with pytest.raises(photos.Refused) as refused:
        photos.assign_photo(event, repo, str(racer.pk), "a.jpg", restore=True)
    assert refused.value.code == "assigned"
    assert Racer.objects.get(pk=racer.pk).headshot == "b.jpg"


def test_assign_with_no_folder_is_refused(event, folders):
    RaceSettings.objects.filter(event=event).update(photo_directory="")
    racer = RacerFactory(group__event=event)
    with pytest.raises(photos.Refused):
        photos.assign_photo(
            event, photos.repository(event, "head"), str(racer.pk), "a.jpg"
        )


# ----- the page's image URLs -----


def test_each_turn_changes_the_work_render_url_within_one_second(
    event, folders, django_capture_on_commit_callbacks
):
    """Two turns in one second kept PHP's breaker, so the crop sheet showed
    the browser's stale work render and the crop box landed in the wrong
    frame (#47, seen in the check-in car photo browser test)."""
    put(folders.head, "a.jpg")
    repo = photos.repository(event, "head")
    seen = [photos.page_url(repo, "a.jpg", photos.WORK)]
    for _ in range(2):
        with django_capture_on_commit_callbacks(execute=True):
            photos.rotate_photo(event, repo, "a.jpg", 90)
        seen.append(photos.page_url(repo, "a.jpg", photos.WORK))
    assert len(set(seen)) == 3
    assert all("?v=" in url for url in seen)


def test_a_page_url_of_nothing_is_empty(event, folders):
    repo = photos.repository(event, "head")
    assert photos.page_url(repo, "", photos.WORK) == ""
    assert "?v=" not in photos.page_url(repo, "gone.jpg", photos.WORK)


def test_a_racer_with_a_hidden_photo_has_it(event, folders):
    put(folders.head, ".h.jpg")
    racer = RacerFactory(group__event=event, headshot=".h.jpg")
    found = photos.inventory(event, photos.repository(event, "head"))
    assert found.stored[racer.pk].lost is False


def test_restore_with_no_photo_never_unassigns(event, folders):
    put(folders.head, "a.jpg")
    racer = RacerFactory(group__event=event, headshot="a.jpg")
    repo = photos.repository(event, "head")
    with pytest.raises(photos.Refused):
        photos.assign_photo(event, repo, str(racer.pk), "", restore=True)
    assert Racer.objects.get(pk=racer.pk).headshot == "a.jpg"


def test_assign_returns_what_it_changed(event, folders):
    put(folders.head, "a.jpg")
    racer = RacerFactory(group__event=event, headshot="0-cropped/old.jpg")
    repo = photos.repository(event, "head")
    removed = photos.assign_photo(event, repo, str(racer.pk), "")
    assert (removed.racer.pk, removed.previous) == (racer.pk, "old.jpg")
    given = photos.assign_photo(event, repo, str(racer.pk), "a.jpg")
    assert (given.racer.first_name, given.previous) == (racer.first_name, "")


def test_delete_reports_a_bad_or_missing_name(event, folders):
    repo = photos.repository(event, "head")
    for name in ("", "../a.jpg", "gone.jpg", None):
        assert photos.delete_photo(event, repo, name) is False
    put(folders.head, "a.jpg")
    assert photos.delete_photo(event, repo, "a.jpg") is True


def test_page_url_never_stats_a_path_outside_the_folder(event, folders, monkeypatch):
    put(folders.head, "a.jpg")
    repo = photos.repository(event, "head")
    seen = []
    real = Path.stat

    def stat(self, *args, **kwargs):
        seen.append(str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    for path in ("../a.jpg", "racers/../../x.jpg", "0-work/a.jpg", "/etc/passwd"):
        assert photos.page_url(repo, path, photos.WORK) == ""
    assert not any(".." in p or p == "/etc/passwd" for p in seen), seen
    assert "?v=" in photos.page_url(repo, "a.jpg", photos.WORK)
    assert photos.page_url(repo, "0-cropped/a.jpg", photos.WORK) != ""
