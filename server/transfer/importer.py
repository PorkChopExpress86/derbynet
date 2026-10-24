"""Import one legacy DerbyNet event as a new `Event` (#14).

`import_derbynet()` reads a legacy SQLite file or snapshot, brings its rows to
schema 10, and writes them in one transaction: the event, every table, the
`RaceInfo` settings and state, and a `LegacyId` row per imported row. It then
runs `check_invariants()`. A violation rolls everything back unless the
caller accepts violations. Photo files are staged outside the event's folder
and moved in only after the commit.
"""

import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction

from core.invariants import check_invariants
from core.models import Event, SiteState, event_scoped_models
from core.services import new_event

from .legacy.photos import CAR_PHOTOS, HEADSHOTS, Photos, publish
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
):
    """Import the legacy database at `path` and return the `Report`.

    Legacy local times read in `time_zone`, by default `TIME_ZONE`. Raises
    `SourceError` for an unreadable file, and `ImportFailed` when the event
    name is taken or the imported rows violate an invariant.
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

    events_root = Path(settings.DATA_DIR) / "events"
    events_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=events_root))
    try:
        photos = Photos(
            report,
            staging,
            source_path=path,
            legacy_dirs=legacy_folders(tables["raceinfo"]),
            search_dirs={HEADSHOTS: photos_dir, CAR_PHOTOS: car_photos_dir},
        )
        photos.add_snapshot_photos(source.photos)
        restore = None
        try:
            with transaction.atomic():
                event = _create_event(name, replace, report)
                ctx = Context(event, tables, report, zone, photos)
                legacy_ids = load_tables(ctx)
                apply_raceinfo(ctx)
                LegacyId.objects.bulk_create(
                    LegacyId(event=event, model=m, legacy_id=legacy, new_id=new)
                    for m, legacy, new in legacy_ids
                )
                report.photos = {
                    "paths": photos.referenced,
                    "files_copied": photos.copied,
                }
                report.invariants = check_invariants(event)
                if report.invariants and not accept_violations:
                    raise ImportFailed(
                        "the imported rows violate invariants: "
                        + ", ".join(sorted(report.invariants)),
                        report,
                    )
                # Last, so a failure here rolls the rows back too.
                restore = publish(staging, event.slug)
        except OSError as error:
            if restore is not None:
                restore()
            raise ImportFailed(f"cannot write the photos: {error}", report) from None
        except BaseException:
            if restore is not None:
                restore()
            raise
        report.outcome = "committed"
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


def _create_event(name, replace, report):
    """A new event named `name`, replacing the existing one if asked to."""
    existing = list(Event.objects.select_for_update().filter(name=name))
    if existing and not replace:
        raise ImportFailed(
            f"an event named {name!r} exists; pass --replace to replace it", report
        )
    if len(existing) > 1:
        raise ImportFailed(f"{len(existing)} events are named {name!r}", report)
    old = existing[0] if existing else None
    if old is not None:
        # Frees the slug, so the new event gets the old one's folder name.
        Event.objects.filter(pk=old.pk).update(slug=f"{old.slug}-replaced-{old.pk}")
        delete_event_rows(old)
    event = new_event(name)
    if old is not None:
        SiteState.objects.filter(active_event=old).update(active_event=event)
        old.delete()
    report.event = {"id": event.id, "name": event.name, "slug": event.slug}
    return event


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
