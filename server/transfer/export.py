"""`manage.py export_event <slug>` and the events page's download: one event
as a zip that `import_derbynet` reads back (#9, Archive; #212).

The zip is a legacy event folder: `derbynet.sqlite3`, a standalone SQLite
file at schema 10 with `snapshot.get`'s tables and columns, beside the
event's media folders, `racers/`, `cars/`, `videos/`, `logs/`, `imagery/`
and `slides/`. A photo's path in `RegistrationInfo` is relative to its
folder, so the importer finds it in the zip's `racers/` or `cars/`. The
database keeps what a snapshot keeps: it loses `ntrophies`, `note`,
`checkin_time`, the fraction of a second of each result's time, and
what the legacy schema has no column for (`transfer.snapshot`).
"""

import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from django.db import transaction

from core.models import Event, RaceSettings
from core.services import event_dir, lock_race_state
from transfer import snapshot

DATABASE = "derbynet.sqlite3"
# The zip's folder for each RaceSettings directory.
SETTINGS_FOLDERS = {
    "photo_directory": "racers",
    "car_photo_directory": "cars",
    "video_directory": "videos",
    "logs_directory": "logs",
}
# Folders of the event's own directory (#9, Media layout).
EVENT_FOLDERS = ("imagery", "slides")
MEDIA_FOLDERS = (*SETTINGS_FOLDERS.values(), *EVENT_FOLDERS)


def _sql_value(value):
    return int(value) if isinstance(value, bool) else value


def write_database(path: Path, tables: dict[str, tuple[list[str], list[list]]]):
    """The legacy tables as a SQLite file. Every column is untyped, as the
    importer reads values by name."""
    connection = sqlite3.connect(path)
    try:
        with connection:
            for name, (columns, rows) in tables.items():
                quoted = ", ".join(f'"{c}"' for c in columns)
                connection.execute(f'CREATE TABLE "{name}" ({quoted})')
                marks = ", ".join("?" for _ in columns)
                connection.executemany(
                    f'INSERT INTO "{name}" ({quoted}) VALUES ({marks})',
                    ([_sql_value(v) for v in row] for row in rows),
                )
    finally:
        connection.close()


def _media(event: Event) -> dict[str, Path]:
    """The zip folder name to the event's folder on disk."""
    row = RaceSettings.objects.get(event=event)
    found = {
        folder: Path(getattr(row, field))
        for field, folder in SETTINGS_FOLDERS.items()
        if getattr(row, field)
    }
    if found.get("cars") == found.get("racers"):
        # PHP keeps car photos with the headshots when both name one folder.
        found.pop("cars", None)
    root = event_dir(event.slug)
    found.update({folder: root / folder for folder in EVENT_FOLDERS})
    return found


def _files(folder: Path):
    """The regular files under `folder`, reached through no symbolic link."""
    if not folder.is_dir() or folder.is_symlink():
        return
    for parent, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not (Path(parent) / d).is_symlink())
        for name in sorted(names):
            path = Path(parent) / name
            if path.is_file() and not path.is_symlink():
                yield path


def export_event(event: Event, target: Path) -> Path:
    """Write the event's zip to `target`. The tables are read under the
    event's `RaceState` lock, so they are one consistent state; a
    read-only event exports too. The zip is written beside `target` and
    renamed over it, so a failed export leaves no partial file."""
    target = Path(target)
    with transaction.atomic():
        lock_race_state(event.pk, writable=False)
        tables = snapshot.tables(event)
        media = _media(event)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="derbynet-export-") as work:
        database = Path(work) / DATABASE
        write_database(database, tables)
        partial = Path(
            tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)[1]
        )
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as out:
                out.write(database, DATABASE)
                for name in MEDIA_FOLDERS:
                    out.writestr(f"{name}/", "")
                    folder = media.get(name)
                    for path in _files(folder) if folder else ():
                        out.write(path, f"{name}/{path.relative_to(folder)}")
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return target


def file_name(event: Event) -> str:
    return f"{event.slug}.zip"
