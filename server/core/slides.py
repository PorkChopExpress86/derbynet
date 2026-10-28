"""The slides folder of an event, as far as the shell suites need it (#63).

`test.setup` seeds slides for `testing/test-slide-next.sh`. Serving and
uploading slides is #35's.
"""

import os
import shutil
import tempfile
from pathlib import Path

from core.models import Event
from core.services import event_dir

EMBLEM = Path(__file__).parent / "static" / "core" / "emblem.png"
# website/inc/path-info.inc, is_acceptable_subdir().
NAME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789!@#$%-_.ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


class InvalidName(ValueError):
    pass


def is_acceptable_name(name: str) -> bool:
    """A folder or file name with no separator. PHP accepts the empty string,
    which names nothing, so it is refused here."""
    return (
        name not in ("", ".", "..")
        and len(name) <= 255
        and all(c in NAME_CHARACTERS for c in name)
    )


def slides_dir(event: Event, subdir: str | None = None) -> Path:
    """The event's slides folder, or a folder under it. Raises `InvalidName`
    for a name that is not one folder."""
    root = event_dir(event.slug) / "slides"
    if subdir is None:
        return root
    if not is_acceptable_name(subdir):
        raise InvalidName(subdir)
    return root / subdir


def install_test_slide(folder: Path, slide: str) -> None:
    """Copy the emblem to `folder/slide`. Raises `InvalidName` for a bad name.

    The copy replaces whatever is at the path, a link included, instead of
    writing through it.
    """
    if not is_acceptable_name(slide):
        raise InvalidName(slide)
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as target, EMBLEM.open("rb") as source:
            shutil.copyfileobj(source, target)
        os.replace(temporary, folder / slide)
    except BaseException:
        os.unlink(temporary)
        raise
