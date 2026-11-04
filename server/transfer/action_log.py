"""The action log of an imported event, with its legacy ids resolved (#49).

`import_derbynet` copies `ActionHistory` requests verbatim, so an imported
row names racers, groups, subgroups, partitions, rounds and awards by their
legacy ids, while the event's rows got new ids (#12) and every action logged
after the import names new ids. The Retrospective page reads the log through
`resolved_log()`, which turns each legacy id of an imported row into the new
id `transfer.LegacyId` maps it to. A legacy id with no map entry, such as a
racer deleted before the export, becomes an `Unmapped` marker: the history
formatter keeps the rows the log creates for it apart from the new ids, and
shows the raw id with the marker where it has no name, never another row's.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from core.models import ActionLog
from transfer.models import LegacyId

# The request keys that hold an id, by the model of the row they name.
ID_KEYS = {
    "racerid": "roster.racer",
    "racer": "roster.racer",
    "classid": "roster.group",
    # `partition.move` names the racing group it moves a partition to.
    "group_id": "roster.group",
    "rankid": "roster.subgroup",
    "partitionid": "roster.partition",
    # `partition.move` names the partition it moves.
    "div_id": "roster.partition",
    "roundid": "racing.round",
    "awardid": "awards.award",
}
# `partition.*structure` lists every partition with its ids.
NESTED_KEYS = ("partitions",)
ACTION_LOG = "core.actionlog"


@dataclass(frozen=True)
class Unmapped:
    """A legacy id of an imported row that names no row of the event."""

    model: str
    legacy_id: int

    def __str__(self) -> str:
        return f"{self.legacy_id} (old id, not imported)"


def _legacy_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _resolve(value, model: str, maps: Mapping[str, Mapping[int, int]]):
    legacy = _legacy_int(value)
    if legacy is None:
        return value
    new = maps.get(model, {}).get(legacy)
    if new is None:
        return Unmapped(model, legacy)
    return str(new) if isinstance(value, str) else new


class Resolved(dict):
    """A resolved request. `original` is the request as logged, which the
    history shows where it prints a request whole (`rank.edit`)."""

    original: Mapping


def resolve_request(request: Mapping, maps: Mapping[str, Mapping[int, int]]) -> dict:
    """An imported request with each id key resolved through `maps`,
    `{model label: {legacy id: new id}}`."""
    out = Resolved()
    out.original = request
    for key, value in request.items():
        if key in ID_KEYS:
            out[key] = _resolve(value, ID_KEYS[key], maps)
        elif key in NESTED_KEYS and isinstance(value, list):
            out[key] = [
                resolve_request(item, maps) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            out[key] = value
    return out


def resolved_log(event) -> Iterator[tuple[object, dict]]:
    """`(received, request)` for each row of the event's action log, oldest
    first, with an imported row's legacy ids resolved."""
    maps: dict[str, dict[int, int]] = {}
    for model, legacy, new in LegacyId.objects.filter(event=event).values_list(
        "model", "legacy_id", "new_id"
    ):
        maps.setdefault(model, {})[legacy] = new
    imported = set(maps.get(ACTION_LOG, {}).values())
    rows = (
        ActionLog.objects.filter(event=event)
        .order_by("received", "pk")
        .values_list("pk", "received", "request")
    )
    for pk, received, request in rows.iterator():
        if pk in imported and isinstance(request, Mapping):
            request = resolve_request(request, maps)
        yield received, request
