"""The race dashboard's live state (#41): the coordinator payload, what the
dashboard adds to it, the heat held on screen, and the state signature.

Reads only, through the racing services: the page and its poll write
neither the session nor the race state.

The page sends the heat it shows (`round`, `heat`) and the time until which
it holds a finished heat (`hold`, epoch milliseconds, 0 for none). PHP keeps
these on the client (`g_current_heat` in `coordinator-poll.js`); here the
server works out the same hold from them, so a manager sees the results of a
heat for 10 s before the page moves on to the next one.
"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass

from django.urls import get_script_prefix
from django.utils import timezone

from accounts.permissions import Perm, allows
from core.models import Event, RaceSettings, RaceState
from kiosks.models import ReplayStatus
from racing.engine.standings import finalists
from racing.models import HeatLane, Round, TimerStatus
from racing.services import finals, lanes, master, polls
from racing.services.standings import standings_snapshot
from roster.models import Racer
from roster.services.racers import HEADSHOTS
from ui.dashboard.presenter import Extras, Held, present, strip_seconds
from ui.responses import racer_name

HOLD_MS = 10_000
LEADERS = 3
MASTER_LIST = 8
PHOTO_RENDER = "80x80"


@dataclass(frozen=True)
class Shown:
    """The heat the page shows and how long it holds it."""

    round_id: int | None = None
    heat: int | None = None
    hold: int = 0

    @classmethod
    def from_request(cls, data) -> Shown:
        def number(name):
            try:
                return int(data.get(name, ""))
            except ValueError:
                return None

        return cls(number("round"), number("heat"), number("hold") or 0)


def _now_ms() -> int:
    return int(time.time() * 1000)


def racers_of_heat(event: Event, settings: RaceSettings, round_id, heat) -> list[dict]:
    rows = (
        HeatLane.objects.filter(event=event, round_id=round_id, heat=heat)
        .select_related("racer")
        .order_by("lane")
    )
    return [
        {
            "lane": row.lane,
            "racerid": row.racer_id,
            "name": polls.mangled_name(
                row.racer.first_name, row.racer.last_name, polls.FULL_NAME
            ),
            "carnumber": row.racer.car_number,
            "photo": "",
            "finishtime": polls.format_time(settings.finish_formatting, row.finish_time)
            if row.finish_time
            else "",
            "finishplace": row.finish_place or "",
        }
        for row in rows
    ]


def _hold(
    event, settings, payload, shown: Shown, now_ms: int
) -> tuple[Held | None, Shown]:
    """`generate_current_heat_racers()`: when the current heat moved on from
    the heat the page shows, and that heat has results, keep showing it for
    10 s with the next heat's racers beside it."""
    current = payload["current-heat"]
    cur = (current["roundid"], current["heat"])
    hold = shown.hold
    if shown.round_id is not None and (shown.round_id, shown.heat) != cur and hold == 0:
        if polls.heat_results(event, shown.round_id, shown.heat):
            hold = now_ms + HOLD_MS
    if hold and now_ms < hold and shown.round_id is not None:
        held = Held(
            round_id=shown.round_id,
            heat=shown.heat,
            racers=racers_of_heat(event, settings, shown.round_id, shown.heat),
            results=polls.heat_results(event, shown.round_id, shown.heat),
        )
        return held, Shown(shown.round_id, shown.heat, hold)
    return None, Shown(cur[0], cur[1], 0)


def _intermission(event: Event, state: RaceState) -> dict | None:
    """The playlist step that waits for the race coordinator (D12): the round
    that ended, its leaders, and the round that starts next."""
    entry = state.waiting_entry
    if entry is None:
        return None
    nxt = Round.objects.filter(
        group_id=entry.group_id, number=entry.round_number
    ).first()
    info = {
        "next_name": f"{entry.group.name}, Round {entry.round_number}",
        "next_racers": nxt.roster.count() if nxt else 0,
        "ended_name": "",
        "leaders": [],
    }
    ended = state.ended_round
    if ended is not None:
        info["ended_name"] = f"{ended.group.name}, Round {ended.number}"
        standings = standings_snapshot(event).standings
        cut = finalists.from_round(
            standings, ended.id, ended.group_id, bucketed=False, limit=LEADERS
        )
        picked = [f.racer_id for f in cut.finalists][:LEADERS]
        by_id = Racer.objects.in_bulk(picked)
        info["leaders"] = [
            {
                "place": i,
                "car": by_id[pk].car_number,
                "name": racer_name(by_id[pk].first_name, by_id[pk].last_name),
            }
            for i, pk in enumerate(picked, 1)
            if pk in by_id
        ]
    return info


def _finals(event: Event) -> tuple[list[dict], list[dict]]:
    polled = finals.poll_finals(event)
    ids = {r["racerid"] for r in polled["final-requests"]}
    for s in polled["finalist-suggestions"]:
        ids |= set(s["add"]) | set(s["remove"])
    racers = Racer.objects.select_related("group").in_bulk(ids)
    rounds = Round.objects.select_related("group").in_bulk(
        [s["roundid"] for s in polled["finalist-suggestions"]]
    )
    requests = []
    for r in polled["final-requests"]:
        racer = racers[r["racerid"]]
        requests.append(
            {
                "id": r["requestid"],
                "state": r["state"],
                "racer_id": racer.pk,
                "car": racer.car_number,
                "name": racer_name(racer.first_name, racer.last_name),
                "group": racer.group.name,
                "exclude": racer.exclude,
                "no_speed_awards": racer.not_eligible_for_speed_awards,
            }
        )

    def named(pk):
        r = racers[pk]
        return {
            "id": pk,
            "car": r.car_number,
            "name": racer_name(r.first_name, r.last_name),
        }

    suggestions = []
    for s in polled["finalist-suggestions"]:
        final = rounds[s["roundid"]]
        suggestions.append(
            {
                "round_id": final.pk,
                "name": f"{final.group.name}, Round {final.number}",
                "failure": s.get("failure", ""),
                "add": [named(pk) for pk in s["add"]],
                "remove": [named(pk) for pk in s["remove"]],
            }
        )
    return requests, suggestions


def _master_heats(event: Event) -> list[tuple[int, str, int]]:
    names = {
        r.pk: f"{r.group.name}, Round {r.number}"
        for r in Round.objects.filter(event=event).select_related("group")
    }
    heats: dict[int, tuple[int, int, bool]] = {}
    for n, round_id, heat, done in master.master_index(event):
        if n is None:
            continue
        known = heats.get(n)
        heats[n] = (round_id, heat, done or (known[2] if known else False))
    upcoming = [
        (n, names.get(round_id, ""), heat)
        for n, (round_id, heat, done) in sorted(heats.items())
        if not done
    ]
    return upcoming[:MASTER_LIST]


def _age(moment, now) -> int | None:
    if moment is None:
        return None
    return max(0, int((now - moment).total_seconds()))


@dataclass
class Live:
    context: dict
    shown: Shown
    signature: str


def _material(payload: dict, extras: Extras, shown: Shown) -> str:
    """What the signature covers: everything the page renders but the
    seconds since the timer and the replay client called, which the page
    counts up itself. The time of their last call is covered, so a call
    resets the count."""
    payload = json.loads(json.dumps(payload, default=str))
    for key in ("timer-state", "replay-state"):
        payload[key]["message"] = strip_seconds(payload[key]["message"])
    extras_dict = asdict(extras)
    extras_dict.pop("timer_age")
    extras_dict.pop("replay_age")
    extras_dict["emptied"] = sorted(extras.emptied)
    return json.dumps(
        {"payload": payload, "extras": extras_dict, "shown": asdict(shown)},
        sort_keys=True,
        default=str,
    )


def build(request, event: Event, shown: Shown) -> Live:
    """The dashboard's context for `shown`, with its signature. It writes
    nothing: a finished playlist step's scene comes due at the next kiosk
    poll, which applies it before it reads the scene, as PHP's
    `query.poll.kiosk.inc` does (#41 risks: a poll writes no race state)."""
    now = timezone.now()
    payload = polls.coordinator(event)
    settings = RaceSettings.objects.get(event=event)
    state = RaceState.objects.select_related(
        "waiting_entry__group", "ended_round__group"
    ).get(event=event)
    # The current heat's racers with their photos, which PHP's coordinator
    # payload leaves out.
    run = polls.running(event)
    prefix = get_script_prefix()
    payload["racers"] = [
        {**r, "photo": prefix + r["photo"] if r["photo"] else ""}
        for r in polls.current_racers(
            run, polls.FULL_NAME, polls.Render(HEADSHOTS, PHOTO_RENDER)
        )
    ]
    held, shown = _hold(event, settings, payload, shown, _now_ms())
    requests, suggestions = _finals(event)
    left_out = {
        a["racerid"]
        for r in payload["rounds"]
        for a in r.get("adjustments") or []
        if a["why"] == "unscheduled"
    }
    award_settings = {
        pk: (exclude, no_speed)
        for pk, exclude, no_speed in Racer.objects.filter(pk__in=left_out).values_list(
            "pk", "exclude", "not_eligible_for_speed_awards"
        )
    }
    timer_row = TimerStatus.objects.get(event=event)
    replay_row = ReplayStatus.objects.get(event=event)
    extras = Extras(
        lanes=lanes.poll_lanes(event),
        final_requests=requests,
        suggestions=suggestions,
        award_settings=award_settings,
        emptied={(rid, heat) for _, rid, heat, _ in master.held_emptied_heats(event)},
        intermission=_intermission(event, state),
        master_heats=_master_heats(event) if settings.use_master_sched else [],
        warn_no_timer=settings.warn_no_timer,
        use_subgroups=settings.use_subgroups,
        can_set_up=allows(request, Perm.SET_UP),
        timer_age=_age(timer_row.last_contact, now),
        replay_age=_age(replay_row.last_contact, now),
        held=held,
    )
    context = present(payload, extras)
    signature = hashlib.sha256(_material(payload, extras, shown).encode()).hexdigest()[
        :20
    ]
    context["shown"] = shown
    context["signature"] = signature
    return Live(context, shown, signature)
