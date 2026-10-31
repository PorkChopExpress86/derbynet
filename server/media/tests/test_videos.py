"""`video.upload` (#35): S20 and D16."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.permissions import Perm
from core.models import RaceSettings
from media.tests.conftest import log_in

pytestmark = pytest.mark.django_db

CLIP = b"\x1a\x45\xdf\xa3" + b"matroska clip" * 10


def send(client, name, data=CLIP, **fields):
    return client.post(
        "/action.php",
        {"action": "video.upload", "video": SimpleUploadedFile(name, data), **fields},
    ).json()


def test_a_video_upload_needs_a_login(client, folders):
    """S20: PHP's check is `if (false && ...)`, so anyone uploads."""
    reply = send(client, "Lions_Round1_Heat01.mkv")

    assert reply["outcome"]["code"] == "notauthorized"
    assert list(folders.video.iterdir()) == []


def test_the_photo_role_cannot_upload_video(client, folders):
    """D16: the replay login is CONTROL_RACE."""
    log_in(client, Perm.PHOTO_UPLOAD | Perm.ASSIGN_RACER_IMAGE)

    assert send(client, "clip.mkv")["outcome"]["code"] == "notauthorized"


@pytest.mark.parametrize(
    "name", ["shell.php", "clip.mp4", "page.html", "clip", "clip.mkv.php", ".mkv"]
)
def test_only_mkv_and_webm_are_taken(client, folders, name):
    log_in(client, Perm.CONTROL_RACE)

    reply = send(client, name)

    assert reply["outcome"]["code"] == "upload_video_type"
    assert list(folders.video.iterdir()) == []


def test_the_replay_login_uploads_a_clip_with_phps_suffix(client, folders):
    log_in(client, Perm.CONTROL_RACE)

    replies = [send(client, name) for name in ("r.mkv", "r.mkv", "s.WEBM")]

    assert [r["outcome"]["code"] for r in replies] == ["success"] * 3
    assert sorted(p.name for p in folders.video.iterdir()) == [
        "r-1.mkv",
        "r.mkv",
        "s.WEBM",
    ]
    assert (folders.video / "r.mkv").read_bytes() == CLIP


def test_a_clip_past_the_cap_is_refused(client, folders, settings):
    log_in(client, Perm.CONTROL_RACE)
    settings.VIDEO_UPLOAD_MAX_BYTES = len(CLIP) - 1

    assert send(client, "r.mkv")["outcome"]["code"] == "upload_1"
    assert list(folders.video.iterdir()) == []


def test_no_video_folder_is_reported(client, folders, event):
    log_in(client, Perm.CONTROL_RACE)
    RaceSettings.objects.filter(event=event).update(video_directory="")

    assert send(client, "r.mkv")["outcome"]["code"] == "not-configured"
