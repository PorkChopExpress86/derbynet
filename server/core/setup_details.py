"""The status `database.execute` reports to the setup page: `details` (#63).

The keys are those of PHP's `build_setup_page_details()`, one per step of the
page. The text is trimmed to what the Django server can say. #48 rebuilds the
page and may reshape it.
"""

import os
import re
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Count, Q

from awards.models import Award
from core.models import Event, RaceSettings, RaceState
from racing.models import HeatLane
from roster.models import Group, Racer


def plural(word: str) -> str:
    """The rules of `website/inc/plural.inc` that a group label needs."""
    rules = [
        (r"(ss|sh|ch|x|z)$", r"\1es"),
        (r"([^aeiou])o$", r"\1oes"),
        (r"([^aeiou]|qu)y$", r"\1ies"),
        (r"([a-rt-z])$", r"\1s"),
    ]
    for pattern, replacement in rules:
        if re.search(pattern, word, re.IGNORECASE):
            return re.sub(pattern, replacement, word, flags=re.IGNORECASE)
    return word


def _count_phrase(n: int, single: str, many: str) -> str:
    if n == 0:
        return f"are no {many}"
    return f"is one {single}" if n == 1 else f"are {n} {many}"


def _connection_text() -> str:
    name = connection.settings_dict["NAME"]
    if connection.vendor == "sqlite":
        return f"SQLITE: {name}"
    return f"{connection.vendor.upper()}: {name}"


def _settings_step(row: RaceSettings, locked: bool) -> dict:
    lanes = row.lane_count
    unset = "The number of lanes on the track hasn't been set."
    lanes_text = (
        f"<br/><span class='trouble'>{unset}</span>"
        if lanes == 0
        else f"<br/>The track has {lanes} lanes."
    )
    if locked:
        return {"status": "unknown" if lanes == 0 else "ok", "details": lanes_text}
    folders = [Path(row.photo_directory), Path(row.car_photo_directory)]
    details = (
        f"Racer photos: {row.photo_directory}<br/>\n"
        f"Car photos: {row.car_photo_directory}"
    )
    if not all(f.is_dir() for f in folders):
        status = "trouble"
    elif not all(_writable(f) for f in folders):
        status = "readonly"
    else:
        status = "unknown" if lanes == 0 else "ok"
        details += lanes_text
    return {"status": status, "details": details}


def _writable(folder: Path) -> bool:
    return os.access(folder, os.W_OK)


def build(event: Event) -> dict:
    row = RaceSettings.for_event(event)
    state = RaceState.for_event(event)
    heats = HeatLane.objects.filter(event=event)
    nheats = heats.values("round_id", "heat").distinct().count()
    nresults = (
        heats.filter(Q(finish_time__isnull=False) | Q(finish_place__isnull=False))
        .values("round_id", "heat")
        .distinct()
        .count()
    )
    nracers = Racer.objects.filter(event=event).count()
    nawards = Award.objects.filter(event=event).count()
    groups = list(
        Group.objects.filter(event=event)
        .annotate(
            aggregate=Count("constituents") + Count("constituent_subgroups"),
        )
        .filter(aggregate=0)
        .order_by("sort_order")
        .values_list("name", flat=True)
    )
    step = _settings_step(row, settings.LOCK_SETTINGS)
    name = connection.settings_dict["NAME"]
    label = row.partition_label
    rule = row.group_formation_rule
    racing_by = (
        f"Racing by {label.lower()}: "
        if rule == "by-partition"
        else "Racing as one big group: "
        if rule == "one-group"
        else "Customized groups: "
    )
    return {
        "locked": settings.LOCK_SETTINGS,
        "database": {
            "status": "ok",
            "writable": True,
            "details": f"<p>{_connection_text()}</p>\n",
        },
        "schema": {
            "status": "ok",
            "button": "re-initialize",
            "details": "<p>The database schema is up to date.</p>\n",
        },
        "purge": {
            "nracers": nracers,
            "nawards": nawards,
            "nheats": nheats,
            "nresults": nresults,
        },
        "roster": {
            "status": "unknown" if nracers == 0 else "ok",
            "count": nracers,
            "fake": int(state.fake_racers),
            "details": (
                f"<p>There {_count_phrase(nracers, 'racer', 'racers')} registered."
                '  (Visit the <a href="checkin.php">Check-In page</a>'
                " to enter individual racers.)</p>\n"
            ),
        },
        "groups": {
            "status": "unknown" if not groups else "ok",
            "label": label,
            "plural": plural(label),
            "details": (
                f"<p>{racing_by}"
                + (
                    f"There are no {plural(label.lower())} defined."
                    if not groups
                    else ", ".join(groups)
                )
                + "</p>\n"
            ),
        },
        "awards": {
            "status": "unknown" if nawards == 0 else "ok",
            "details": (
                f"<p>There {_count_phrase(nawards, 'award', 'awards')} defined."
                '  (Visit the <a href="awards-editor.php">Awards Editor page</a>'
                " to enter individual awards.)</p>\n"
            ),
        },
        "settings": {
            "status": step["status"],
            "details": f"<p>{step['details']}</p>\n",
        },
        "form_fields": {
            "drivers": [connection.vendor],
            "radio": connection.vendor,
            "connection_string": f"{connection.vendor}:{name}",
        },
    }
