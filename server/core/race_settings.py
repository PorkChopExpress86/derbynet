"""Read and write the settings and live state by their legacy `RaceInfo` key
names, for the `settings.*` endpoints (#63).

The legacy text of a key comes from the codec in `core.legacy_keys`, so a
value reads back the way PHP stored it. A key with no single stored value
(the replay queue, the timer test lanes) is not listed and reads as None.
"""

from django.apps import apps

from core.legacy_keys import (
    REGISTRY,
    Dropped,
    LegacyValueError,
    Mapped,
    Ref,
    decode,
    encode,
    entry_for,
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


class NotWritten(Exception):
    """The value cannot be stored, as PHP's read-back finds a truncated one."""


def _entry(key):
    """The `Mapped` entry of a key held in one of the per-event rows, or None."""
    try:
        entry = entry_for(key)
    except KeyError:
        return None
    if isinstance(entry, Dropped) or entry.model not in ROW_MODELS or not entry.fields:
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
    """Store legacy text. Raises `NotWritten` for text the key cannot hold."""
    entry = _entry(key)
    if entry is None:
        raise NotWritten(key)
    try:
        value = decode(key, text)
    except LegacyValueError as error:
        raise NotWritten(key) from error
    fields = {entry.fields[0]: value} if len(entry.fields) == 1 else value
    row = ROW_MODELS[entry.model].for_event(event)
    for name, field_value in fields.items():
        field = row._meta.get_field(name)
        if isinstance(entry.codec, Ref) and field_value is not None:
            target = apps.get_model(entry.codec.model)
            if not target.objects.filter(event=event, pk=field_value).exists():
                raise NotWritten(key)
        setattr(row, field.attname, field_value)
    row.save(update_fields=[row._meta.get_field(n).attname for n in fields])
    if read(event, key) != text:
        raise NotWritten(key)
