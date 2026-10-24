"""Legacy `RaceInfo` rows into the event's settings and state rows.

`write_raceinfo()` is not atomic, so a database can hold a key twice (#64
C30). The row with the highest `raceinfoid` wins, as the last write would
have. Each key then goes through the registry in `core.legacy_keys`.
"""

import re

from django.db import connection, models

from core.legacy_keys import Dropped, LegacyValueError, Ref, decode, entry_for
from core.models import FullFieldRule, RaceSettings, RaceState
from kiosks.models import ReplayCommand, ReplayStatus
from racing.models import TimerStatus

from . import values
from .photos import CAR_PHOTOS, HEADSHOTS
from .report import Kind
from .values import BadValue

TARGETS = {
    "core.RaceSettings": RaceSettings,
    "core.RaceState": RaceState,
    "racing.TimerStatus": TimerStatus,
    "kiosks.ReplayStatus": ReplayStatus,
}

# Fields of packed keys that hold a legacy id, and the model they name.
PACKED_REFS = {
    "current_round": "racing.round",
    "full_field_group": "roster.group",
    "staged_round": "racing.round",
}

# The event's own folders replace these, from `new_event()`.
FOLDER_KEYS = {
    "photo-directory",
    "car-photo-directory",
    "video-directory",
    "logs-directory",
}

TIMER_TEST_RESULT = re.compile(r"tt-(lane|place)(\d+)")


def latest_values(rows, report=None):
    """`{key: value}`, keeping the highest `raceinfoid` of a repeated key."""
    latest = {}
    for row in sorted(rows, key=lambda r: values.id_order(r["raceinfoid"])):
        key = values.text(row["itemkey"])
        if key in latest and report is not None:
            report.add(
                Kind.DUPLICATE_KEY,
                "raceinfo",
                latest[key]["raceinfoid"],
                f"{key} appears again as raceinfoid {row['raceinfoid']}; the later"
                f" value {values.text(row['itemvalue'])!r} wins",
            )
        latest[key] = row
    return {key: values.text(row["itemvalue"]) for key, row in latest.items()}


def legacy_folders(rows):
    """The legacy photo directory of each repository, for rebasing paths."""
    found = latest_values(rows)
    headshots = found.get(HEADSHOTS.setting) or None
    # `car_photo_directory()` falls back to the photo directory.
    return {
        HEADSHOTS: headshots,
        CAR_PHOTOS: found.get(CAR_PHOTOS.setting) or headshots,
    }


def _convert_obsolete_keys(found, report):
    """Two keys PHP converts when it first reads them, converted once here."""
    if values.truthy(found.get("drop-slowest")) and "scoring" not in found:
        # standings.inc writes scoring=1 on every include (#64 C11).
        found["scoring"] = "1"
        report.add(
            Kind.CHANGED_VALUE,
            "raceinfo",
            "drop-slowest",
            "drop-slowest is set; scoring=1",
        )
    if not values.truthy(found.get("photos-on-now-racing")):
        # read_photos_on_now_racing() in photos-on-now-racing.inc.
        derived = "head" if values.truthy(found.get("show-racer-photos")) else "0"
        if found.get("photos-on-now-racing") != derived:
            report.add(
                Kind.CHANGED_VALUE,
                "raceinfo",
                "photos-on-now-racing",
                f"unset; show-racer-photos {found.get('show-racer-photos')!r}"
                f" makes it {derived!r}",
            )
        found["photos-on-now-racing"] = derived


CONVERTED = {"drop-slowest", "show-racer-photos", "schema"}


def apply_raceinfo(ctx):
    found = latest_values(ctx.tables["raceinfo"], ctx.report)
    _convert_obsolete_keys(found, ctx.report)
    rows = {name: model.for_event(ctx.event) for name, model in TARGETS.items()}
    replay_commands = []
    for key, raw in found.items():
        if key in FOLDER_KEYS:
            ctx.report.add(
                Kind.DROPPED_KEY,
                "raceinfo",
                key,
                f"{raw!r} is replaced by the event's own folder",
            )
            continue
        try:
            entry = entry_for(key)
        except KeyError:
            ctx.report.add(
                Kind.UNKNOWN_KEY, "raceinfo", key, f"value {raw!r} not imported"
            )
            continue
        if isinstance(entry, Dropped):
            if key not in CONVERTED:
                ctx.report.add(Kind.DROPPED_KEY, "raceinfo", key, entry.reason)
            continue
        try:
            value = decode(key, raw)
        except LegacyValueError as error:
            ctx.report.add(
                Kind.BAD_VALUE,
                "raceinfo",
                error.key,
                f"cannot read {error.raw!r}; the default stays",
            )
            continue
        if entry.model == "kiosks.ReplayCommand":
            replay_commands = value
            continue
        row = rows[entry.model]
        match = TIMER_TEST_RESULT.fullmatch(key)
        if match:
            kind, lane = match.groups()
            if value is not None:
                row.test_results.setdefault(lane, {})[
                    "time" if kind == "lane" else "place"
                ] = value
            continue
        fields = (
            dict(zip(entry.fields, [value], strict=True))
            if len(entry.fields) == 1
            else value
        )
        try:
            _assign(ctx, row, key, fields, entry)
        except BadValue as error:
            ctx.report.add(
                Kind.BAD_VALUE, "raceinfo", key, f"{error}; the default stays"
            )

    settings = rows["core.RaceSettings"]
    if (
        settings.full_field_rule == FullFieldRule.GROUP
        and settings.full_field_group_id is None
    ):
        settings.full_field_rule = FullFieldRule.STANDARD
        ctx.report.add(
            Kind.CHANGED_VALUE,
            "raceinfo",
            "full-field-calc",
            "names no imported group; the standard rule applies",
        )
    for row in rows.values():
        row.save()
    ReplayCommand.objects.bulk_create(
        ReplayCommand(event=ctx.event, command=command) for command in replay_commands
    )
    ctx.report.count("raceinfo", len(ctx.tables["raceinfo"]), len(found))


def _assign(ctx, row, key, fields, entry):
    """Set the decoded fields, mapping legacy ids. All or none of them."""
    resolved = {}
    for name, value in fields.items():
        field = row._meta.get_field(name)
        target = (
            entry.codec.model.lower()
            if isinstance(entry.codec, Ref)
            else PACKED_REFS.get(name)
        )
        if name == "last_heat" and value is not None:
            round_id = ctx.ids["racing.round"].get(value["round_id"])
            if round_id is None:
                _unmapped(ctx, key, value["round_id"])
                value = None
            else:
                value = {**value, "round_id": round_id}
        elif target is not None and value is not None:
            new = ctx.ids[target].get(value)
            if new is None:
                _unmapped(ctx, key, value)
            value = new
        _check_range(field, value)
        resolved[field.attname] = value
    for attname, value in resolved.items():
        setattr(row, attname, value)


def _unmapped(ctx, key, legacy_id):
    ctx.report.add(
        Kind.CHANGED_VALUE,
        "raceinfo",
        key,
        f"id {legacy_id} names no imported row; cleared",
    )


def _check_range(field, value):
    if value is None or field.is_relation:
        return
    if isinstance(field, models.CharField) and len(value) > field.max_length:
        raise BadValue(f"{value!r} is longer than {field.max_length}")
    if isinstance(field, models.IntegerField):
        low, high = connection.ops.integer_field_range(field.get_internal_type())
        if (low is not None and value < low) or (high is not None and value > high):
            raise BadValue(f"{value} is out of range for {field.name}")
