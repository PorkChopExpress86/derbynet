"""The photo, replay video and slideshow endpoints (#35): `photo.upload`,
`photo.assign`, `photo.crop`, `photo.rotate`, `photo.delete`, `video.upload`
and `query=slide.next`. The work is in `media.photos`, `media.videos` and
`media.slides`.
"""

import math
import re

from accounts.permissions import Access, Perm
from core.slides import is_acceptable_name
from legacy_api.registry import LegacyContext, action, query
from media import photos, slides, videos
from racing.engine.phpcompat import php_numeric, php_truthy

UNKNOWN_RACER = ("R1: an unknown racer id fails with noracer; PHP answers success.",)
STORAGE = (
    "P1: renders, crops, autocrop and rotation apply the EXIF orientation first,"
    " and renders carry no orientation tag (#8, #73).",
    "P2: only the allowed render sizes are made; any other size gets the nearest"
    " allowed render, never a new folder.",
)
UPLOAD_DIVERGENCES = (
    "C27: filename-change names the stored file; PHP's is always null.",
    *UNKNOWN_RACER,
    *STORAGE,
    "P3: the file is decoded to check it is a JPEG, PNG or GIF, its name must end"
    " in .jpg, .jpeg, .jpe, .png or .gif, and its size is capped; PHP trusts the"
    " client's MIME type.",
    "P4: autocrop runs OpenCV and warns of a miss, where PHP's detector returns a"
    " box of zeros (#74), and runs the DERBYNET_FACEDETECT hook with no shell.",
)
REPO_DIVERGENCE = ("P5: an unknown repo fails with unknown-repo; PHP fails fatally.",)


def _text(ctx: LegacyContext, name: str) -> str | None:
    value = ctx.args.get(name)
    return value if isinstance(value, str) else None


def _repository(ctx: LegacyContext):
    name = _text(ctx, "repo")
    repo = photos.repository(ctx.event, "head" if name is None else name)
    if repo is None:
        ctx.failure("unknown-repo", f"No photo repository named {name}.")
    return repo


def _selector(ctx: LegacyContext) -> photos.Selector:
    """PHP's order: `racerid`, then `carnumber`, then `barcode`."""
    for key, kind in (
        ("racerid", photos.ByRacerId),
        ("carnumber", photos.ByCarNumber),
        ("barcode", photos.ByBarcode),
    ):
        if key in ctx.args:
            return kind(_text(ctx, key) or "")
    return None


@action(
    "photo.upload",
    permission=Perm.PHOTO_UPLOAD,
    divergences=UPLOAD_DIVERGENCES + REPO_DIVERGENCE,
)
def photo_upload(ctx: LegacyContext) -> None:
    repo = _repository(ctx)
    if repo is None:
        return
    try:
        result = photos.upload_photo(
            ctx.event,
            repo,
            ctx.files.get("photo"),
            max_file_size=_text(ctx, "MAX_FILE_SIZE"),
            autocrop_requested=php_truthy(ctx.args.get("autocrop", "")),
            selector=_selector(ctx),
        )
    except photos.Refused as refused:
        ctx.failure(refused.code, refused.description)
        return
    if result.filename_change is not None:
        ctx.put("filename-change", result.filename_change)
    if result.cropped:
        ctx.put("cropped", True)
    if result.warning is not None:
        ctx.put("warning", result.warning)
    if result.failure is not None:
        ctx.failure(*result.failure)
    if result.photo_url is not None:
        ctx.put("photo-url", result.photo_url)
    if result.uploaded is not None:
        ctx.put("uploaded", result.uploaded)
        ctx.put("thumbnail", result.thumbnail)
        ctx.success()


@action(
    "photo.assign",
    permission=Perm.ASSIGN_RACER_IMAGE,
    divergences=UNKNOWN_RACER + REPO_DIVERGENCE,
)
def photo_assign(ctx: LegacyContext) -> None:
    repo = _repository(ctx)
    if repo is None:
        return
    try:
        photos.assign_photo(
            ctx.event, repo, _text(ctx, "racer") or "", _text(ctx, "photo") or ""
        )
    except photos.Refused as refused:
        ctx.failure(refused.code, refused.description)
        return
    ctx.success()


CROP_FIELDS = ("left", "top", "right", "bottom", "original_width", "original_height")


@action(
    "photo.crop",
    permission=Perm.ASSIGN_RACER_IMAGE,
    divergences=(
        "S19: needs ASSIGN_RACER_IMAGE; PHP has no check.",
        "P1: the crop box is in the upright, turned frame of the work render (#8).",
        "P6: a missing photo, a missing or zero size, or an empty box fails;"
        " PHP fails fatally.",
        *REPO_DIVERGENCE,
    ),
)
def photo_crop(ctx: LegacyContext) -> None:
    repo = _repository(ctx)
    if repo is None:
        return
    values = [php_numeric(_text(ctx, name) or "") for name in CROP_FIELDS]
    if any(value is None or not math.isfinite(value) for value in values):
        ctx.failure("crop", "The crop needs " + ", ".join(CROP_FIELDS) + ".")
        return
    try:
        photos.crop_photo(
            ctx.event, repo, _text(ctx, "image_name") or "", photos.CropBox(*values)
        )
    except photos.Refused as refused:
        ctx.failure(refused.code, refused.description)
        return
    ctx.success()
    ctx.put("cache-breaker", photos.cache_breaker())


@action(
    "photo.rotate",
    permission=Perm.ASSIGN_RACER_IMAGE,
    divergences=(
        "P1: the rotation is stored and the upload kept; renders apply it after the"
        " EXIF orientation (#8).",
        "P6: a missing photo or a rotation that is not a number fails; PHP fails"
        " fatally.",
        *REPO_DIVERGENCE,
    ),
)
def photo_rotate(ctx: LegacyContext) -> None:
    repo = _repository(ctx)
    if repo is None:
        return
    degrees = php_numeric(_text(ctx, "rotation") or "")
    if degrees is None or not math.isfinite(degrees):
        ctx.failure("rotation", "The rotation must be a number of degrees.")
        return
    try:
        photos.rotate_photo(ctx.event, repo, _text(ctx, "image_name") or "", degrees)
    except photos.Refused as refused:
        ctx.failure(refused.code, refused.description)
        return
    ctx.success()
    ctx.put("cache-breaker", photos.cache_breaker())


@action(
    "photo.delete",
    permission=Perm.ASSIGN_RACER_IMAGE,
    divergences=(
        "C17: a file that can't be removed is skipped; PHP's error path calls"
        " json_failure() with one argument and fails fatally.",
        *REPO_DIVERGENCE,
    ),
)
def photo_delete(ctx: LegacyContext) -> None:
    repo = _repository(ctx)
    if repo is None:
        return
    photos.delete_photo(ctx.event, repo, _text(ctx, "photo"))
    ctx.success()


@action(
    "video.upload",
    permission=Perm.CONTROL_RACE,
    divergences=(
        "S20: needs CONTROL_RACE, takes only .mkv and .webm, and caps the size;"
        " PHP takes any file from anyone.",
        "D16: the replay login is CONTROL_RACE; the Photo role cannot upload video.",
    ),
)
def video_upload(ctx: LegacyContext) -> None:
    try:
        videos.upload_video(
            ctx.event, ctx.files.get("video"), _text(ctx, "MAX_FILE_SIZE")
        )
    except photos.Refused as refused:
        ctx.failure(refused.code, refused.description)
        return
    ctx.success()


_NO_RACER_AFTER = 2**63 - 1
_INTEGER = re.compile(r"[+-]?[0-9]+")


@query(
    "slide.next",
    permission=Access.ANONYMOUS,
    divergences=(
        "S10: classids must be a comma-separated list of integers, or the query"
        " fails; PHP pastes it into its SQL.",
        "P7: a racer whose photo column is empty, not NULL, counts as having no"
        " photo of that kind.",
    ),
)
def slide_next(ctx: LegacyContext) -> None:
    class_ids = None
    if "classids" in ctx.args:
        try:
            class_ids = slides.parse_class_ids(ctx.args["classids"])
        except slides.BadClassIds:
            ctx.failure("classids", "classids must be class ids separated by commas.")
            return
    subdir = _text(ctx, "subdir") or ""
    racer_id = (_text(ctx, "racerid") or "0").strip()
    request = slides.SlideRequest(
        mode=_text(ctx, "mode") or "title",
        subdir=subdir if is_acceptable_name(subdir) else "",
        # SQLite compares a text id that is no number above every racer id.
        racer_id=int(racer_id) if _INTEGER.fullmatch(racer_id) else _NO_RACER_AFTER,
        file=_text(ctx, "file") or "",
        class_ids=class_ids,
    )
    for key, value in slides.next_slide(ctx.event, request).items():
        ctx.put(key, value)
