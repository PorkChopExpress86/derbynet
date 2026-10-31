"""SPIKE #71 ONLY. The data both prototypes show, read from the services the
real kiosk pages will use: `racing.services.polls` (the `poll` payload) and
`racing.services.standings.standings_snapshot`. Candidate A renders it into
HTML on the server; candidate B fetches it as JSON."""

import hashlib

from django.db.models import Count, Max

from core.models import RaceSettings, RaceState, SiteState
from kiosks.models import Kiosk
from racing.models import HeatLane
from racing.services import polls
from racing.services.polls import mangled_name
from racing.services.standings import standings_snapshot

NOW_RACING_VALUES = (
    "current-heat,heat-results,precision,racers,timer-trouble,current-reschedule"
)
"""The values `website/js/now-racing.js` asks `query=poll` for."""


def active_event():
    state = SiteState.objects.select_related("active_event").filter(pk=1).first()
    return state.active_event if state else None


def now_racing(event, roundid=None, heat=None) -> dict:
    """The now-racing `poll` payload for the lineup the page shows."""
    return polls.poll(
        event,
        NOW_RACING_VALUES,
        polls.PollArgs(roundid=roundid, heat=heat),
    )


def _heat_board(event, roundid, heat, results: dict) -> dict:
    """One heat's lineup by lane, with whatever results it has."""
    rows = (
        HeatLane.objects.filter(event=event, round_id=roundid, heat=heat)
        .select_related("racer", "round__group")
        .order_by("lane")
    )
    style = RaceSettings.objects.get(event=event).name_style
    lanes, header = [], {}
    for row in rows:
        result = results.get(row.lane)
        header = {"class": row.round.group.name, "round": row.round.number}
        lanes.append(
            {
                "lane": row.lane,
                "carnumber": row.racer.car_number,
                "name": mangled_name(row.racer.first_name, row.racer.last_name, style),
                "time": str(result["time"]) if result else None,
                "place": result["place"] if result else None,
            }
        )
    nheats = HeatLane.objects.filter(event=event, round_id=roundid).aggregate(
        n=Max("heat")
    )["n"]
    return {
        "roundid": roundid,
        "heat": heat,
        "nheats": nheats,
        **header,
        "lanes": lanes,
        "nresults": sum(1 for lane in lanes if lane["place"]),
    }


def now_racing_board(event, roundid=None, heat=None, shown_results=0) -> dict:
    """What the board should show next, from the `poll` payload, as
    now-racing.js decides it: results for the heat on screen come first
    (timer-message has usually moved the current heat on by then), and only
    once they are shown does the next lineup replace it."""
    data = now_racing(event, roundid, heat)
    current = data["current-heat"]
    results = {r["lane"]: r for r in data.get("heat-results", [])}
    shown = (str(roundid), str(heat))
    is_current = shown == (str(current.get("roundid")), str(current.get("heat")))
    if heat and (is_current or len(results) > shown_results):
        board = _heat_board(event, int(roundid), int(heat), results)
    elif current.get("heat"):
        board = _heat_board(event, current["roundid"], current["heat"], {})
    else:
        board = {"roundid": None, "heat": None, "lanes": [], "nresults": 0}
    board["timer_trouble"] = "timer-trouble" in data
    return board


def _avg(value) -> str:
    return "" if value is None else f"{value:.3f}"


def standings(event, key: str | None) -> dict:
    """One catalog entry's standings, best first, as the reveal shows them.
    An unknown or empty key means the first class entry."""
    snapshot = standings_snapshot(event).standings
    catalog = [
        {"key": c.key, "name": c.name, "presentation": c.presentation}
        for c in snapshot.catalog()
    ]
    entry = next((c for c in catalog if c["key"] == key), None)
    if entry is None:
        entry = next((c for c in catalog if c["presentation"] == "class"), None)
    if entry is None:
        return {"catalog": catalog, "entry": None, "rows": []}
    style = RaceSettings.objects.get(event=event).name_style
    rows = []
    for row in snapshot.rows:
        place = row.outcome.get(entry["key"])
        if place is None:
            continue
        racer = row.racer
        rows.append(
            {
                "id": f"{row.round.id}-{racer.id}",
                "place": int(place),
                "carnumber": racer.car_number,
                "name": mangled_name(racer.first_name, racer.last_name, style),
                "group": snapshot.groups[racer.group_id].name,
                "heats": row.summary.base,
                "avg": _avg(row.summary.avg),
                "best": _avg(row.summary.best),
            }
        )
    rows.sort(key=lambda r: r["place"])
    return {"catalog": catalog, "entry": entry, "rows": rows}


def leaders(event, top: int = 3) -> dict:
    """The intermission screen (D12): each class's leaders so far."""
    snapshot = standings_snapshot(event).standings
    style = RaceSettings.objects.get(event=event).name_style
    panels = []
    for c in snapshot.catalog():
        if c.presentation not in ("class", "ff"):
            continue
        rows = []
        for row in snapshot.rows:
            place = row.outcome.get(c.key)
            if place is not None and int(place) <= top:
                r = row.racer
                rows.append(
                    {
                        "id": f"{c.key}-{r.id}",
                        "place": int(place),
                        "carnumber": r.car_number,
                        "name": mangled_name(r.first_name, r.last_name, style),
                        "avg": _avg(row.summary.avg),
                    }
                )
        rows.sort(key=lambda r: r["place"])
        panels.append({"key": c.key, "name": c.name, "rows": rows})
    return {"panels": panels}


def change_signature(event) -> str:
    """What a push notice (#56) would announce: the current heat, the newest
    result, and every kiosk's page and parameters. Cheap enough to read every
    50 ms in the spike's event stream."""
    state = RaceState.objects.filter(event=event).values_list(
        "current_round_id", "current_heat", "racing"
    )
    lanes = HeatLane.objects.filter(event=event).aggregate(
        n=Count("finish_time"), m=Max("completed")
    )
    kiosks = list(Kiosk.objects.filter(event=event).values_list("page", "params"))
    text = repr((list(state), lanes, kiosks))
    return hashlib.sha1(text.encode()).hexdigest()[:12]
