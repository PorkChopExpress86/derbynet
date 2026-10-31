"""`video.upload`: the replay page's clips (#35).

PHP's check is disabled by `if (false && ...)`, so anyone writes a file of
any type into the video folder (S20). Django needs `CONTROL_RACE`, the replay
login (D16), accepts only `.mkv` and `.webm`, and caps the size
(`VIDEO_UPLOAD_MAX_BYTES`). A name in use gets PHP's `-1`, `-2` suffix.
"""

from pathlib import Path

from django.conf import settings as django_settings

from core.models import RaceSettings
from media.photos import (
    UNSAFE_NAME_CHARACTERS,
    Refused,
    check_upload_size,
    is_valid_name,
    move_failure,
    store_upload,
)

VIDEO_EXTENSIONS = ("mkv", "webm")


def upload_video(event, upload, max_file_size=None) -> str:
    """Store the clip and return the name it got. Raises `Refused`."""
    check_upload_size(upload, max_file_size, django_settings.VIDEO_UPLOAD_MAX_BYTES)
    folder = RaceSettings.objects.get(event=event).video_directory
    if not folder:
        raise Refused("not-configured", "No video upload directory configured.")
    name = UNSAFE_NAME_CHARACTERS.sub("_", (upload.name or "").rsplit("/", 1)[-1])
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not is_valid_name(name) or extension not in VIDEO_EXTENSIONS:
        raise Refused(
            "upload_video_type",
            f"Unacceptable file type: {name} (a replay video ends in .mkv or .webm)",
        )
    directory = Path(folder)
    if not directory.is_dir():
        raise move_failure(directory, "video")
    try:
        return store_upload(directory, name, upload, separator="-")
    except OSError:
        raise move_failure(directory, "video") from None
