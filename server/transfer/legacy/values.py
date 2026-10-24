"""Conversions from legacy column values, which SQLite does not type-check."""

import json
from datetime import UTC, datetime


class BadValue(ValueError):
    """A legacy value that its column's conversion cannot read."""


def integer(value):
    """An integer, or None for NULL and `""`."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except TypeError, ValueError:
        raise BadValue(f"{value!r} is not a number") from None
    if not number.is_integer():
        raise BadValue(f"{value!r} is not a whole number")
    return int(number)


def integer_or_none(value):
    """An integer, or None for anything `integer()` cannot read."""
    try:
        return integer(value)
    except BadValue:
        return None


def id_order(value):
    """A sort key for a legacy id column, which can hold anything."""
    return integer_or_none(value) or 0


def number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        raise BadValue(f"{value!r} is not a number") from None


def truthy(value):
    """PHP truthiness of a column value: NULL, 0, `""` and `"0"` are false."""
    return value not in (None, 0, 0.0, "", "0", False)


def text(value):
    return "" if value is None else str(value)


def ref_id(value):
    """A legacy id that may be absent: NULL, `""`, 0 and negatives mean none."""
    id_ = integer(value)
    return id_ if id_ is not None and id_ > 0 else None


def id_list(csv):
    """The ids in a legacy CSV column such as `Classes.constituents`."""
    return [integer(part) for part in text(csv).split(",") if part.strip()]


def local_time(value, zone):
    """A legacy local-time string, such as `2024-02-10 13:05:09`, made aware.

    Returns `(moment, ambiguous)`. An autumn wall time that occurs twice reads
    as its first occurrence (fold=0), and `ambiguous` says so.
    """
    if value is None or str(value).strip() == "":
        return None, False
    try:
        naive = datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise BadValue(f"{value!r} is not a local time") from None
    if naive.tzinfo is not None:
        return naive, False
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    return first, first.utcoffset() != second.utcoffset()


def unix_time(value, *, scale=1):
    """Unix seconds (or `scale` units per second) as an aware datetime.

    0, NULL and `""` mean none.
    """
    count = integer(value)
    if not count:
        return None
    if count < 0:
        raise BadValue(f"{value!r} is a negative time")
    try:
        return datetime.fromtimestamp(count / scale, tz=UTC)
    except OverflowError, OSError, ValueError:
        raise BadValue(f"{value!r} is out of range") from None


def kiosk_page(raw):
    """Split a legacy kiosk page into `(page, params, reload_requested)`.

    PHP marks a pending reload with a leading `!` (`mark_kiosk_for_reload()`)
    and appends parameters after `#` (`parse_kiosk_params()`). Raises
    `BadValue` when the parameters are not a JSON object.
    """
    page = text(raw)
    reload = page.startswith("!")
    if reload:
        page = page[1:]
    page, _, params = page.partition("#")
    if not params:
        return page, {}, reload
    try:
        decoded = json.loads(params)
    except json.JSONDecodeError:
        raise BadValue(f"parameters {params!r} are not JSON") from None
    if not isinstance(decoded, dict):
        raise BadValue(f"parameters {params!r} are not a JSON object")
    return page, decoded, reload
