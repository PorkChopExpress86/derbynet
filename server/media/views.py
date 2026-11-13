"""`photo.php`, `image.php` and `slide.php` (#35), served to anyone, as PHP
serves them to kiosks (D5), with PHP's cache headers.

`photo.php/info/<key>/...` served the file named by any setting, or a path
from the URL (S8). Nothing builds those URLs, and Django answers them 404.
"""

import time
from io import BufferedReader
from pathlib import Path
from urllib.parse import unquote_plus

from django.http import FileResponse, HttpResponse
from django.utils.http import http_date

from core.models import SiteState
from media import photos, slides
from roster.models import Racer

PHOTO_MAX_AGE = 86400
IMAGE_MAX_AGE = 120
# `website/img/1x1.png`, for a racer with no photo.
TRANSPARENT_PIXEL = Path(__file__).parent / "data" / "1x1.png"


def _active_event():
    state = SiteState.objects.select_related("active_event").filter(pk=1).first()
    return state.active_event if state else None


def _not_found(text: str) -> HttpResponse:
    return HttpResponse(f"{text}\n", status=404, content_type="text/plain")


def _serve_file(path: Path, max_age: int, pragma: bool = False) -> HttpResponse:
    try:
        stream = path.open("rb")
    except OSError:
        return _not_found("Can't read file.")
    return _serve(stream, max_age, pragma)


def _serve(stream: BufferedReader, max_age: int, pragma: bool = False) -> HttpResponse:
    """Stream a file the caller opened. A photo is opened under its lock
    (`photos.open_render()`), and streamed here after the lock is let go."""
    response = FileResponse(stream, content_type=photos.content_type(stream.name))
    response["Cache-Control"] = f"max-age={max_age}, public"
    response["Expires"] = http_date(time.time() + max_age)
    if pragma:
        response["Pragma"] = "public"
    return response


def photo(request, rest: str):
    """`photo.php/<repo>/file/<render>/<basename>/<breaker>` and
    `photo.php/<repo>/racer/<racerid>[/<render>]/<breaker>`."""
    parts = ("/" + rest).split("/")
    if len(parts) > 2 and parts[1] == "info":
        return _not_found("Malformed URL")  # S8
    if len(parts) < 4 or parts[2] not in ("file", "racer"):
        return _not_found("Malformed URL")
    event = _active_event()
    repo = photos.repository(event, parts[1]) if event is not None else None
    if repo is None:
        return _not_found("No such repository")
    if parts[2] == "file":
        if len(parts) < 5:
            return _not_found("Malformed URL")
        label = photos.parse_label(parts[3])
        if label is None:
            return _not_found("No such render")
        # PHP decodes the basename a second time, as render_url() encodes it.
        # An OSError is a file that won't open or a render that couldn't be
        # written, under the photo's lock.
        try:
            found = photos.open_render(repo, label, unquote_plus(parts[4]))
        except OSError:
            return _not_found("Can't read file.")
        if found is None:
            return _not_found("No file path")
        return _serve(found, PHOTO_MAX_AGE, pragma=True)

    label = None
    if len(parts) > 5:
        label = photos.parse_label(parts[4])
        if label is None:
            return _not_found("No such render")
    # SQLite reads PHP's text id as a number, so `065` is racer 65.
    racer_id = photos.bounded_int(parts[3])

    def column() -> str:
        if racer_id is None:
            return ""
        return (
            Racer.objects.filter(event=event, pk=racer_id)
            .values_list(repo.field, flat=True)
            .first()
        ) or ""

    # An OSError is a file that won't open or a render that couldn't be
    # written, under the photo's lock.
    try:
        found = photos.open_racer_photo(repo, column, label)
    except OSError:
        return _not_found("Can't read file.")
    # A racer with no photo, or a missing file, gets the transparent pixel,
    # which check-in and kiosk <img> tags rely on.
    if found is None:
        return _serve_file(TRANSPARENT_PIXEL, PHOTO_MAX_AGE, pragma=True)
    return _serve(found, PHOTO_MAX_AGE, pragma=True)


def image(request, rest: str):
    """`image.php/<stem>`: a branding image, such as the emblem."""
    found = slides.image_file(_active_event(), rest)
    if found is None:
        return _not_found("Can't read file.")
    return _serve_file(found, IMAGE_MAX_AGE)


def slide(request, rest: str):
    """`slide.php/<name>` or `slide.php/<subdir>/<name>`; `title` is the
    title slide, whatever its extension."""
    # PHP decodes the path a second time.
    found = slides.slide_file(_active_event(), unquote_plus(rest))
    if found is None:
        return _not_found("Can't read file.")
    return _serve_file(found, IMAGE_MAX_AGE)
