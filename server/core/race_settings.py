"""Read and write the settings and live state by their legacy `RaceInfo` key
names, for the `settings.*` endpoints (#63).

The legacy text of a key comes from the codec in `core.legacy_keys`, so a
value reads back the way PHP stored it. A key with no single stored value
(the replay queue, the timer test lanes) is not listed and reads as None.
"""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction

from core.legacy_keys import (
    REGISTRY,
    LegacyValueError,
    Mapped,
    Ref,
    decode,
    encode,
)
from core.models import RaceSettings, RaceState
from kiosks.models import ReplayStatus
from racing.models import TimerStatus

ROW_MODELS = {
    "core.RaceSettings": RaceSettings,
    "core.RaceState": RaceState,
    "racing.TimerStatus": TimerStatus,
    "kiosks.ReplayStatus": ReplayStatus,
}


# SQLite keeps any integer in these columns, and Postgres refuses one past the
# column. The same range applies on both.
INTEGER_RANGES = {
    "SmallIntegerField": (-(2**15), 2**15 - 1),
    "PositiveSmallIntegerField": (0, 2**15 - 1),
    "IntegerField": (-(2**31), 2**31 - 1),
    "PositiveIntegerField": (0, 2**31 - 1),
}


class NotWritten(Exception):
    """The value cannot be stored, as PHP's read-back finds a truncated one."""


def _entry(key):
    """The `Mapped` entry of a key held in one of the per-event rows, or None.

    A key matched by a prefix pattern (`tt-lane3`) is one slot of a stored
    dict, and `replay_queue` is a table, so neither has a single value to read.
    """
    entry = REGISTRY.get(key)
    if not isinstance(entry, Mapped) or entry.model not in ROW_MODELS:
        return None
    return entry


def _stored(entry, row):
    values = {name: getattr(row, _attname(row, name)) for name in entry.fields}
    return values[entry.fields[0]] if len(entry.fields) == 1 else values


def _attname(row, name):
    return row._meta.get_field(name).attname


def read(event, key) -> str | None:
    """The legacy text of `key`, or None for a key that is not stored."""
    entry = _entry(key)
    if entry is None:
        return None
    row = ROW_MODELS[entry.model].for_event(event)
    return encode(key, _stored(entry, row))


def read_many(event, keys) -> dict[str, str | None]:
    return {key: read(event, key) for key in keys}


def all_keys(event) -> dict[str, str]:
    """Every stored key, as `SELECT * FROM RaceInfo` lists them."""
    return {
        key: read(event, key)
        for key, entry in REGISTRY.items()
        if isinstance(entry, Mapped) and _entry(key) is not None
    }


def write(event, key, text: str) -> None:
    """Store legacy text. Raises `NotWritten` for text the key cannot hold.

    Where PHP reads the text back to catch a truncated value, this reads the
    typed value back, since the text of a typed field is normalized.
    """
    entry = _entry(key)
    if entry is None:
        raise NotWritten(key)
    try:
        value = decode(key, text)
    except LegacyValueError as error:
        raise NotWritten(key) from error
    fields = {entry.fields[0]: value} if len(entry.fields) == 1 else value
    try:
        with transaction.atomic():
            row = ROW_MODELS[entry.model].for_event(event)
            for name, field_value in fields.items():
                _check(event, entry, row._meta.get_field(name), field_value, key)
                setattr(row, _attname(row, name), field_value)
            names = [_attname(row, name) for name in fields]
            row.save(update_fields=names)
            row.refresh_from_db(fields=names)
            if any(getattr(row, _attname(row, n)) != v for n, v in fields.items()):
                raise NotWritten(key)
    except (IntegrityError, DataError) as error:
        raise NotWritten(key) from error


def _check(event, entry, field, value, key) -> None:
    if value is None:
        return
    if isinstance(entry.codec, Ref):
        target = apps.get_model(entry.codec.model)
        if not target.objects.filter(event=event, pk=value).exists():
            raise NotWritten(key)
        return
    low, high = INTEGER_RANGES.get(field.get_internal_type(), (None, None))
    if low is not None and not low <= value <= high:
        raise NotWritten(key)
    try:
        field.run_validators(value)
    except ValidationError as error:
        raise NotWritten(key) from error
