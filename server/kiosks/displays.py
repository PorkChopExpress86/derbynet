"""What the kiosk display pages read (#52): each page's parameters, typed,
and the lists the server renders.

A page's parameters come from the kiosk's row or, in preview, from the URL,
which anyone can write. `read_parameters()` keeps only the names the page's
registry entry lists and only values of the right type, so a forged value is
dropped, never used: `please-check-in` read `classids` straight into SQL in
PHP (S12).
"""

from dataclasses import dataclass

from kiosks import pages, parameters
from roster.models import Racer


def _ids(value) -> tuple[int, ...]:
    """A list of whole numbers. Any other item drops the list, so
    `["1) OR 1=1 --"]` filters nothing (S12)."""
    if not isinstance(value, list):
        return ()
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return ()
    return tuple(value)


def _text(value) -> str:
    return value[: parameters.TEXT_LIMIT] if isinstance(value, str) else ""


_READERS = {
    "classids": _ids,
    "title": _text,
    "subdir": _text,
    "content": _text,
    "key": _text,
    # The award kiosk's confetti is on unless set false, as
    # award-presentations-kiosk.js defaults it.
    "confetti": lambda value: value is not False,
}


def read_parameters(page: pages.KioskPage, text: str) -> dict:
    """The page's parameters from their stored text, each of the type the
    page reads. A missing name takes its reader's empty value."""
    stored = parameters.stored(text)
    return {name: _READERS[name](stored.get(name)) for name in page.parameters}


@dataclass(frozen=True)
class Waiting:
    group: str
    names: tuple[str, ...]


def to_check_in(event, group_ids: tuple[int, ...] = ()) -> list[Waiting]:
    """The racers who have not passed inspection, by group in the groups'
    order, then by last and first name, as `please-check-in.kiosk` lists
    them. `group_ids` narrows the list; empty lists every group. Sorted
    here, so Postgres collation plays no part."""
    racers = Racer.objects.filter(event=event, passed_inspection=False).select_related(
        "group"
    )
    if group_ids:
        racers = racers.filter(group_id__in=group_ids)
    by_group: dict[tuple, list[Racer]] = {}
    for racer in racers:
        group = racer.group
        key = (group.sort_order, group.name, group.pk)
        by_group.setdefault(key, []).append(racer)
    return [
        Waiting(
            key[1],
            tuple(
                f"{r.last_name}, {r.first_name}"
                for r in sorted(found, key=lambda r: (r.last_name, r.first_name))
            ),
        )
        for key, found in sorted(by_group.items())
    ]


LANE_COLORS = ("blue", "green", "orange", "pink", "purple", "red", "yellow")


def lane_colors(settings) -> list[str]:
    """Each lane's color from `lane-colors`, lane 1 first, `none` for a lane
    with no color or a color `css/lane-colors.css` has no class for."""
    named = (
        [c.strip() for c in settings.lane_colors.split(",")]
        if settings.lane_colors
        else []
    )
    named += ["none"] * (settings.lane_count - len(named))
    return [c if c in LANE_COLORS else "none" for c in named[: settings.lane_count]]


def scaled_track(settings) -> dict:
    """`scale-MPH.kiosk`'s figures: the track scaled up 25x, in feet, and
    the scale speed of a 3.0 s run, each as PHP's `%4.1f` prints them."""
    length = settings.track_length
    return {
        "length": length,
        "feet": f"{13 * 12 / 7 * length:4.1f}",
        "mph": f"{length / 3.0 * 3600 * 25 / 5280:4.1f}",
    }
