"""The import page's batch import (#49): a roster, awards or results file,
mapped to fields in the browser, imported in one transaction.

PHP's import pages post one `racer.import`, `award.import` or
`result.import` per row and keep the rows before a failing one. Here every
row of a file goes to the same per-row service those endpoints use
(`roster.services.racers.insert_new_racer`, `awards.editing.import_award`,
`racing.services.result_import.import_result`), each in its own savepoint
of one transaction under the race-state lock. A row that fails is reported
with its row number and the service's message, the other rows still run so
every error shows at once, and then the whole file rolls back: a file
imports all or nothing (D69, a deliberate difference from PHP). The legacy
endpoints keep PHP's row by row. A results file's columns are mapped by
header name in the browser, with PHP's fixed order as the fallback (D71).

`preview_roster()` reads the same rows without writing: how many racers,
the partitions that would be new (a name that matches an existing
partition with case ignored joins it, D34), car numbers the file repeats or
the event has already, and rows missing a required field.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from django.db import DatabaseError, transaction

from awards.editing import AwardFailure, import_award
from core import race_settings
from core.models import ActionLog, Event, RaceSettings
from core.services import lock_race_state
from racing.services.result_import import ImportFailure, import_result
from roster.models import Partition, Racer
from roster.services import groups, racers
from roster.services.racers import RacerFailure

# The roster fields of `import-roster.php`, with the combined name field.
ROSTER_FIELDS = (
    "lastname",
    "firstname",
    "first-last",
    "partition",
    "carnumber",
    "carname",
    "note_from",
    "exclude",
)
AWARD_FIELDS = ("awardname", "awardtype", "classname", "subgroup", "carnumber")
# `import-results.js`'s fixed columns, in order.
RESULT_FIELDS = (
    "class",
    "round",
    "heat",
    "lane",
    "firstname",
    "lastname",
    "carnumber",
    "carname",
    "finishtime",
    "finishplace",
    "scale-mph",
    "completed",
)
REQUIRED = {
    "roster": ("lastname", "firstname"),
    "awards": ("awardname", "awardtype"),
    "results": ("class", "round", "heat", "lane", "carnumber"),
}
FIELDS = {"roster": ROSTER_FIELDS, "awards": AWARD_FIELDS, "results": RESULT_FIELDS}
# The labels a roster import may rename (`import-roster.php`).
LABELS = ("supergroup-label", "partition-label")
MAX_ROWS = 5000


@dataclass(frozen=True)
class Row:
    """One row of the file: its row number as the page shows it, and its
    text by field."""

    number: int
    fields: Mapping[str, str]


@dataclass(frozen=True)
class RowError:
    number: int
    message: str


@dataclass
class Outcome:
    imported: int = 0
    errors: list[RowError] = field(default_factory=list)
    warnings: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class BadRequest(Exception):
    """The rows are not what the page sends."""


class ImportedAlready(Exception):
    """The import's token names an import that committed already."""


# The history entry a committed batch import writes, with its token. The
# history formatter gives it no sentence.
BATCH_ACTION = "import.batch"
TOKEN = re.compile(r"[0-9a-f]{32}")


def parse_rows(kind: str, data) -> list[Row]:
    """The posted rows, `[{"row": n, "fields": {name: text}}]`, keeping only
    the fields of `kind`. All-blank rows are left out, as PHP's page skips
    them (`website/js/import-csv.js:374` to `:388`)."""
    if kind not in FIELDS:
        raise BadRequest("Unknown import.")
    if not isinstance(data, list) or len(data) > MAX_ROWS:
        raise BadRequest("The rows could not be read.")
    rows = []
    for item in data:
        if not isinstance(item, Mapping) or not isinstance(item.get("fields"), Mapping):
            raise BadRequest("The rows could not be read.")
        number = item.get("row")
        if not isinstance(number, int) or isinstance(number, bool):
            raise BadRequest("The rows could not be read.")
        fields = {
            name: str(value)
            for name, value in item["fields"].items()
            if name in FIELDS[kind] and isinstance(value, str | int | float)
        }
        if any(v.strip() for v in fields.values()):
            rows.append(Row(number, fields))
    return rows


def _missing(kind: str, row: Row) -> str | None:
    fields = dict(row.fields)
    if kind == "roster" and "first-last" in fields:
        first, last = racers.split_first_last(fields["first-last"])
        fields["firstname"], fields["lastname"] = first, last
    for name in REQUIRED[kind]:
        if not fields.get(name, "").strip():
            return name
    return None


# ----- the roster ---------------------------------------------------------------


def _roster_row(event: Event, row: Row) -> list[str]:
    fields = row.fields
    if "first-last" in fields:
        first, last = racers.split_first_last(fields["first-last"])
    else:
        first, last = fields.get("firstname", ""), fields.get("lastname", "")
    new = racers.insert_new_racer(
        event,
        first_name=first,
        last_name=last,
        car_number=fields.get("carnumber", ""),
        car_name=fields.get("carname", ""),
        note=fields.get("note_from", ""),
        partition_name=fields.get("partition", "").strip(" \t\n\r\0\x0b"),
        exclude="exclude" in fields and racers.as_boolean(fields["exclude"]),
    )
    return new.warnings


def _award_row(event: Event, row: Row) -> list[str]:
    import_award(event, dict(row.fields))
    return []


def _result_row(event: Event, row: Row) -> list[str]:
    failure = import_result(event, dict(row.fields))
    if failure is not None:
        raise failure
    return []


ROW_SERVICES: dict[str, Callable[[Event, Row], list[str]]] = {
    "roster": _roster_row,
    "awards": _award_row,
    "results": _result_row,
}
FAILURES = (RacerFailure, AwardFailure, ImportFailure)


def _message(error: Exception) -> str:
    return getattr(error, "description", None) or str(error)


class _RollBack(Exception):
    pass


def import_rows(
    event: Event,
    kind: str,
    rows: Sequence[Row],
    labels: Mapping[str, str] | None = None,
    *,
    token: str,
) -> Outcome:
    """Import every row in one transaction, or none when any row fails.
    `labels` renames the supergroup and partition labels with the roster,
    in the same transaction.

    `token` names this import: 32 hex digits the page makes for each file
    and mapping. A committed import records it in the history, so the same
    request sent twice, by a double tap or a replay, raises `ImportedAlready`
    the second time and changes nothing. A refused import records nothing,
    so it can be sent again once fixed."""
    if not TOKEN.fullmatch(token or ""):
        raise BadRequest("Reload the page and import the file again.")
    service = ROW_SERVICES[kind]
    outcome = Outcome()
    try:
        with transaction.atomic():
            lock_race_state(event.id)
            if ActionLog.objects.filter(
                event=event, request__action=BATCH_ACTION, request__token=token
            ).exists():
                raise ImportedAlready

            for key, value in (labels or {}).items():
                if key in LABELS and value.strip():
                    try:
                        race_settings.write(event, key, value.strip())
                    except race_settings.NotWritten:
                        outcome.errors.append(RowError(0, f"The {key} can't be saved."))
            for row in rows:
                missing = _missing(kind, row)
                if missing is not None:
                    outcome.errors.append(
                        RowError(row.number, f"Missing required field {missing}.")
                    )
                    continue
                try:
                    with transaction.atomic():
                        warnings = service(event, row)
                except FAILURES as failure:
                    outcome.errors.append(RowError(row.number, _message(failure)))
                    continue
                except DatabaseError:
                    # Text past a column's width, which Postgres refuses.
                    outcome.errors.append(
                        RowError(row.number, "This row can't be stored.")
                    )
                    continue
                outcome.imported += 1
                outcome.warnings += [RowError(row.number, w) for w in warnings]
            if outcome.errors:
                raise _RollBack
            ActionLog.objects.create(
                event=event,
                request={
                    "action": BATCH_ACTION,
                    "kind": kind,
                    "token": token,
                    "rows": outcome.imported,
                },
            )
    except _RollBack:
        outcome.imported = 0
        outcome.warnings = []
    return outcome


# ----- the preview --------------------------------------------------------------


@dataclass
class Preview:
    rows: int = 0
    existing_partitions: list[str] = field(default_factory=list)
    new_partitions: list[str] = field(default_factory=list)
    # Car number to the row numbers that name it: a number the file
    # repeats, and apart from those, a number a racer of the event has.
    duplicate_cars: dict[str, list[int]] = field(default_factory=dict)
    taken_cars: dict[str, list[int]] = field(default_factory=dict)
    missing: list[RowError] = field(default_factory=list)


def preview(event: Event, kind: str, rows: Sequence[Row]) -> Preview:
    found = Preview(rows=len(rows))
    for row in rows:
        missing = _missing(kind, row)
        if missing is not None:
            found.missing.append(
                RowError(row.number, f"Missing required field {missing}.")
            )
    if kind != "roster":
        return found
    existing = list(Partition.objects.filter(event=event).order_by("sort_order", "id"))
    found.existing_partitions = [p.name for p in existing]
    known = {Partition.name_key(p.name) for p in existing}
    for row in rows:
        name = row.fields.get("partition", "").strip(" \t\n\r\0\x0b")
        name = name or groups.DEFAULT_PARTITION_NAME
        key = Partition.name_key(name)
        if key not in known:
            known.add(key)
            found.new_partitions.append(name)
    taken = {
        str(n)
        for n in Racer.objects.filter(event=event).values_list("car_number", flat=True)
    }
    by_car: dict[str, list[int]] = {}
    for row in rows:
        number = row.fields.get("carnumber", "").strip()
        if number:
            by_car.setdefault(number, []).append(row.number)
    found.duplicate_cars = {
        car: numbers for car, numbers in by_car.items() if len(numbers) > 1
    }
    found.taken_cars = {
        car: numbers
        for car, numbers in by_car.items()
        if car in taken and len(numbers) == 1
    }
    return found


def current_labels(event: Event) -> dict[str, str]:
    settings = RaceSettings.objects.get(event=event)
    return {
        "supergroup_label": settings.supergroup_label,
        "partition_label": settings.partition_label,
    }
