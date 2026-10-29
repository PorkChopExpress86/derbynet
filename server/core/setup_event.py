"""Starting or opening an event from the setup page, and the folders the setup
page browses (`setup.nodata`, `file.*`) (#36).

PHP's `ez-new` makes a folder with a database in it, and writes its
configuration files. The database here is the server's, so a new event is rows
and the same folder layout under the data folder. `ez-old` opens an event the
server already holds.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction

from accounts.models import Role
from core.models import Event, SiteState
from core.services import event_dir, new_event

SUBFOLDERS = ("racers", "cars", "videos", "logs", "imagery", "slides")


@dataclass(frozen=True)
class SetupFailure(Exception):
    code: str
    description: str


def _valid_name(name: str) -> bool:
    return (
        bool(name.strip())
        and not any(c in name for c in "/\\\0")
        and name
        not in (
            ".",
            "..",
        )
    )


def start_event(name: str) -> Event:
    """`ez-new`: an event with its folders, made the active one."""
    if not _valid_name(name):
        raise SetupFailure("mkdir", f"{name}:  not a usable folder name")
    with transaction.atomic():
        if Event.objects.filter(name=name).exists():
            raise SetupFailure("already_exists", "Directory already exists.")
        event = new_event(name)
        root = event_dir(event.slug)
        for sub in SUBFOLDERS:
            try:
                (root / sub).mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise SetupFailure(f"mkdir-{sub}", f"{root / sub}: {error}") from None
        SiteState.objects.update_or_create(pk=1, defaults={"active_event": event})
    return event


def open_existing(name: str) -> Event:
    """`ez-old`: make an event the server holds the active one."""
    event = (
        Event.objects.filter(name=name).first()
        or Event.objects.filter(slug=name).first()
    )
    if event is None:
        raise SetupFailure(
            "not-recognized", f"{name} is not a recognized configuration."
        )
    SiteState.objects.update_or_create(pk=1, defaults={"active_event": event})
    return event


def post_setup_role() -> Role | None:
    """The role the setup page logs in as: the one marked for it, else the
    race coordinator."""
    return (
        Role.objects.filter(post_setup=True).first()
        or Role.objects.filter(name="RaceCoordinator").first()
    )


# ----- the folders the setup page browses -----


def data_root() -> Path:
    return Path(os.path.realpath(settings.DATA_DIR))


def inside(path: str) -> Path | None:
    """The real path, if it is the data folder or under it."""
    real = Path(os.path.realpath(path))
    root = data_root()
    return real if real == root or root in real.parents else None


def listing(path: str) -> dict:
    """`query=file.directory.nodata` for a path under the data folder. A
    file lists its folder and names itself as `chosen`; a path that is not a
    folder lists the nearest one above it."""
    out: dict = {"original": path}
    root = data_root()
    real = inside(path)
    failures: list[tuple[str, str]] = []
    if real is None:
        failures.append(("outside", f"{path} is outside the data folder"))
        real = root
    if real.is_file():
        out["chosen"] = real.name
        real = real.parent
    while real != root and not real.is_dir():
        failures.append(("unable", f"Unable to list directory {real}"))
        real = real.parent
    entries = []
    try:
        names = sorted(os.listdir(real))
    except OSError:
        names = []
        failures.append(("unable", f"Unable to list directory {real}"))
    for name in names:
        if name.startswith("."):
            continue
        target = real / name
        entries.append(
            {
                "file": name,
                "readable": os.access(target, os.R_OK),
                "writable": os.access(target, os.W_OK),
                "isdir": target.is_dir(),
            }
        )
    out["path"] = str(real).rstrip("/") + "/"
    base_paths = []
    walk = real
    while True:
        base_paths.append({"path": str(walk).rstrip("/") + "/", "basename": walk.name})
        if walk == root:
            break
        walk = walk.parent
    out["base-paths"] = base_paths
    out["files"] = entries
    out["failures"] = failures
    return out


def stat(path: str) -> dict | None:
    """`query=file.stat`, or None for a path outside the data folder."""
    real = inside(path)
    if real is None:
        return None
    return {
        "file": path,
        "readable": os.access(real, os.R_OK),
        "writable": os.access(real, os.W_OK),
        "isdir": real.is_dir(),
    }


def make_folder(path: str) -> bool:
    """`file.directory.new.nodata`: one new folder under the data folder."""
    real = inside(os.path.dirname(os.path.abspath(path)))
    if real is None:
        return False
    try:
        Path(real, os.path.basename(path.rstrip("/"))).mkdir()
    except OSError:
        return False
    return True
