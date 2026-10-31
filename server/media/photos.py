"""Racer and car photos: `PhotoRepository` and `PhotoRender` of
`website/inc/photo-config.inc`, and the photo actions (#35).

A repository is a folder named by a setting: `head` is `photo_directory` and
`car` is `car_photo_directory`, which falls back to `photo_directory`. The
upload sits at the top of the folder and its renders sit in subfolders, as in
PHP: `0-cropped`, `0-work` (600x600 from the original) and `<W>x<H>`. Django
adds `0-rotated` for the original of a turned photo.

#8's storage rule:

- The uploaded bytes are kept unchanged. `original`, `cropped` and a racer's
  photo serve them while the photo has no crop and no rotation.
- Every decode applies `ImageOps.exif_transpose()` first, so renders, crops,
  autocrop and rotation see the photo upright (#73). Renders carry no
  orientation tag.
- A crop box is in the upright, turned frame that the `work` render shows.
- `photo.rotate` stores the rotation (`PhotoRotation`) and leaves the upload.
- PNG renders use Pillow's default compression, where PHP writes level 0.

A render is made on first request under a per-file lock, and only for the
labels in `ALLOWED_BOXES`. Any other size gets the allowed render nearest to
it, so a client cannot create folders. Sizes truncate as PHP's do: 921x299
becomes 200x64 at `200x200`.

A file is written before the transaction that refers to it, and removed only
after the transaction that stops referring to it commits.
"""

import fcntl
import math
import os
import re
import secrets
import time
import zlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote_plus

from django.conf import settings as django_settings
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from core.models import RaceSettings
from core.services import lock_race_state
from media import faces
from media.models import PhotoRotation
from racing.engine.phpcompat import php_round
from racing.models import AuditEvent
from roster.models import Racer
from roster.services import barcode
from roster.services.racers import HEADSHOTS, photo_url

ORIGINAL = "original"
CROPPED = "cropped"
WORK = "work"
RENDER_THUMBNAIL = "200x200"
RENDER_CHECKIN = "200x200"

CROPPED_FOLDER = "0-cropped"
WORK_FOLDER = "0-work"
ROTATED_FOLDER = "0-rotated"

# `Racer.headshot` holds 255 characters, `0-cropped/` included.
MAX_NAME_LENGTH = 255 - len(CROPPED_FOLDER) - 1
# `photo.upload` keeps these characters and replaces each run of others with `_`.
UNSAFE_NAME_CHARACTERS = re.compile(r"[^-a-zA-Z0-9 .]+")
# The formats PHP's GD reads, and the extensions a file of each may carry.
EXTENSIONS = {
    "JPEG": ("jpg", "jpeg", "jpe"),
    "PNG": ("png",),
    "GIF": ("gif",),
}
JPEG_QUALITY = 90

# `pseudo_mime_content_type()`, for the extensions a photo, image set or
# slide folder holds.
CONTENT_TYPES = {
    "png": "image/png",
    "jpe": "image/jpeg",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "ico": "image/vnd.microsoft.icon",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    "svg": "image/svg+xml",
    "svgz": "image/svg+xml",
    "webp": "image/webp",
}


def content_type(path: Path | str) -> str:
    extension = str(path).rsplit(".", 1)[-1].lower() if "." in str(path) else ""
    return CONTENT_TYPES.get(extension, "application/octet-stream")


@dataclass(frozen=True)
class Box:
    """A sized render: the largest width and height, 0 for no limit."""

    width: int
    height: int

    @property
    def folder(self) -> str:
        return f"{self.width}x{self.height}"


WORK_BOX = Box(600, 600)
# The sized renders a client may get. `200x200` and `400x400` are what PHP's
# pages name (RENDER_LISTVIEW, RENDER_ONDECK). The now-racing poll asks for
# `<2h>x<h>` at a row height `h`, and the columnar page for its column size;
# both get the nearest of these.
ALLOWED_BOXES = (
    *(Box(side, side) for side in (100, 150, 200, 300, 400)),
    *(Box(2 * h, h) for h in (40, 50, 60, 80, 100, 120, 150, 200, 250, 300)),
)
_ALLOWED_FOLDERS = {box.folder: box for box in ALLOWED_BOXES}
_PHP_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_SIZED_LABEL = re.compile(rf"({_PHP_NUMBER})x({_PHP_NUMBER})|({_PHP_NUMBER})h")


@dataclass(frozen=True)
class Label:
    """A render label: `original`, `cropped`, `work`, or a requested size."""

    name: str
    requested: tuple[float, float] | None = None


def parse_label(text: str) -> Label | None:
    """A label PHP's `lookup()` accepts, or None, where PHP answers `No such
    render`."""
    if text in (ORIGINAL, CROPPED, WORK):
        return Label(text)
    found = _SIZED_LABEL.fullmatch(text)
    if not found:
        return None
    if found[3] is not None:
        return Label(text, (0.0, float(found[3])))
    return Label(text, (float(found[1]), float(found[2])))


def scaled_size(width, height, max_width, max_height) -> tuple[int, int]:
    """`find_or_make_image_file()`'s size: scale to fit, truncating, where 0
    is no limit. PHP also scales a small photo up."""
    if not max_width and not max_height:
        return int(width), int(height)
    if not max_height or (max_width and width / height > max_width / max_height):
        return int(max_width), int(height * max_width / width)
    return int(width * max_height / height), int(max_height)


def nearest_box(requested: tuple[float, float], size: tuple[int, int]) -> Box:
    """The allowed render nearest the requested one for a photo of `size`:
    the largest whose result fits inside the requested result, so a page
    never gets a bigger image than it laid out, or else the smallest."""
    for box in ALLOWED_BOXES:
        if (box.width, box.height) == requested:
            return box
    want = scaled_size(*size, *requested)
    results = [
        (scaled_size(*size, box.width, box.height), box) for box in ALLOWED_BOXES
    ]
    fitting = [r for r in results if r[0][0] <= want[0] and r[0][1] <= want[1]]
    if fitting:
        return max(fitting, key=lambda r: (r[0][0] * r[0][1], -r[1].width))[1]
    return min(results, key=lambda r: (r[0][0] * r[0][1], r[1].width))[1]


def is_valid_name(name) -> bool:
    """A file name that stays in its folder and fits a racer's column."""
    return (
        isinstance(name, str)
        and 0 < len(name) <= MAX_NAME_LENGTH
        and "/" not in name
        and "\\" not in name
        and "\0" not in name
        and name.strip(".") != ""
    )


@dataclass(frozen=True)
class Repository:
    name: str
    directory: Path | None
    # The `Racer` field that names a photo of this repository.
    field: str

    def file(self, name: str, folder: str | None = None) -> Path:
        assert self.directory is not None
        return self.directory / folder / name if folder else self.directory / name

    @property
    def key(self) -> str:
        """The folder as `PhotoRotation` names it."""
        return os.path.normpath(os.path.abspath(self.directory))

    def url(self, path: str, render: str) -> str:
        return photo_url(self.name, str(self.directory or ""), path, render)


def repository(event, name: str) -> Repository | None:
    """The `head` or `car` repository of the event, or None for another name.
    An unset folder gives a repository with no directory, which holds no
    photos."""
    settings = RaceSettings.objects.get(event=event)
    match name:
        case "head":
            folder, field_name = settings.photo_directory, "headshot"
        case "car":
            folder = settings.car_photo_directory or settings.photo_directory
            field_name = "car_photo"
        case _:
            return None
    return Repository(name, Path(folder) if folder else None, field_name)


def rotation_of(repo: Repository, name: str) -> float:
    row = PhotoRotation.objects.filter(directory=repo.key, name=name).first()
    return row.degrees if row else 0.0


# ----- Decoding and encoding -----------------------------------------------------


def _is_svg(path: Path) -> bool:
    return path.suffix.lower() in (".svg", ".svgz")


def _open_upright(path: Path, rotation: float = 0.0) -> tuple[Image.Image, str]:
    """The decoded image, turned by its orientation tag and then by
    `rotation`, and its format."""
    with Image.open(path) as opened:
        fmt = opened.format
        image = ImageOps.exif_transpose(opened)
    if rotation:
        image = _rotate(image, rotation)
    return image, fmt


def _upright_size(path: Path, rotation: float = 0.0) -> tuple[int, int]:
    """The size `_open_upright()` gives, from the header alone."""
    with Image.open(path) as opened:
        width, height = opened.size
        if opened.getexif().get(0x0112) in (5, 6, 7, 8):
            width, height = height, width
    turn = math.radians(rotation % 360)
    if rotation % 180 == 90:
        return height, width
    if rotation % 90:
        return (
            math.ceil(abs(width * math.cos(turn)) + abs(height * math.sin(turn))),
            math.ceil(abs(width * math.sin(turn)) + abs(height * math.cos(turn))),
        )
    return width, height


def _workable(image: Image.Image) -> Image.Image:
    """A mode that resizes, rotates and crops without losing transparency."""
    if image.mode in ("RGB", "RGBA", "L", "LA", "CMYK"):
        return image
    if image.mode in ("P", "PA") or "transparency" in image.info:
        return image.convert("RGBA")
    return image.convert("RGB")


def _rotate(image: Image.Image, degrees: float) -> Image.Image:
    """Counterclockwise, as GD's `imagerotate()`, filling the corners black
    (transparent with an alpha channel)."""
    image = _workable(image)
    fill = {"RGBA": (0, 0, 0, 0), "LA": (0, 0), "L": 0, "CMYK": (0, 0, 0, 255)}
    return image.rotate(degrees, expand=True, fillcolor=fill.get(image.mode, (0, 0, 0)))


def _encode(image: Image.Image, fmt: str, target) -> None:
    """Write in the source's format, with no orientation tag."""
    if fmt == "JPEG":
        if image.mode not in ("RGB", "L", "CMYK"):
            image = image.convert("RGB")
        image.save(target, "JPEG", quality=JPEG_QUALITY)
    elif fmt == "PNG":
        image.save(target, "PNG")
    else:
        image.save(target, "GIF")


# ----- Writing files -------------------------------------------------------------

LOCK_STRIPES = 64


@contextmanager
def _file_lock(target: Path):
    """An exclusive lock for one file, across threads and processes.

    The locks are a fixed set of files in the data folder, picked by the
    target's path, so no lock file is left in a photo folder.
    """
    folder = django_settings.DATA_DIR / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    stripe = zlib.crc32(os.fsencode(target)) % LOCK_STRIPES
    descriptor = os.open(folder / f"render-{stripe:02d}.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write_atomically(target: Path, write: Callable) -> None:
    """Write through a temporary file in the same folder and move it into
    place, so a reader sees the old file or the whole new one. The new file
    gets the service user's umask, where PHP made folders world-writable."""
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            write(stream)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _make(target: Path, build: Callable[[], tuple[Image.Image, str]]) -> Path | None:
    """`target`, made by `build()` under the file's lock unless it exists."""
    if target.is_file():
        return target
    try:
        target.parent.mkdir(exist_ok=True)
        with _file_lock(target):
            if not target.is_file():
                image, fmt = build()
                _write_atomically(target, lambda stream: _encode(image, fmt, stream))
    except OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError:
        return None
    return target


def _resized(image: Image.Image, box: Box) -> Image.Image:
    width, height = scaled_size(image.width, image.height, box.width, box.height)
    return _workable(image).resize(
        (max(1, width), max(1, height)), Image.Resampling.LANCZOS
    )


# ----- Renders -------------------------------------------------------------------


def find_or_make(repo: Repository, label: Label, name: str) -> Path | None:
    """`find_or_make_image_file()`: the file that serves `name` at `label`,
    made if it is missing, or None when there is no such photo."""
    if repo.directory is None or not is_valid_name(name):
        return None
    original = repo.file(name)
    cropped = repo.file(name, CROPPED_FOLDER)
    if label.name == CROPPED and cropped.is_file():
        return cropped
    if label.name in (ORIGINAL, CROPPED):
        return _original(repo, name)
    if label.name == WORK:
        source, rotation, box, folder = original, None, WORK_BOX, WORK_FOLDER
    else:
        # A sized render starts from the crop, which already holds the turn.
        source, rotation = (cropped, 0.0) if cropped.is_file() else (original, None)
        box = None
    if not source.is_file():
        return None
    if _is_svg(source):
        return source
    if rotation is None:
        rotation = rotation_of(repo, name)
    if box is None:
        try:
            box = nearest_box(label.requested, _upright_size(source, rotation))
        except OSError, UnidentifiedImageError, Image.DecompressionBombError:
            return None
        folder = box.folder

    def build():
        image, fmt = _open_upright(source, rotation)
        return _resized(image, box), fmt

    return _make(repo.file(name, folder), build)


def _original(repo: Repository, name: str) -> Path | None:
    """The upload itself, or for a turned photo, its turned render."""
    original = repo.file(name)
    if not original.is_file():
        return None
    rotation = rotation_of(repo, name)
    if not rotation or _is_svg(original):
        return original
    return _make(
        repo.file(name, ROTATED_FOLDER), lambda: _open_upright(original, rotation)
    )


def racer_photo(repo: Repository, path: str, label: Label | None) -> Path | None:
    """The file `photo.php/<repo>/racer/<id>[/<render>]` serves for a racer
    whose column holds `path`. With no render, the file the column names."""
    if repo.directory is None or not path:
        return None
    name = path.rsplit("/", 1)[-1]
    if label is not None:
        return find_or_make(repo, label, name)
    if path.startswith(CROPPED_FOLDER + "/"):
        cropped = repo.file(name, CROPPED_FOLDER)
        return cropped if cropped.is_file() else None
    return _original(repo, name)


def _dependents(repo: Repository, name: str, keep: str | None) -> list[Path]:
    """`delete_dependents()`: the renders of `name` in every subfolder but
    `keep`."""
    found = []
    try:
        entries = list(os.scandir(repo.directory))
    except OSError:
        return found
    for entry in entries:
        if entry.name != keep and entry.is_dir(follow_symlinks=False):
            candidate = Path(entry.path) / name
            if candidate.is_file() or candidate.is_symlink():
                found.append(candidate)
    return found


def _unlink_all(paths) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def _stored_path(repo: Repository, name: str) -> str:
    """The racer column for `name`: its crop when there is one."""
    return (
        f"{CROPPED_FOLDER}/{name}"
        if repo.file(name, CROPPED_FOLDER).is_file()
        else name
    )


# ----- photo.upload --------------------------------------------------------------


class Refused(Exception):
    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


@dataclass(frozen=True)
class ByRacerId:
    text: str


@dataclass(frozen=True)
class ByCarNumber:
    text: str


@dataclass(frozen=True)
class ByBarcode:
    text: str


Selector = ByRacerId | ByCarNumber | ByBarcode | None


@dataclass
class UploadResult:
    """What `photo.upload` reports, in PHP's order."""

    filename_change: str | None = None
    cropped: bool = False
    warning: str | None = None
    failure: tuple[str, str] | None = None
    photo_url: str | None = None
    uploaded: str | None = None
    thumbnail: str | None = None


def sanitized_name(posted: str) -> str:
    """PHP's `basename(urldecode($name))` with the unsafe characters
    replaced. A name too long for a racer's column keeps its extension."""
    name = UNSAFE_NAME_CHARACTERS.sub("_", unquote_plus(posted).rsplit("/", 1)[-1])
    if len(name) > MAX_NAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and len(extension) < 10:
            name = stem[: MAX_NAME_LENGTH - len(extension) - 1] + "." + extension
        else:
            name = name[:MAX_NAME_LENGTH]
    return name


def suffixed_name(name: str, attempt: int) -> str:
    """PHP's `_1`, `_2` before the extension."""
    stem, dot, extension = name.rpartition(".")
    return f"{stem}_{attempt}.{extension}" if dot else f"{name}_{attempt}"


def decoded_format(upload) -> str:
    """The upload's format, decoded by Pillow rather than taken from the
    client's MIME type. Raises `Refused` for anything GD could not read."""
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            fmt = image.format
            image.load()
    except (
        OSError,
        UnidentifiedImageError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ):
        fmt = None
    finally:
        upload.seek(0)
    if fmt not in EXTENSIONS:
        kind = Image.MIME.get(fmt, "not an image") if fmt else "not an image"
        raise Refused("upload_image_type", f"Unacceptable file type: {kind}")
    return fmt


def check_upload_size(upload, max_file_size, cap: int) -> None:
    """PHP's upload errors for a missing file and for one past the form's
    `MAX_FILE_SIZE`, and the server's own cap."""
    if upload is None:
        raise Refused("upload_4", "No file was uploaded.")
    try:
        form_limit = int(max_file_size) if max_file_size is not None else None
    except TypeError, ValueError:
        form_limit = None
    if form_limit is not None and 0 < form_limit < upload.size:
        raise Refused(
            "upload_2",
            "The uploaded file exceeds the MAX_FILE_SIZE directive that was"
            " specified in the HTML form.",
        )
    if upload.size > cap:
        raise Refused(
            "upload_1", f"The uploaded file exceeds the server's limit of {cap} bytes."
        )


def store_upload(folder: Path, name: str, upload, separator="_") -> str:
    """Write the upload's bytes unchanged under `name`, or the first free
    suffixed name, and return the name used. Creating the file exclusively
    keeps two uploads of one name apart."""
    attempt = 0
    while True:
        target = name if attempt == 0 else _suffixed(name, attempt, separator)
        try:
            descriptor = os.open(
                folder / target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666
            )
        except FileExistsError:
            attempt += 1
            continue
        except IsADirectoryError:
            attempt += 1
            continue
        try:
            with os.fdopen(descriptor, "wb") as stream:
                for chunk in upload.chunks():
                    stream.write(chunk)
        except BaseException:
            (folder / target).unlink(missing_ok=True)
            raise
        return target


def _suffixed(name: str, attempt: int, separator: str) -> str:
    if separator == "_":
        return suffixed_name(name, attempt)
    stem, dot, extension = name.rpartition(".")
    return f"{stem}-{attempt}.{extension}" if dot else f"{name}-{attempt}"


def move_failure(folder: Path, what: str) -> Refused:
    description = "move_uploaded_file failed."
    if not os.access(folder, os.R_OK):
        description += f"  <b>Check settings: {what} directory is not readable.</b>"
    if not os.access(folder, os.W_OK):
        description += f"  <b>Check settings: {what} directory is not writable.</b>"
    return Refused("move_uploaded", description)


def widen_and_clip(
    start: int, length: int, scale: float, limit: int
) -> tuple[int, int]:
    """PHP's `widen_and_clip()`: widen about the middle, then clip to the
    image."""
    original = length
    length = php_round(length * scale)
    start -= php_round((length - original) / 2)
    if start < 0:
        length += start
        start = 0
    if start + length > limit:
        length = limit - start
    return start, length


def autocrop(repo: Repository, name: str, result: UploadResult) -> bool:
    """Crop a fresh upload to the face, widened 10% and made 4:3 tall, as
    PHP does. Returns whether it cropped; a miss or an error is a warning."""
    original = repo.file(name)
    try:
        image, fmt = _open_upright(original)
        face = _find_face(original, image, fmt)
        if face is None:
            result.warning = "No face detected for autocrop."
            return False
        x, w = widen_and_clip(
            php_round(face.x), php_round(face.width), 1.10, image.width
        )
        y, h = widen_and_clip(php_round(face.y), w, 4 / 3, image.height)
        if w <= 0 or h <= 0:
            result.warning = f"Autocrop gave {w} width x {h} height"
            return False
        target = repo.file(name, CROPPED_FOLDER)
        target.parent.mkdir(exist_ok=True)
        crop = image.crop((x, y, x + w, y + h))
        with _file_lock(target):
            _write_atomically(target, lambda stream: _encode(crop, fmt, stream))
    except Exception as error:  # PHP turns any exception into the warning.
        result.warning = str(error)
        return False
    result.cropped = True
    return True


def _find_face(original: Path, image: Image.Image, fmt: str):
    command = django_settings.FACE_DETECT_COMMAND
    if not faces.hook_is_usable(command):
        return faces.detect(image)
    with Image.open(original) as opened:
        tagged = opened.getexif().get(0x0112, 1) not in (1, None)
    if not tagged:
        return faces.detect_with_hook(command, str(original))
    # The hook reads the file, so it gets an upright copy when the stored
    # pixels are turned, and its box lands in the frame the crop uses.
    temporary = original.with_name(f".{original.name}.{secrets.token_hex(6)}.upright")
    try:
        with temporary.open("wb") as stream:
            _encode(image, fmt, stream)
        return faces.detect_with_hook(command, str(temporary))
    finally:
        temporary.unlink(missing_ok=True)


def upload_photo(
    event,
    repo: Repository,
    upload,
    *,
    max_file_size=None,
    autocrop_requested: bool = False,
    selector: Selector = None,
) -> UploadResult:
    """`photo.upload`. Raises `Refused` before anything is stored."""
    check_upload_size(upload, max_file_size, django_settings.PHOTO_UPLOAD_MAX_BYTES)
    decoded_format(upload)
    name = sanitized_name(upload.name or "")
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not is_valid_name(name) or extension not in sum(EXTENSIONS.values(), ()):
        raise Refused(
            "upload_image_type",
            f"Unacceptable file name: {name} (a photo's name ends in .jpg, .jpeg,"
            " .png or .gif)",
        )
    if repo.directory is None or not repo.directory.is_dir():
        raise move_failure(repo.directory or Path(""), "photo")
    try:
        target = store_upload(repo.directory, name, upload)
    except OSError:
        raise move_failure(repo.directory, "photo") from None

    result = UploadResult()
    if target != name:
        result.filename_change = target  # PHP echoes an undefined variable (C27).
    if autocrop_requested:
        autocrop(repo, target, result)

    path = f"{CROPPED_FOLDER}/{target}" if result.cropped else target
    success = True
    match selector:
        case ByBarcode("PWDuploadtest"):
            # The photo stand's test: upload, delete, and report success.
            _unlink_all([repo.file(target), repo.file(target, CROPPED_FOLDER)])
        case ByBarcode("PWDuploadonly") | None:
            pass
        case _:
            success = _assign_upload(event, repo, path, selector, result)
    if success:
        result.uploaded = target
        result.thumbnail = repo.url(_stored_path(repo, target), RENDER_THUMBNAIL)
    return result


def _assign_upload(event, repo, path, selector, result: UploadResult) -> bool:
    with transaction.atomic():
        lock_race_state(event.id)
        racers = Racer.objects.filter(event=event)
        match selector:
            case ByRacerId(text):
                # PHP skips an empty or zero `racerid`.
                if text in ("", "0"):
                    return True
                racer = _by_id(racers, text)
                if racer is None:
                    result.failure = ("noracer", "No such racer.")
                    return False
            case ByCarNumber(text):
                racer = None
                if re.fullmatch(r"[+-]?[0-9]+", text.strip()):
                    racer = racers.filter(car_number=int(text)).order_by("pk").first()
                if racer is None:
                    result.failure = ("no-such-car", f"No car numbered {text} found.")
                    return False
            case ByBarcode(text):
                racer = barcode.resolve(event, text).racer
                if racer is None:
                    result.failure = ("barcode", f"Barcode {text} not recognized.")
                    return False
        _set_photo(event, repo, racer, path)
    result.photo_url = repo.url(path, RENDER_CHECKIN)
    return True


def _by_id(racers, text) -> Racer | None:
    try:
        return racers.filter(pk=int(str(text).strip())).first()
    except ValueError:
        return None


def _set_photo(event, repo: Repository, racer: Racer, path: str) -> None:
    Racer.objects.filter(pk=racer.pk).update(**{repo.field: path})
    AuditEvent.objects.create(
        event=event,
        action=AuditEvent.Action.RACER_PHOTO_ASSIGN
        if repo.name == HEADSHOTS
        else AuditEvent.Action.CAR_PHOTO_ASSIGN,
        racer=racer,
    )


# ----- photo.assign, photo.crop, photo.rotate, photo.delete ------------------------


def assign_photo(event, repo: Repository, racer_id, photo) -> None:
    """`photo.assign`: give the racer the photo, by its crop when it has one,
    or take the racer's photo away when `photo` is empty."""
    path = ""
    if photo:
        if find_or_make(repo, Label(CROPPED), photo) is None:
            raise Refused("nosuchfile", f"Image {photo} not found.")
        path = _stored_path(repo, photo)
    with transaction.atomic():
        lock_race_state(event.id)
        racer = _by_id(Racer.objects.filter(event=event), racer_id)
        if racer is None:
            raise Refused("noracer", "No such racer.")
        _set_photo(event, repo, racer, path)


@dataclass(frozen=True)
class CropBox:
    left: float
    top: float
    right: float
    bottom: float
    original_width: float
    original_height: float


def crop_photo(event, repo: Repository, name: str, box: CropBox) -> None:
    """`photo.crop`: cut the box, given in pixels of an image of
    `original_width` x `original_height` that shows the upright, turned
    photo, out of the original."""
    original = repo.file(name) if repo.directory and is_valid_name(name) else None
    if original is None or not original.is_file():
        raise Refused("nosuchfile", f"Image {name} not found.")
    if box.original_width <= 0 or box.original_height <= 0:
        raise Refused("crop", "The crop needs the image's width and height.")
    try:
        image, fmt = _open_upright(original, rotation_of(repo, name))
    except OSError, UnidentifiedImageError, Image.DecompressionBombError:
        raise Refused("nosuchfile", f"Image {name} can't be read.") from None
    # GD's imagecopy() truncates the float coordinates.
    x = int(box.left * image.width / box.original_width)
    y = int(box.top * image.height / box.original_height)
    width = int((box.right - box.left) * image.width / box.original_width)
    height = int((box.bottom - box.top) * image.height / box.original_height)
    if width <= 0 or height <= 0:
        raise Refused("crop", "The crop is empty.")
    target = repo.file(name, CROPPED_FOLDER)
    target.parent.mkdir(exist_ok=True)
    crop = _workable(image).crop((x, y, x + width, y + height))
    with _file_lock(target):
        _write_atomically(target, lambda stream: _encode(crop, fmt, stream))
    with transaction.atomic():
        lock_race_state(event.id)
        Racer.objects.filter(event=event, **{repo.field: name}).update(
            **{repo.field: f"{CROPPED_FOLDER}/{name}"}
        )
        stale = _dependents(repo, name, keep=CROPPED_FOLDER)
        transaction.on_commit(lambda: _unlink_all(stale))


def rotate_photo(event, repo: Repository, name: str, degrees: float) -> None:
    """`photo.rotate`: turn the photo `degrees` counterclockwise more. The
    upload stays; the crop and every render go, and racers shown by the crop
    are shown by the original again."""
    original = repo.file(name) if repo.directory and is_valid_name(name) else None
    if original is None or not original.is_file():
        raise Refused("nosuchfile", f"Image {name} not found.")
    with transaction.atomic():
        lock_race_state(event.id)
        row = (
            PhotoRotation.objects.select_for_update()
            .filter(directory=repo.key, name=name)
            .first()
        )
        total = ((row.degrees if row else 0.0) + degrees) % 360
        if total == 0:
            PhotoRotation.objects.filter(directory=repo.key, name=name).delete()
        elif row:
            PhotoRotation.objects.filter(pk=row.pk).update(degrees=total)
        else:
            PhotoRotation.objects.create(directory=repo.key, name=name, degrees=total)
        Racer.objects.filter(
            event=event, **{repo.field: f"{CROPPED_FOLDER}/{name}"}
        ).update(**{repo.field: name})
        stale = _dependents(repo, name, keep=None)

        def tidy():
            _unlink_all(stale)
            # The upload's bytes stay, so its time changes instead, and with
            # it every cache breaker that names the photo.
            try:
                os.utime(original)
            except OSError:
                pass

        transaction.on_commit(tidy)


def delete_photo(event, repo: Repository, name) -> None:
    """`photo.delete`: forget the photo and remove it from every folder of the
    repository, after the racers that showed it lose it."""
    if repo.directory is None or not is_valid_name(name):
        return
    with transaction.atomic():
        lock_race_state(event.id)
        Racer.objects.filter(
            event=event, **{f"{repo.field}__in": [name, f"{CROPPED_FOLDER}/{name}"]}
        ).update(**{repo.field: ""})
        PhotoRotation.objects.filter(directory=repo.key, name=name).delete()
        directory = repo.directory
        transaction.on_commit(lambda: _delete_everywhere(directory, name))


def _delete_everywhere(directory: Path, name: str) -> None:
    """`delete_photo_recursively()`, which follows no linked folder."""
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if entry.name == name and not entry.is_dir(follow_symlinks=False):
            _unlink_all([Path(entry.path)])
        elif entry.is_dir(follow_symlinks=False):
            _delete_everywhere(Path(entry.path), name)


def cache_breaker() -> int:
    return int(time.time())
