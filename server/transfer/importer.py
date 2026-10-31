"""Import one legacy DerbyNet event as a new `Event` (#14).

`import_derbynet()` reads a legacy SQLite file or snapshot, brings its rows to
schema 10, and writes them in one transaction: the event, every table, the
`RaceInfo` settings and state, and a `LegacyId` row per imported row. It then
runs `check_invariants()`. A violation rolls everything back unless the
caller accepts violations. The event's files go into a new folder, which the
commit switches the event to (`legacy.folders`).
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction

from core.invariants import check_invariants
from core.models import Event, RaceSettings, SiteState, event_scoped_models
from core.services import new_event

from .legacy.folders import FOLDERS, ImportFolder, recover
from .legacy.photos import CAR_PHOTOS, HEADSHOTS, Photos
from .legacy.raceinfo import apply_raceinfo, legacy_folders
from .legacy.report import Report
from .legacy.source import read_source
from .legacy.tables import Context, load_tables
from .legacy.upgrade import upgrade
from .models import LegacyId

LATEST_SCHEMA = 10


class ImportFailed(Exception):
    """The import wrote nothing. `report` says what it found, when it got far."""

    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


def import_derbynet(
    path,
    *,
    event_name=None,
    photos_dir=None,
    car_photos_dir=None,
    replace=False,
    accept_violations=False,
    time_zone=None,
    discard_videos=False,
    keep_folders=False,
):
    """Import the legacy database at `path` and return the `Report`.

    Legacy local times read in `time_zone`, by default `TIME_ZONE`. Raises
    `SourceError` for an unreadable file, and `ImportFailed` when the event
    name is taken or the imported rows violate an invariant. A replaced
    event's videos and logs cannot be recovered from the source, so
    `replace` refuses to remove any unless `discard_videos` is set. With
    `keep_folders`, the new event takes the replaced event's folder paths,
    which then hold the new event's files (`legacy.folders`).
    """
    path = Path(path)
    time_zone = time_zone or settings.TIME_ZONE
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ImportFailed(f"unknown time zone {time_zone!r}: {error}") from None
    source = read_source(path)
    version = source.version
    report = Report(
        source={
            "path": str(path),
            "kind": source.kind,
            "version": version,
            "tables": sorted(source.tables),
            "time_zone": time_zone,
        }
    )
    if not 1 <= version <= LATEST_SCHEMA:
        raise ImportFailed(
            f"schema version {version} is not 1 to {LATEST_SCHEMA}", report
        )
    name = event_name or path.resolve().parent.name
    tables = upgrade(source.tables, version)
    recordings = _recordings_to_discard(name, replace, discard_videos, report)

    events_root = Path(settings.DATA_DIR) / "events"
    report.recovered = [str(path) for path in recover(events_root)]
    folder = ImportFolder(events_root, name)
    try:
        photos = Photos(
            report,
            folder.path,
            source_path=path,
            legacy_dirs=legacy_folders(tables["raceinfo"]),
            search_dirs={HEADSHOTS: photos_dir, CAR_PHOTOS: car_photos_dir},
        )
        photos.add_snapshot_photos(source.photos)
        # durable: the commit at the end of this block is the switch from the
        # old event's folders to the new one.
        with transaction.atomic(durable=True):
            event, replaced = _create_event(name, replace, report)
            ctx = Context(event, tables, report, zone, photos)
            legacy_ids = load_tables(ctx)
            apply_raceinfo(ctx)
            folder.point(event)
            LegacyId.objects.bulk_create(
                LegacyId(event=event, model=m, legacy_id=legacy, new_id=new)
                for m, legacy, new in legacy_ids
            )
            report.photos = {
                "folder": str(folder.path),
                "paths": photos.referenced,
                "files_copied": photos.copied,
            }
            if recordings is not None:
                report.photos["discarded_recordings"] = recordings
            report.invariants = check_invariants(event)
            if report.invariants and not accept_violations:
                raise ImportFailed(
                    "the imported rows violate invariants: "
                    + ", ".join(sorted(report.invariants)),
                    report,
                )
            folder.note_replaced(replaced, keep=keep_folders)
    except OSError as error:
        folder.discard()
        raise ImportFailed(f"cannot write the photos: {error}", report) from None
    except BaseException:
        folder.discard()
        raise
    report.photos["replaced_folders_removed"] = [str(p) for p in folder.finish()]
    report.outcome = "committed"
    return report


def _recordings_to_discard(name, replace, discard_videos, report):
    """The file count and size of the videos and logs that replacing `name`
    removes, or None when no event is replaced. Refuses when there are files
    and `discard_videos` is false, before anything is written.
    """
    old = Event.objects.filter(name=name).first() if replace else None
    if old is None:
        return None
    old_settings = RaceSettings.for_event(old)
    files = size = 0
    problems = []
    for directory in (old_settings.video_directory, old_settings.logs_directory):
        if not os.path.isdir(directory):
            continue
        for root, _, names in os.walk(
            directory, onerror=lambda e: problems.append((e.filename, e.strerror))
        ):
            # rmtree needs write and search access on every folder it empties.
            if not os.access(root, os.W_OK | os.X_OK):
                problems.append((root, "not writable"))
            for file_name in names:
                path = Path(root, file_name)
                try:
                    if path.is_symlink():
                        continue
                    size += path.stat().st_size
                except OSError as e:
                    problems.append((str(path), e.strerror))
                    continue
                files += 1
    if problems:
        where, why = problems[0]
        raise ImportFailed(
            f"cannot count or delete the videos and logs of {name!r}: {where}"
            f" cannot be read or deleted ({why}). Fix its permissions and run"
            " again. --discard-videos does not skip this check, because the"
            " size cannot be reported and the delete would fail after the"
            " commit",
            report,
        )
    if files and not discard_videos:
        raise ImportFailed(
            f"replacing {name!r} would delete {files} video and log files"
            f" ({size} bytes) that the legacy source cannot restore; pass"
            " --discard-videos to delete them",
            report,
        )
    return {"files": files, "bytes": size}


def _create_event(name, replace, report):
    """A new event named `name`, replacing the existing one if asked to.

    Also returns the replaced event's directories, by `FOLDERS` field.
    """
    existing = list(Event.objects.select_for_update().filter(name=name))
    if existing and not replace:
        raise ImportFailed(
            f"an event named {name!r} exists; pass --replace to replace it", report
        )
    if len(existing) > 1:
        raise ImportFailed(f"{len(existing)} events are named {name!r}", report)
    old = existing[0] if existing else None
    replaced = {}
    if old is not None:
        old_settings = RaceSettings.for_event(old)
        replaced = {field: getattr(old_settings, field) for field in FOLDERS}
        # Frees the slug, so the new event gets the old one's folder name.
        Event.objects.filter(pk=old.pk).update(slug=f"{old.slug}-replaced-{old.pk}")
        delete_event_rows(old)
    event = new_event(name)
    if old is not None:
        SiteState.objects.filter(active_event=old).update(active_event=event)
        old.delete()
    report.event = {"id": event.id, "name": event.name, "slug": event.slug}
    return event, replaced


def delete_event_rows(event):
    """Delete every event-scoped row of `event`, referencing models first.

    Most foreign keys are `PROTECT`, so a model's rows go only after every
    model that points at it.
    """
    remaining = set(event_scoped_models())
    while remaining:
        ready = [
            model
            for model in remaining
            if not any(
                other is not model and _points_at(other, model) for other in remaining
            )
        ]
        if not ready:
            raise RuntimeError(f"foreign keys form a cycle among {remaining}")
        for model in sorted(ready, key=lambda m: m._meta.label_lower):
            model.objects.filter(event=event).delete()
            remaining.discard(model)


def _points_at(model, target):
    return any(
        field.is_relation and field.name != "event" and field.related_model is target
        for field in model._meta.concrete_fields
    )
