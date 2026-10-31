"""Slides and branding images: `slide.php`, `image.php` and `query=slide.next`
(#35), from `website/inc/path-info.inc` and `query.slide.next.inc`.

Each looks first in the event's folder (`events/<slug>/slides` or `imagery`,
D57), then in the install's config folder, as PHP looks in its data folder,
and last in the selected image set. The image sets that ship with DerbyNet
are copied from `website/Images` into `media/image_sets`.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from django.conf import settings as django_settings

from core.models import RaceSettings
from core.services import event_dir
from core.slides import is_acceptable_name
from racing.services.polls import mangled_name
from roster.models import Racer
from roster.services.racers import CAR_PHOTOS, HEADSHOTS, photo_url

IMAGE_SETS = Path(__file__).parent / "image_sets"
DEFAULT_IMAGE_SET = "Generic"


def image_set_dir(event) -> Path:
    """The selected image set's folder. A setting that names no shipped set,
    or names a path, gets `Generic`."""
    name = DEFAULT_IMAGE_SET
    if event is not None:
        name = RaceSettings.objects.get(event=event).images_dir
    if name not in _image_sets():
        name = DEFAULT_IMAGE_SET
    return IMAGE_SETS / name


def _image_sets() -> set[str]:
    return {entry.name for entry in os.scandir(IMAGE_SETS) if entry.is_dir()}


def _plain_name(name: str) -> bool:
    """One file name: no separator, and not `.` or `..`."""
    return name not in ("", ".", "..") and "/" not in name and "\0" not in name


def _first_match(folder: Path, matches) -> Path | None:
    """The first file in `folder`, in `glob()`'s sorted order, that matches."""
    try:
        names = sorted(entry.name for entry in os.scandir(folder) if entry.is_file())
    except OSError:
        return None
    return next((folder / name for name in names if matches(name)), None)


def _stem_matcher(stem: str):
    """PHP's glob `<stem>.*`."""
    return lambda name: name.startswith(stem + ".")


def image_file(event, stem: str) -> Path | None:
    """`image_file_path()`: `<stem>.*` in the event's imagery folder, the
    config folder's, then the image set. The stem is matched as text, where
    PHP passes glob characters through."""
    if not _plain_name(stem):
        return None
    folders = [django_settings.CONFIG_DIR / "imagery", image_set_dir(event)]
    if event is not None:
        folders.insert(0, event_dir(event.slug) / "imagery")
    for folder in folders:
        found = _first_match(folder, _stem_matcher(stem))
        if found is not None:
            return found
    return None


def slide_file(event, path: str) -> Path | None:
    """`slide_file_path()` for `slide.php/<name>` or `slide.php/<subdir>/<name>`.
    `title` matches `title.*`. The subfolder follows PHP's slide.next rule,
    and the name is one plain name. PHP checks the name only, so its subfolder
    can be `..`."""
    parts = path.split("/")
    if len(parts) == 2 and is_acceptable_name(parts[0]):
        subdir, name = parts
    elif len(parts) == 1:
        subdir, name = None, parts[0]
    else:
        return None
    if not _plain_name(name):
        return None
    matches = _stem_matcher("title") if name == "title" else (lambda n: n == name)
    relative = Path(subdir) if subdir else Path()
    folders = [django_settings.CONFIG_DIR / "slides" / relative]
    if event is not None:
        folders.insert(0, event_dir(event.slug) / "slides" / relative)
    # An image set holds only a title slide.
    folders.append(image_set_dir(event) / "slides" / relative)
    for folder in folders:
        found = _first_match(folder, matches)
        if found is not None:
            return found
    return None


def _slides_after(folder: Path, current: str) -> list[str]:
    """`enumerate_non_title_slides()`: the files after `current` by name,
    leaving out hidden files and the title slide."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    return [
        entry.name
        for entry in entries
        if entry.name > current
        and not entry.is_dir()
        and not entry.name.startswith(".")
        and not entry.name.startswith("title.")
    ]


def next_slides(event, current: str, subdir: str) -> list[str]:
    relative = Path(subdir) if subdir else Path()
    folders = [
        event_dir(event.slug) / "slides" / relative,
        django_settings.CONFIG_DIR / "slides" / relative,
    ]
    return sorted(name for folder in folders for name in _slides_after(folder, current))


class BadClassIds(ValueError):
    pass


_CLASS_ID = re.compile(r"\s*[+-]?[0-9]+\s*")


def parse_class_ids(text) -> list[int]:
    """`classids` as a comma-separated list of integers. PHP pastes the text
    into its SQL (S10), so anything else is refused."""
    parts = text.split(",") if isinstance(text, str) else [None]
    if not all(isinstance(p, str) and _CLASS_ID.fullmatch(p) for p in parts):
        raise BadClassIds(text)
    return [int(p) for p in parts]


@dataclass(frozen=True)
class SlideRequest:
    mode: str = "title"
    subdir: str = ""
    racer_id: int = 0
    file: str = ""
    class_ids: list[int] | None = None


def next_slide(event, request: SlideRequest) -> dict:
    """`query=slide.next`: the reply's keys, in PHP's order.

    `slide` mode gives the next slide after `file`, then falls to the racers;
    `racer` mode gives the next racer after `racer_id` with a photo, then
    falls to the title slide.
    """
    out = {}
    subdir = request.subdir
    if subdir:
        out["subdir"] = subdir
    prefix = f"{subdir}/" if subdir else ""
    mode, racer_id = request.mode, request.racer_id
    if mode == "slide":
        found = next_slides(event, request.file, subdir)
        if found:
            out["debug-results"] = found
            out["photo"] = {
                "photo": f"slide.php/{prefix}{quote(found[0], safe='')}",
                "next": {"mode": "slide", "file": found[0]},
            }
            return out
        mode, racer_id = "racer", 0
    if mode == "racer":
        racer = _next_racer(event, racer_id, request.class_ids)
        if racer is not None:
            out["photo"] = _racer_slide(event, racer)
            return out
    out["photo"] = {
        "photo": f"slide.php/{prefix}title",
        "title": True,
        "next": {"mode": "slide", "file": ""},
    }
    return out


def _next_racer(event, after: int, class_ids) -> Racer | None:
    racers = (
        Racer.objects.filter(event=event, pk__gt=after, passed_inspection=True)
        .exclude(headshot="", car_photo="")
        .order_by("pk")
    )
    if class_ids is not None:
        racers = racers.filter(group_id__in=class_ids)
    return racers.first()


def _racer_slide(event, racer: Racer) -> dict:
    """The racer's car photo with the headshot as an inset, or the headshot
    alone. An empty column counts as no photo, where PHP tests for NULL."""
    settings = RaceSettings.objects.get(event=event)
    out = {
        "name": mangled_name(racer.first_name, racer.last_name, settings.name_style),
        "carnumber": racer.car_number,
        "carname": racer.car_name,
        "next": {"mode": "racer", "racerid": racer.pk},
    }
    car_folder = settings.car_photo_directory or settings.photo_directory
    headshot = photo_url(HEADSHOTS, settings.photo_directory, racer.headshot, "cropped")
    if racer.car_photo:
        out["photo"] = photo_url(CAR_PHOTOS, car_folder, racer.car_photo, "cropped")
        if racer.headshot:
            out["inset"] = headshot
    else:
        out["photo"] = headshot
    return out
