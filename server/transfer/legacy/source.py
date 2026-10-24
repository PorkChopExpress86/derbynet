"""Reading a legacy DerbyNet database: a SQLite file or a `snapshot.get` file.

Both become a `Source`: every table as a list of row dicts, with table and
column names in lower case, as PHP reads them through `PDO::CASE_LOWER`.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\0"


class SourceError(Exception):
    """The file is neither a readable DerbyNet database nor a snapshot."""


@dataclass(frozen=True)
class SnapshotPhoto:
    """One photo that `snapshot.get&photos` carried for a racer.

    `column` is `imagefile` or `carphoto`. `original` is the uncropped photo,
    present only when the racer showed a cropped one.
    """

    racerid: int
    column: str
    basename: str
    displayed: bytes
    original: bytes | None


@dataclass(frozen=True)
class Source:
    path: Path
    kind: str  # "sqlite" or "snapshot"
    tables: dict[str, list[dict]]
    photos: list[SnapshotPhoto] = field(default_factory=list)

    @property
    def version(self):
        """The legacy schema version: the `schema` key, or 1 without one."""
        rows = sorted(
            (
                row
                for row in self.tables.get("raceinfo", [])
                if row.get("itemkey") == "schema"
            ),
            key=lambda row: _int_or_zero(row.get("raceinfoid")),
        )
        try:
            return int(rows[-1]["itemvalue"]) if rows else 1
        except TypeError, ValueError:
            raise SourceError(
                f"unreadable schema version {rows[-1]['itemvalue']!r}"
            ) from None


def _int_or_zero(value):
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def read_source(path):
    path = Path(path)
    try:
        with path.open("rb") as f:
            head = f.read(len(SQLITE_MAGIC))
    except OSError as error:
        raise SourceError(f"cannot read {path}: {error.strerror}") from None
    if head == SQLITE_MAGIC:
        return Source(path, "sqlite", _read_sqlite(path))
    tables, photos = _read_snapshot(path)
    return Source(path, "snapshot", tables, photos)


def _read_sqlite(path):
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise SourceError(f"cannot open {path}: {error}") from None
    # Legacy text is not always UTF-8, for example names typed on Windows.
    connection.text_factory = lambda raw: raw.decode("utf-8", "replace")
    try:
        names = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite_%'"
            )
        ]
        tables = {}
        for name in names:
            cursor = connection.execute(f'SELECT * FROM "{name}"')
            columns = [column[0].lower() for column in cursor.description]
            tables[name.lower()] = [
                dict(zip(columns, row, strict=True)) for row in cursor
            ]
        return tables
    except sqlite3.Error as error:
        raise SourceError(f"cannot read {path}: {error}") from None
    finally:
        connection.close()


def _strip_bookends(text, start, stop):
    """As `remove_bookends()` in `action.snapshot.put.inc`."""
    begin, end = text.find(start), text.rfind(stop)
    if begin == -1 or end == -1:
        return text
    return text[begin + len(start) : end]


def _read_snapshot(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    text = _strip_bookends(text, "<snapshot>", "</snapshot>")
    text = _strip_bookends(text, "<![CDATA[", "]]>")
    try:
        entries = json.loads(text)
    except json.JSONDecodeError as error:
        raise SourceError(
            f"{path} is neither a SQLite database nor a snapshot: {error}"
        ) from None
    if not isinstance(entries, list):
        raise SourceError(f"{path} is not a snapshot: the JSON is not a list")
    tables, photos = {}, []
    for entry in entries:
        if not isinstance(entry, dict):
            raise SourceError(f"{path} has a snapshot entry that is not an object")
        if "table" in entry:
            tables[str(entry["table"]).lower()] = [
                {str(column).lower(): value for column, value in row.items()}
                for row in entry.get("rows", [])
            ]
        elif "racerid" in entry:
            photos += _snapshot_photos(path, entry)
    return tables, photos


def _snapshot_photos(path, entry):
    photos = []
    for column in ("imagefile", "carphoto"):
        photo = entry.get(column)
        if not photo:
            continue
        try:
            photos.append(
                SnapshotPhoto(
                    racerid=int(entry["racerid"]),
                    column=column,
                    basename=str(photo["basename"]),
                    displayed=bytes.fromhex(photo.get("displayed", "")),
                    original=(
                        bytes.fromhex(photo["original"])
                        if "original" in photo
                        else None
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SourceError(
                f"{path} has an unreadable {column} for racer {entry.get('racerid')!r}:"
                f" {error}"
            ) from None
    return photos
