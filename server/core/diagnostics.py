"""What the setup wizard and the about page report about the server (#48).

PHP's `about.php` and `build_setup_page_details()`
(`website/inc/details-for-setup-page.inc`) check the database, the photo
folders, the upload limit and the configuration. Django has no
`config-database.inc`: its configuration is the environment and the data
folder, so those are what is checked (D80). Nothing here writes a setting.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor

from core import race_settings
from core.models import Event, RaceSettings
from core.setup_event import inside

# The photo stand posts MAX_FILE_SIZE=30000000 (`photo-stand` in `extras/`,
# and `PHOTO_UPLOAD_MAX_BYTES` in `derbynet/settings.py`).
PHOTO_STAND_BYTES = 30_000_000


class Status:
    OK = "ok"
    READONLY = "readonly"
    MISSING = "missing"
    OUTSIDE = "outside"
    TROUBLE = "trouble"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: str
    detail: str


@dataclass(frozen=True)
class TimeZone:
    name: str
    # The environment variable it came from, or "" when neither is set and
    # the server runs on UTC.
    source: str


def time_zone() -> TimeZone:
    """The zone the server runs in and where it came from (D56). Read at
    startup from `DERBYNET_TIME_ZONE`, then `TZ`, else UTC
    (`derbynet/settings.py`); no setting stores it."""
    if os.environ.get("DERBYNET_TIME_ZONE"):
        source = "DERBYNET_TIME_ZONE"
    elif os.environ.get("TZ") == settings.TIME_ZONE:
        source = "TZ"
    else:
        source = ""
    return TimeZone(settings.TIME_ZONE, source)


def pending_migrations() -> list[str]:
    """The migrations not yet applied, as `app.name`. The server applies them
    when it starts (`manage.py migrate` in the image), so a list here means
    it started on an older database without migrating."""
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{m.app_label}.{m.name}" for m, _ in plan]


def folder_status(path: str) -> str:
    """A folder setting's badge: ok, read-only, missing, or outside the data
    folder, the one root a folder may be under (S25)."""
    if not path:
        return Status.MISSING
    real = inside(path)
    if real is None:
        return Status.OUTSIDE
    if not real.is_dir() or not os.access(real, os.R_OK):
        return Status.MISSING
    if not os.access(real, os.W_OK):
        return Status.READONLY
    return Status.OK


def database_location() -> str:
    """The engine and database name, for SET_UP callers only (`about.php`
    shows `config-database.inc` only with SET_UP)."""
    name = connection.settings_dict["NAME"]
    if connection.vendor == "sqlite":
        return f"SQLite: {name}"
    host = connection.settings_dict.get("HOST") or "localhost"
    return f"{connection.vendor.title()}: {name} on {host}"


def database_writable() -> bool:
    """Whether the database takes writes, found without writing, since a
    page GET writes nothing (`ui/tests/test_every_view.py`). SQLite needs a
    writable file and folder (its journal goes beside it); Postgres refuses
    writes on a read-only replica or a read-only session."""
    if connection.vendor == "sqlite":
        name = Path(str(connection.settings_dict["NAME"]))
        if name.exists() and not os.access(name, os.W_OK):
            return False
        return os.access(name.parent, os.W_OK)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_is_in_recovery(), current_setting('transaction_read_only')"
            )
            recovery, read_only = cursor.fetchone()
    except DatabaseError:
        return False
    return not recovery and read_only == "off"


def _database_check() -> Check:
    if database_writable():
        return Check("database", "Database", Status.OK, "The database takes writes.")
    return Check(
        "database",
        "Database",
        Status.TROUBLE,
        "The database refuses writes. Check that the data volume is mounted"
        " read-write and that the server's user owns it.",
    )


def _folder_check(key: str, label: str, path: str) -> Check:
    status = folder_status(path)
    detail = {
        Status.OK: f"{path} exists and takes new files.",
        Status.READONLY: f"{path} exists, but the server cannot write to it.",
        Status.MISSING: f"{path or 'No folder is set'} does not exist.",
        Status.OUTSIDE: f"{path} is outside the data folder {settings.DATA_DIR}.",
    }[status]
    shown = Status.TROUBLE if status in (Status.MISSING, Status.OUTSIDE) else status
    return Check(key, label, shown, detail)


def _megabytes(n: int) -> str:
    return f"{round(n / 1_000_000, 1):g} MB"


def upload_limit() -> Check:
    """Django's photo upload cap against the 30 MB the photo stand sends.
    The proxy in front of Django (nginx in the image from #58, whose
    `MAX_UPLOAD_SIZE` sets it) has its own `client_max_body_size`, which the
    server cannot read, so the page says to check it.
    `DATA_UPLOAD_MAX_MEMORY_SIZE` caps only the form fields beside a file
    (Django streams the file itself), so it is shown, not compared."""
    cap = settings.PHOTO_UPLOAD_MAX_BYTES
    fields = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
    detail = (
        f"Photos up to {_megabytes(cap)} are accepted (DERBYNET_PHOTO_UPLOAD_MAX_MB);"
        f" the photo stand sends up to {_megabytes(PHOTO_STAND_BYTES)}."
        f" Form fields beside a file are capped at {_megabytes(fields or 0)}."
        " A proxy in front of the server must allow at least as much"
        " (nginx client_max_body_size)."
    )
    status = Status.OK if cap >= PHOTO_STAND_BYTES else Status.TROUBLE
    return Check("upload", "Upload size limit", status, detail)


def _config_check() -> Check:
    """PHP explains a missing or unwritable `local/` folder
    (`diagnose_configuration()`). Django keeps its data and its secret key
    in `DERBYNET_DATA_DIR` and `DERBYNET_CONFIG_DIR`."""
    bad = [
        f"{name} ({path})"
        for name, path in (
            ("DERBYNET_DATA_DIR", settings.DATA_DIR),
            ("DERBYNET_CONFIG_DIR", settings.CONFIG_DIR),
        )
        if not (Path(path).is_dir() and os.access(path, os.W_OK))
    ]
    if not bad:
        return Check(
            "config",
            "Configuration",
            Status.OK,
            f"Data in {settings.DATA_DIR}, configuration in {settings.CONFIG_DIR}.",
        )
    return Check(
        "config",
        "Configuration",
        Status.TROUBLE,
        "The server cannot write to " + " and ".join(bad) + ". Set the variable"
        " to a folder the server's user owns, or mount a writable volume there.",
    )


def health_checks(event: Event | None) -> list[Check]:
    """The checks of `build_setup_page_details()`, for the wizard and the
    about page: the database takes writes, each photo folder exists and is
    writable, the upload limit, and the configuration folders."""
    checks = [_database_check(), _config_check()]
    if event is not None:
        row = RaceSettings.for_event(event)
        checks += [
            _folder_check("photo-dir", "Racer photos folder", row.photo_directory),
            _folder_check(
                "car-photo-dir", "Car photos folder", row.car_photo_directory
            ),
            _folder_check("video-dir", "Videos folder", row.video_directory),
        ]
    checks.append(upload_limit())
    return checks


@dataclass(frozen=True)
class Timer:
    type: str
    human: str
    ident: str


def timer(event: Event) -> Timer:
    """The timer the server last heard from. PHP's `about.php` reads
    `timer-humant`, a typo, so the timer's name never shows (C35); this
    reads `timer-human`."""
    values = race_settings.read_many(
        event, ("timer-type", "timer-human", "timer-ident")
    )
    return Timer(
        values["timer-type"] or "",
        values["timer-human"] or "",
        values["timer-ident"] or "",
    )
