"""`video.upload`: the replay page's clips (#35).

PHP's check is disabled by `if (false && ...)`, so anyone writes a file of
any type into the video folder (S20). Django needs `CONTROL_RACE`, the replay
login (D16), accepts only `.mkv` and `.webm`, and caps the size
(`VIDEO_UPLOAD_MAX_BYTES`). A name in use gets PHP's `-1`, `-2` suffix.

The name is reduced to its base name, as PHP's `basename()` does, with
control characters dropped. A clip is named after its heat, such as
`Lions & Tigers_Round1_Heat01.mkv`, so spaces and `&` stay (#53).
"""

import re
from pathlib import Path

from django.conf import settings as django_settings
from django.db import transaction

from core.models import RaceSettings
from core.services import lock_race_state
from media.photos import (
    Refused,
    check_upload_size,
    is_upload_name,
    move_failure,
    store_upload,
)

VIDEO_EXTENSIONS = ("mkv", "webm")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def video_name(name: str) -> str:
    """The base name a clip is stored under: after the last `/` or `\\`,
    control characters dropped."""
    return _CONTROL_CHARACTERS.sub("", re.split(r"[/\\]", name or "")[-1])


def upload_video(event, upload, max_file_size=None) -> str:
    """Store the clip and return the name it got. Raises `Refused`."""
    check_upload_size(upload, max_file_size, django_settings.VIDEO_UPLOAD_MAX_BYTES)
    folder = RaceSettings.objects.get(event=event).video_directory
    if not folder:
        raise Refused("not-configured", "No video upload directory configured.")
    name = video_name(upload.name)
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not is_upload_name(name) or extension not in VIDEO_EXTENSIONS:
        raise Refused(
            "upload_video_type",
            f"Unacceptable file type: {name} (a replay video ends in .mkv or .webm)",
        )
    directory = Path(folder)
    if not directory.is_dir():
        raise move_failure(directory, "video")
    with transaction.atomic():
        # A clip of a past event is refused (#212); the lock holds the
        # event writable until the file is stored.
        lock_race_state(event.pk)
        try:
            return store_upload(directory, name, upload, separator="-")
        except OSError:
            raise move_failure(directory, "video") from None
