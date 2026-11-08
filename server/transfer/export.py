"""`manage.py export_event <slug>` and the events page's download: one event
as a zip that `import_derbynet` reads back (D14, D81; #9, Archive).

The zip is a legacy event folder: `derbynet.sqlite3`, a standalone SQLite
file at schema 10, beside the event's whole media folders, `racers/`,
`cars/`, `videos/`, `logs/`, `imagery/` and `slides/`, unassigned photos
included. A photo's path in `RegistrationInfo` is relative to its folder,
so the importer finds it in the zip's `racers/` or `cars/`.

The database holds `snapshot.get`'s tables and columns, and also what the
importer reads from a PHP database and a snapshot leaves out: each racer's
`note` and `checkin_time`, the classes' and ranks' `ntrophies`, and the
`Kiosks` table (`_beyond_the_snapshot`). A round trip still loses, as D65
records for a snapshot round trip:

- lane closures and each round's allowed racers, which the legacy schema
  has no column for;
- the timer audit and the action log's Django-only rows;
- the fraction of a second of each result's completion time, which
  `RaceChart.completed` keeps to the second (`snapshot._stamp`);
- the event's folders' paths, which the importer replaces with the new
  event's own (`legacy.folders`).
"""

import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from django.db import transaction

from core.models import Event, RaceSettings
from core.services import event_dir, lock_race_state
from kiosks import pages
from kiosks.models import Kiosk
from roster.models import Group, Racer, Subgroup
from transfer import snapshot
from transfer.legacy import embedded

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


def _ms(moment) -> int | None:
    return None if moment is None else int(moment.timestamp() * 1000)


def _seconds(moment) -> int | None:
    return None if moment is None else int(moment.timestamp())


def _beyond_the_snapshot(event: Event, tables: dict, ids: snapshot.Numbering) -> dict:
    """`snapshot.tables` plus the columns and the table the importer reads
    from a PHP database (`transfer.legacy.tables`) and a snapshot omits. The
    snapshot numbers each table's rows in primary key order, so the rows
    here line up with its rows, and a kiosk's ids are its numbers (`ids`)."""

    def add(name, columns, values):
        old_columns, rows = tables[name]
        values = list(values)
        if len(values) != len(rows):
            raise ValueError(f"{name}: the rows changed under the export's lock")
        tables[name] = (
            [*old_columns, *columns],
            [[*row, *more] for row, more in zip(rows, values, strict=True)],
        )

    mine = {"event": event}
    add(
        "RegistrationInfo",
        ["note", "checkin_time"],
        (
            [racer.note, _ms(racer.checked_in_at)]
            for racer in Racer.objects.filter(**mine).order_by("pk")
        ),
    )
    add(
        "Classes",
        ["ntrophies"],
        ([g.n_trophies] for g in Group.objects.filter(**mine).order_by("pk")),
    )
    add(
        "Ranks",
        ["ntrophies"],
        ([r.n_trophies] for r in Subgroup.objects.filter(**mine).order_by("pk")),
    )
    tables["Kiosks"] = (
        ["address", "name", "page", "last_contact"],
        [
            [
                k.address,
                k.name,
                pages.legacy_string(
                    k.page, embedded.kiosk_params(k.page, k.params, ids.lookup).text
                ),
                _seconds(k.last_contact),
            ]
            for k in Kiosk.objects.filter(**mine).order_by("pk")
        ],
    )
    return tables


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
        ids = snapshot.Numbering()
        tables = _beyond_the_snapshot(event, snapshot.tables(event, ids=ids), ids)
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
