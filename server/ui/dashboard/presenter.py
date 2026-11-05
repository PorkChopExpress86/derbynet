"""What the race dashboard shows, from the coordinator payload (#41).

`present()` turns `racing.services.polls.coordinator()`'s payload, PHP's
`poll.coordinator` reply, and the `Extras` the payload lacks (closed lanes,
final requests, the playlist intermission) into the template context. It
reads no database, so a golden payload captured from PHP renders exactly as
a live one (`ui/tests/test_dashboard_golden.py`).

The rules are `process_coordinator_poll_json()` and its helpers in
`website/js/coordinator-poll.js`: the round categories, which buttons a
round card offers, the Re-Run label, and the master schedule's totals.
"""

import re
from dataclasses import dataclass, field

from ui.responses import car, racer_name

CATEGORIES = (
    ("now-racing", "Now racing"),
    ("master-schedule", "Master schedule"),
    ("ready-to-race", "Ready to race"),
    ("not-yet-scheduled", "Not yet scheduled"),
    ("done-racing", "Done racing"),
)
MASTER_ROUND = -1
TIMER_TEST_ROUND = -100
SCORING = {0: "Average all heats", 1: "Drop the slowest heat", 2: "Best heat"}
_SECONDS = re.compile(r"\s*\(\d+ sec\.\)$")


def strip_seconds(message: str) -> str:
    """A timer or replay message without PHP's "(12 sec.)", which the page
    counts up itself."""
    return _SECONDS.sub("", message)


@dataclass(frozen=True)
class Staging:
    """The racers of the next heat while a finished heat is held on screen."""

    lane: int
    car: str
    name: str


@dataclass
class Held:
    """A finished heat the page keeps on screen for 10 s after the current
    heat moves on (`hold_until` in `coordinator-poll.js`): its round and heat,
    its racers by lane, and its results."""

    round_id: int
    heat: int
    racers: list[dict]
    results: list[dict]


@dataclass
class NotAddedRow:
    racer_id: int
    car: str
    name: str
    round_id: int
    round_name: str
    exclude: bool = False
    no_speed_awards: bool = False


@dataclass
class Extras:
    """What the dashboard shows beyond PHP's payload."""

    # `racing.services.lanes.poll_lanes()`.
    lanes: dict = field(default_factory=lambda: {"closed-lanes": [], "scoring": 0})
    # Final requests, each with the racer's name and group (D32).
    final_requests: list[dict] = field(default_factory=list)
    # Finalist suggestions, each with the racers named (D32).
    suggestions: list[dict] = field(default_factory=list)
    # Racer id to (exclude, not eligible for speed awards), for the
    # not-added list (D33).
    award_settings: dict[int, tuple[bool, bool]] = field(default_factory=dict)
    # Held emptied heats (D39), as (round id, heat).
    emptied: set[tuple[int, int]] = field(default_factory=set)
    # The playlist intermission (D12), or None.
    intermission: dict | None = None
    # Upcoming heats under the master schedule: (master heat, round name, heat).
    master_heats: list[tuple[int, str, int]] = field(default_factory=list)
    warn_no_timer: bool = True
    use_subgroups: bool = False
    can_set_up: bool = True
    # Seconds since the timer and the replay client last called.
    timer_age: int | None = None
    replay_age: int | None = None
    held: Held | None = None
    # The timer's lane count when it differs from the race's (D74), from
    # `racing.services.timer.reported_lane_count()`.
    reported_lanes: int | None = None


@dataclass
class Lane:
    lane: int
    car: str = ""
    name: str = ""
    photo: str = ""
    result: str = ""
    closed: bool = False
    staging: Staging | None = None

    @property
    def color(self) -> int:
        return (self.lane - 1) % 4 + 1


@dataclass
class RoundCard:
    roundid: int
    name: str
    category: str
    round: int
    aggregate: bool
    roster_size: int
    passed: int
    scheduled_racers: int
    unscheduled: int
    heats_scheduled: int
    heats_run: int
    next_round: bool = False
    current: bool = False
    buttons: list[str] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)
    adjust_needed: str = ""
    no_lanes: bool = False
    not_scheduled: bool = False
    closed_lanes: list[int] = field(default_factory=list)
    emptied_heats: list[int] = field(default_factory=list)

    @property
    def show_checkins(self) -> bool:
        return self.round == 1 and self.heats_scheduled == 0

    @property
    def heats_percent(self) -> int:
        if not self.heats_scheduled:
            return 0
        return round(100 * self.heats_run / self.heats_scheduled)

    @property
    def passed_percent(self) -> int:
        if not self.roster_size:
            return 0
        return round(100 * self.passed / self.roster_size)

    @property
    def scheduled_percent(self) -> int:
        if not self.roster_size:
            return 0
        return round(100 * self.scheduled_racers / self.roster_size)

    @property
    def departed(self) -> int:
        return sum(1 for a in self.adjustments if a["why"] == "departed")

    @property
    def left_out(self) -> int:
        return sum(1 for a in self.adjustments if a["why"] == "unscheduled")


def _category(round: dict) -> str:
    if round["heats_scheduled"] > round["heats_run"]:
        return "ready-to-race"
    if round["heats_run"] > 0:
        return "done-racing"
    return "not-yet-scheduled"


def _who(a: dict) -> str:
    return f"{car(a['carnumber'])} {racer_name(a['firstname'], a['lastname'])}"


def round_buttons(round: dict, current_id: int, lanes: int) -> tuple[list[str], str]:
    """The buttons of a round card and the adjustment notice, as
    `inject_into_scheduling_control_group()` picks them. "no-lanes" stands
    for PHP's note that the lane count is not set."""
    if round["roundid"] == MASTER_ROUND:
        return ["next-up"], ""
    buttons, notice = [], ""
    scheduled, run = round["heats_scheduled"], round["heats_run"]
    adjustments = round.get("adjustments") or []
    if scheduled > 0 and run == 0:
        if adjustments:
            notice = "regenerate"
        buttons.append("unschedule")
    if scheduled == 0 and round["unscheduled"] > 0:
        buttons.append("schedule" if lanes > 0 else "no-lanes")
    if adjustments and run > 0:
        notice = "adjust"
        buttons.append("adjust")
    if scheduled == 0 and run == 0 and (round["round"] > 1 or round.get("aggregate")):
        buttons.append("delete")
    if round["roundid"] != current_id:
        if run > 0:
            buttons.append("changes")
        elif scheduled > 0 and run < scheduled:
            buttons.append("race")
    if run > 0:
        buttons.append("purge")
    return buttons, notice


def master_totals(rounds: list[dict]) -> dict:
    """`calculate_totals()`: the rounds of the highest round number, summed
    into one synthetic round."""
    top = max((r["round"] for r in rounds), default=-1)
    chosen = [r for r in rounds if r["round"] == top]
    return {
        "roundid": MASTER_ROUND,
        "classid": None,
        "class": "Interleaved Schedule",
        "name": "Interleaved Schedule",
        "round": top,
        "aggregate": False,
        "roster_size": sum(r["roster_size"] for r in chosen),
        "passed": sum(r["passed"] for r in chosen),
        "unscheduled": sum(r["unscheduled"] for r in chosen),
        "heats_scheduled": sum(r["heats_scheduled"] for r in chosen),
        "heats_run": sum(r["heats_run"] for r in chosen),
        "adjustments": [],
    }


def completed_rounds(rounds: list[dict], classes: list[dict]) -> list[dict]:
    """`offer_new_rounds()`: the highest round of each group, when all its
    heats have run, with the group's subgroups."""
    highest: dict = {}
    for r in rounds:
        best = highest.get(r["classid"])
        if best is None or best["round"] < r["round"]:
            highest[r["classid"]] = r
    by_id = {c["classid"]: c for c in classes}
    done = []
    for r in highest.values():
        if r["heats_scheduled"] > 0 and r["heats_scheduled"] == r["heats_run"]:
            group = by_id.get(r["classid"], {})
            done.append({**r, "subgroups": group.get("subgroups", [])})
    return done


def rerun(payload: dict) -> tuple[str, str]:
    """`update_for_last_heat()`: the Re-Run button's state and label. The
    state is none, recoverable, available or current."""
    kind = payload.get("last-heat", "none")
    if kind == "recoverable":
        return "recoverable", "Reinstate Heat"
    racers = payload["racers"]
    if any(r.get("finishtime") or (r.get("finishplace") or 0) > 0 for r in racers):
        return "current", "Re-Run This Heat"
    if kind == "none":
        return "none", "Re-Run"
    return "available", "Re-Run Previous"


def pill_state(icon: str) -> str:
    if icon.endswith("ok.png"):
        return "ok"
    if icon.endswith("trouble.png"):
        return "danger"
    return "warn"


def _lane_count(payload: dict) -> int:
    lanes = payload["timer-state"]["lanes"]
    try:
        return int(lanes)
    except TypeError, ValueError:
        return 0


def _lanes(payload: dict, extras: Extras, closed: set[int]) -> list[Lane]:
    use_points = payload["current-heat"]["use_points"]
    held = extras.held
    racers = held.racers if held else payload["racers"]
    by_lane = {r["lane"]: r for r in racers}
    staging = {r["lane"]: r for r in payload["racers"]} if held else {}
    results = {r["lane"]: r for r in held.results} if held else {}
    n = max([_lane_count(payload), *by_lane, *staging], default=0)
    lanes = []
    for number in range(1, n + 1):
        row = Lane(number, closed=number in closed)
        racer = by_lane.get(number)
        if racer:
            row.car = car(racer["carnumber"])
            row.name = racer["name"]
            row.photo = racer.get("photo", "")
            key = "finishplace" if use_points else "finishtime"
            row.result = str(racer.get(key, ""))
        if number in results:
            hit = results[number]
            row.result = str(hit["place"] if use_points else hit["time"])
        if held and number in staging:
            nxt = staging[number]
            row.staging = Staging(number, car(nxt["carnumber"]), nxt["name"])
        lanes.append(row)
    return lanes


def present(payload: dict, extras: Extras | None = None) -> dict:
    """The dashboard's template context."""
    extras = extras or Extras()
    current = payload["current-heat"]
    current_id = current["roundid"]
    lanes = _lane_count(payload)
    master = current["use_master_sched"]
    closed_rows = extras.lanes.get("closed-lanes", [])
    closed = {row["lane"] for row in closed_rows}
    closed_by_round: dict[int, list[int]] = {}
    for row in closed_rows:
        for round_id in row["roundids"]:
            closed_by_round.setdefault(round_id, []).append(row["lane"])

    cards: dict[str, list[RoundCard]] = {key: [] for key, _ in CATEGORIES}
    current_round = None
    for r in payload["rounds"]:
        category = "now-racing" if r["roundid"] == current_id else _category(r)
        buttons, notice = round_buttons(r, current_id, lanes)
        card = RoundCard(
            roundid=r["roundid"],
            name=r["name"],
            category=category,
            round=r["round"],
            aggregate=bool(r.get("aggregate")),
            roster_size=r["roster_size"],
            passed=r["passed"],
            scheduled_racers=r["passed"] - r["unscheduled"],
            unscheduled=r["unscheduled"],
            heats_scheduled=r["heats_scheduled"],
            heats_run=r["heats_run"],
            next_round=bool(r.get("next-round")),
            current=category == "now-racing",
            buttons=[b for b in buttons if b != "no-lanes"],
            adjustments=r.get("adjustments") or [],
            adjust_needed=notice,
            no_lanes="no-lanes" in buttons,
            not_scheduled=master and r["heats_scheduled"] == 0,
            closed_lanes=sorted(closed_by_round.get(r["roundid"], [])),
            emptied_heats=sorted(h for rid, h in extras.emptied if rid == r["roundid"]),
        )
        if card.current:
            current_round = card
        cards[category].append(card)
    if master:
        totals = master_totals(payload["rounds"])
        buttons, _ = round_buttons(totals, current_id, lanes)
        cards["master-schedule"].append(
            RoundCard(
                roundid=MASTER_ROUND,
                name=totals["name"],
                category="master-schedule",
                round=totals["round"],
                aggregate=False,
                roster_size=totals["roster_size"],
                passed=totals["passed"],
                scheduled_racers=totals["passed"] - totals["unscheduled"],
                unscheduled=totals["unscheduled"],
                heats_scheduled=totals["heats_scheduled"],
                heats_run=totals["heats_run"],
                buttons=buttons,
            )
        )
    groups = [(key, label, cards[key]) for key, label in CATEGORIES if cards[key]]

    rerun_state, rerun_label = rerun(payload)
    timer = payload["timer-state"]
    replay = payload["replay-state"]
    heat_no = current["heat"]
    shown_round = extras.held.round_id if extras.held else current_id
    shown_heat = extras.held.heat if extras.held else heat_no
    held_card = None
    if extras.held:
        held_card = next(
            (r for r in payload["rounds"] if r["roundid"] == extras.held.round_id), None
        )
    title_round = held_card or (
        next((r for r in payload["rounds"] if r["roundid"] == current_id), None)
    )
    if current_id == TIMER_TEST_ROUND:
        title, heat_text = "Timer test", ""
    elif title_round is None:
        title, heat_text = "No round is racing", ""
    else:
        title = title_round["name"]
        heat_text = f"Heat {shown_heat} of {title_round['heats_scheduled']}"
    master_text = ""
    if master and current.get("masterheat") and not extras.held:
        master_text = (
            f"Heat {current['masterheat']} of {current['max_masterheat']}"
            " in the master schedule"
        )
    lane_rows = _lanes(payload, extras, closed)
    in_round = current_id is not None and current_id > 0 and heat_no is not None
    # Every car left the current heat, which keeps its number until the race
    # crew runs or skips it (D39).
    emptied = in_round and not extras.held and not payload["racers"]
    rounds = payload["rounds"]
    done = completed_rounds(rounds, payload["classes"])
    timer_test_running = current_id == TIMER_TEST_ROUND and current["now_racing"]
    not_added = []
    for r in rounds:
        if r["heats_scheduled"] <= r["heats_run"]:
            continue
        for a in r.get("adjustments") or []:
            if a["why"] != "unscheduled":
                continue
            exclude, no_speed = extras.award_settings.get(a["racerid"], (False, False))
            not_added.append(
                NotAddedRow(
                    racer_id=a["racerid"],
                    car=car(a["carnumber"]),
                    name=racer_name(a["firstname"], a["lastname"]),
                    round_id=r["roundid"],
                    round_name=r["name"],
                    exclude=exclude,
                    no_speed_awards=no_speed,
                )
            )
    return {
        "payload": payload,
        "heat": {
            "title": title,
            "heat_text": heat_text,
            "master_text": master_text,
            "racing": current["now_racing"],
            "use_points": current["use_points"],
            "lanes": lane_rows,
            "holding": extras.held is not None,
            "emptied": emptied,
            "in_round": in_round,
            "round_id": shown_round,
            "heat": shown_heat,
            "current_round_id": current_id,
            "current_heat": heat_no,
        },
        "current_round": current_round,
        "rerun": {
            "state": rerun_state,
            "label": rerun_label,
            "enabled": rerun_state != "none",
        },
        "remote_start": bool(timer.get("remote-start")),
        "timer": {
            "monitored": extras.warn_no_timer,
            "text": strip_seconds(timer["message"]),
            "state": pill_state(timer["icon"]),
            "age": extras.timer_age,
            "lanes": lanes,
            "reported_lanes": extras.reported_lanes,
        },
        "replay": {
            "text": strip_seconds(replay["message"]),
            "state": pill_state(replay["icon"]),
            "age": extras.replay_age,
            "connected": replay.get("connected", False),
        },
        "timer_test_running": timer_test_running,
        "refused_results": payload.get("refused-results", 0),
        "not_racing": not current["now_racing"],
        "groups": groups,
        "add_new_rounds": bool(done),
        "completed_rounds": done,
        "use_master_sched": master,
        "playlist_start": current_id == MASTER_ROUND
        and any(r.get("next-round") for r in rounds),
        "not_added": not_added,
        "final_requests": extras.final_requests,
        "suggestions": extras.suggestions,
        "closed_lanes": closed_rows,
        "scoring": SCORING.get(extras.lanes.get("scoring", 0), ""),
        "intermission": extras.intermission,
        "master_heats": extras.master_heats,
        "can_set_up": extras.can_set_up,
        "lane_count": lanes,
    }
