"""The setup page's data tools, each behind an automatic snapshot (#48, D78).

Before a purge, a Re-Initialize or a snapshot import, the event is written
as a complete `snapshot.get` file to
`<event dir>/snapshots/before-<what>-<YYYYmmdd-HHMMSS>.xml`. Every one is
kept. The snapshot is written first, so a change that fails leaves a file and
no harm; a snapshot that cannot be written stops the change.

The file is what `action.php?query=snapshot.get` answers, so `snapshot.put`
reads it back. Like every snapshot it leaves out what D65 lists: videos, lane
closures, allowed racers, the timer audit, racer notes, check-in times and
`ntrophies`.
"""

from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from core.models import Event
from core.services import event_dir, lock_race_state, reset_event
from legacy_api.xml import PROLOG
from racing.services import purge as purges
from transfer import snapshot

PURGES = ("results", "schedules", "racers", "awards")
REINITIALIZE = "reinitialize"
IMPORT = "import"


class BackupFailed(Exception):
    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


def snapshot_folder(event: Event) -> Path:
    return event_dir(event.slug) / "snapshots"


def take_snapshot(event: Event, what: str) -> Path:
    """Write a complete snapshot of `event` before `what` (D78)."""
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    folder = snapshot_folder(event)
    path = folder / f"before-{what}-{stamp}.xml"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"before-{what}-{stamp}-{n}.xml"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(PROLOG + snapshot.get(event), encoding="utf-8")
    except OSError as error:
        raise BackupFailed(
            "snapshot-failed", f"The snapshot before the change failed: {error}"
        ) from None
    return path


def snapshots(event: Event) -> list[Path]:
    """The automatic snapshots of the event, newest first."""
    try:
        found = [p for p in snapshot_folder(event).glob("before-*.xml") if p.is_file()]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def find_snapshot(name: str) -> Path | None:
    """The automatic snapshot called `name` of any event, or None for any
    other name. A snapshot import replaces the open event, so the snapshot
    taken before it belongs to the event it replaced."""
    for event in Event.objects.all():
        found = next((p for p in snapshots(event) if p.name == name), None)
        if found is not None:
            return found
    return None


def purge(event: Event, what: str) -> Path:
    """`database.purge` of one mode, or Re-Initialize (`database.execute`'s
    `schema` script), after a snapshot. Returns the snapshot.

    The snapshot is read under the `RaceState` lock the change then holds,
    in one transaction, so no write lands between the two (#48 review). The
    file is on disk, so it survives a change that rolls back."""
    if what not in (*PURGES, REINITIALIZE):
        raise BackupFailed("not-recognized", f"Unrecognized purge operation: {what}")
    with transaction.atomic():
        lock_race_state(event.pk)
        taken = take_snapshot(event, what)
        if what == REINITIALIZE:
            reset_event(event)
        else:
            purges.purge(event, what)
    return taken


@dataclass(frozen=True)
class Imported:
    snapshot: Path
    tables: list[tuple[str, str, int, int]]


def import_snapshot(event: Event, content: bytes) -> Imported:
    """`snapshot.put` of an uploaded file, after a snapshot of the event it
    replaces. Raises `snapshot.PutFailure` for a file it refuses, which
    changes nothing. The snapshot is taken inside the import's own
    transaction, under the replaced event's lock (`snapshot.put`'s
    `before`)."""
    taken: list[Path] = []

    def before():
        lock_race_state(event.pk)
        taken.append(take_snapshot(event, IMPORT))

    tables = snapshot.put(event, content, before=before)
    return Imported(taken[0], tables)
