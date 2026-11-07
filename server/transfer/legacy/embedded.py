"""Ids that text holds (#231): a kiosk page's parameters and the current
award key.

PHP writes these ids raw and its snapshot keeps every row's id
(`action.snapshot.put.inc:49-78`), so they keep naming their rows. Django
numbers a snapshot's rows from 1 and gives an import new ids (D57), so the
snapshot writes these ids as its own numbers and the importer maps them
back, as it maps every id column.

Each function takes `lookup(model label, id)`, which gives the id the row
has on the other side, or None for a row there is none of. An id that
names no row is dropped (D83): from `classids`, which becomes `[0]` when no
id is left, so the kiosk still shows nobody (please-check-in.kiosk:101-102,
`NO_CLASS`); from a standings
`key` or the award key, which become `""`; and with the `classid` or
`rankid` parameter that holds it. Each drop comes back as a sentence for
the import report.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, fields, replace

from awards.listing import award_key as judged_key
from awards.listing import parse_award_key
from kiosks import pages, parameters
from racing.engine.standings.oracle import parse_standings_key
from racing.engine.standings.speed_awards import parse_speed_key

Lookup = Callable[[str, int], int | None]

GROUP = "roster.group"
SUBGROUP = "roster.subgroup"
ROUND = "racing.round"
AWARD = "awards.award"

# The model each id field of a parsed key names.
_FIELD_MODELS = {"group_id": GROUP, "subgroup_id": SUBGROUP, "round_id": ROUND}

# Parameters PHP's standings kiosk reads that Django's does not
# (website/js/standings-kiosk.js:127-132), mapped so a trip back to PHP
# keeps them.
_PHP_ONLY = {parameters.STANDINGS: ("classid", "rankid")}


# The `classids` of a kiosk whose classes are all gone (D83): no class has
# id 0, so please-check-in.kiosk:101-102 and slide.next list nobody, where
# an empty list would list every class.
NO_CLASS = 0


@dataclass(frozen=True)
class Mapped:
    text: str
    # What was dropped, one sentence each, for the import report.
    dropped: tuple[str, ...] = ()


def _whole_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _map_fields(key, lookup: Lookup):
    """`key` with each id field mapped, or None when one names no row."""
    mapped = {}
    for field in fields(key):
        value = getattr(key, field.name)
        if field.name in _FIELD_MODELS and value is not None:
            value = lookup(_FIELD_MODELS[field.name], value)
            if value is None:
                return None
            mapped[field.name] = value
    return replace(key, **mapped)


def award_key(key: str, lookup: Lookup) -> Mapped:
    """`current_award_key` with its award, group and subgroup ids mapped."""
    if key == "":
        return Mapped(key)
    award = parse_award_key(key)
    if award is not None:
        new = lookup(AWARD, award)
        if new is None:
            return Mapped("", (f"{key!r} names no award; cleared",))
        return Mapped(judged_key(new))
    speed = parse_speed_key(key)
    if speed is None:
        return Mapped("", (f"{key!r} is not an award key; cleared",))
    new = _map_fields(speed, lookup)
    if new is None:
        return Mapped("", (f"{key!r} names no group or subgroup; cleared",))
    return Mapped(new.text)


def _classids(value, lookup: Lookup, dropped: list[str]):
    if not isinstance(value, list):
        return value
    kept = []
    for item in value:
        if item == NO_CLASS and _whole_number(item):
            continue
        new = lookup(GROUP, item) if _whole_number(item) else None
        if new is None:
            dropped.append(f"classids {item!r} names no class; dropped")
        else:
            kept.append(new)
    if value and not kept:
        # please-check-in.kiosk:101-102 filters only on a list with ids, so
        # an empty list would show every class (D83).
        if dropped:
            dropped.append("no class is left, so the kiosk shows nobody")
        return [NO_CLASS]
    return kept


def _standings_key(value, lookup: Lookup, dropped: list[str]):
    if not isinstance(value, str):
        return value
    parsed = parse_standings_key(value)
    new = None if parsed is None else _map_fields(parsed, lookup)
    if new is None:
        dropped.append(f"key {value!r} names no standings; cleared")
        return ""
    return new.text


def _single(model: str):
    def read(value, lookup: Lookup, dropped: list[str]):
        new = lookup(model, value) if _whole_number(value) else None
        if new is None:
            dropped.append(f"{model.split('.')[1]} {value!r} names no row; dropped")
        return new

    return read


_READERS = {
    "classids": _classids,
    "key": _standings_key,
    "classid": _single(GROUP),
    "rankid": _single(SUBGROUP),
}


def kiosk_params(page: str, params: str, lookup: Lookup) -> Mapped:
    """A kiosk page's parameters text with each id it holds mapped. Text
    whose page takes no id, or holds none, comes back as it is."""
    names = [
        name
        for name in (*pages.page_for(page).parameters, *_PHP_ONLY.get(page, ()))
        if name in _READERS
    ]
    stored = parameters.stored(params)
    present = [name for name in names if name in stored]
    if not present:
        return Mapped(params)
    dropped: list[str] = []
    for name in present:
        new = _READERS[name](stored[name], lookup, dropped)
        if new is None:
            del stored[name]
        else:
            stored[name] = new
    text = json.dumps(stored, separators=(",", ":"), ensure_ascii=False)
    return Mapped(text, tuple(dropped))
