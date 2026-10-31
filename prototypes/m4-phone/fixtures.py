"""Fixture data for the #39 phone prototypes. No database: plain Python values.

A 4-lane track, 40 racers in 3 partitions, Wolves round 1 in progress at heat 6 of 11,
and heat 6 runs with lane 3 empty.
"""

import json
import random

LANES = 4

PARTITIONS = ["Tigers", "Wolves", "Bears"]

_NAMES = {
    "Tigers": [
        "Ethan Brooks", "Noah Patel", "Lucas Kim", "Owen Garcia", "Henry Walsh",
        "Jack Nguyen", "Leo Martinez", "Wyatt Foster", "Caleb Ross", "Isaac Bennett",
        "Miles Ortiz", "Eli Sanders", "Gavin Price", "Ryan Hughes",
    ],
    "Wolves": [
        "Liam Chen", "Sophia Reyes", "Benjamin Ward", "Mason Rivera", "Charlotte Hayes",
        "Logan Cooper", "Amelia Diaz", "James Morgan", "Ava Thompson", "Carter Bell",
        "Harper Long", "Daniel Kelly", "Evelyn Ruiz",
    ],
    "Bears": [
        "Aiden Murphy", "Grace Turner", "Samuel Fisher", "Chloe Howard", "Jacob Gray",
        "Lily Jenkins", "Nathan Perry", "Zoe Coleman", "Dylan Barnes", "Nora Powell",
        "Luke Simmons", "Mia Russell", "Hudson Griffin",
    ],
}

_NOT_PASSED = {206, 211, 213, 303, 306, 308, 310, 311, 313}
_NO_PHOTO = {108, 206, 211, 213, 303, 306, 310, 311, 313}


def _racers():
    out = []
    for p_index, partition in enumerate(PARTITIONS):
        for i, name in enumerate(_NAMES[partition]):
            car = (p_index + 1) * 100 + i + 1
            first, last = name.split(" ", 1)
            racer = {
                "car": car,
                "first": first,
                "last": last,
                "name": name,
                "initials": first[0] + last[0],
                "partition": partition,
                "passed": car not in _NOT_PASSED,
                "photo": car not in _NO_PHOTO,
            }
            racer["pass_label"] = f"Passed inspection, {car} {name}"
            racer["json"] = json.dumps(racer)
            out.append(racer)
    return out


RACERS = _racers()
BY_CAR = {r["car"]: r for r in RACERS}


def _wolves_schedule():
    """11 heats for the 10 checked-in Wolves; 4 lanes are byes (heat 6 lane 3 among them)."""
    field = [r for r in RACERS if r["partition"] == "Wolves" and r["passed"]]
    empty = {(3, 4), (6, 3), (9, 1), (11, 2)}
    rng = random.Random(39)
    heats, k = [], 0
    for h in range(1, 12):
        lanes = []
        for lane in range(1, LANES + 1):
            if (h, lane) in empty:
                lanes.append({"lane": lane, "racer": None})
                continue
            lanes.append({"lane": lane, "racer": field[(k * 7) % len(field)]})
            k += 1
        if h < 6:
            times = sorted(round(rng.uniform(2.80, 3.25), 3) for _ in lanes)
            ranked = [l for l in lanes if l["racer"]]
            rng.shuffle(ranked)
            for place, (lane, t) in enumerate(zip(ranked, times), start=1):
                lane["time"] = f"{t:.3f}"
                lane["place"] = place
        status = "done" if h < 6 else ("current" if h == 6 else "upcoming")
        heats.append({"heat": h, "lanes": lanes, "status": status})
    return heats


WOLVES_HEATS = _wolves_schedule()
CURRENT_HEAT = WOLVES_HEATS[5]
NEXT_HEAT = WOLVES_HEATS[6]
LAST_HEAT = WOLVES_HEATS[4]
LAST_HEAT_RESULTS = sorted((l for l in LAST_HEAT["lanes"] if l["racer"]), key=lambda l: l["place"])


def _count(partition, key="passed"):
    rs = [r for r in RACERS if r["partition"] == partition]
    return sum(1 for r in rs if r[key]), len(rs)


ROUNDS = [
    {"id": "t1", "partition": "Tigers", "round": 1, "status": "done", "status_label": "Complete",
     "heats_done": 14, "heats": 14, "racers": 14},
    {"id": "w1", "partition": "Wolves", "round": 1, "status": "racing", "status_label": "Racing",
     "heats_done": 5, "heats": 11, "racers": 10},
    {"id": "b1", "partition": "Bears", "round": 1, "status": "unscheduled", "status_label": "Not scheduled",
     "heats_done": 0, "heats": 0, "racers": 7},
]
for _r in ROUNDS:
    _r["pct"] = round(100 * _r["heats_done"] / _r["heats"]) if _r["heats"] else 0

GROUPS = []
for _p in PARTITIONS:
    _passed, _total = _count(_p)
    GROUPS.append({"partition": _p, "passed": _passed, "total": _total,
                   "racers": [r for r in RACERS if r["partition"] == _p]})

PASSED = sum(1 for r in RACERS if r["passed"])
TOTAL = len(RACERS)
NOT_PASSED = [r for r in RACERS if not r["passed"]]
RECENT = [BY_CAR[c] for c in (312, 309, 307, 305, 210)]

TIMER = {"state": "Ready", "age": "2 s", "port": "/dev/ttyUSB0", "model": "SmartLine (4 lanes)",
         "last_heat": "Heat 5, 2 min ago"}

COORDINATOR_NAV = [
    {"label": "Dashboard", "icon": "dashboard", "href": "dashboard-a.html", "key": "dashboard"},
    {"label": "Check-In", "icon": "checkin", "href": "checkin-a.html", "key": "checkin"},
    {"label": "Standings", "icon": "standings", "href": "#standings", "key": "standings"},
    {"label": "More", "icon": "more", "more": True, "key": "more"},
]
COORDINATOR_MORE = [
    ("Before", ["Set-Up", "Racer Photos", "Car Photos", "Settings"]),
    ("During", ["Kiosk Dashboard", "Judging", "Racers On Deck", "Results By Racer"]),
    ("After", ["Present Awards", "Export Results", "Retrospective"]),
    ("Other", ["About"]),
]
CREW_NAV = [
    {"label": "Check-In", "icon": "checkin", "href": "checkin-a.html", "key": "checkin"},
    {"label": "Photos", "icon": "photos", "href": "#photos", "key": "photos"},
    {"label": "On Deck", "icon": "ondeck", "href": "#ondeck", "key": "ondeck"},
    {"label": "More", "icon": "more", "more": True, "key": "more"},
]
CREW_MORE = [
    ("Before", ["Car Photos"]),
    ("During", ["Results By Racer"]),
    ("Other", ["About"]),
]
