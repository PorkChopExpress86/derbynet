"""`capture/scenarios/13-heat-state.sh` replayed through the services.

`timer/partial-and-non-numeric-results.http`: a `FINISHED` that leaves lane 4
unposted (Q11), then one whose lane 1 time is `---` (Q16). PHP leaves lane 4
with no result, and stores the dashes as a time that shows as 0 and places
first. Django gives both cars the did-not-finish time and places them last
(D42).

`suite/points-rerun-reinstate.http`: a rerun and a reinstate under points
racing (Q4). PHP brings back only the places; Django brings back the times
too.

Each deviation's expected exchange is PHP's with only the fields the services
compute changed. To write them again after a change, run

    WRITE_DEVIATIONS=1 uv run pytest racing/services/tests/test_heat_state_parity.py
"""

import json
import os
import re

import pytest

from racing.engine.tests.golden import (
    DEVIATIONS_FILE,
    GOLDEN_DIR,
    read_transcript,
    transcript_deviations,
)

TIMER = "http/timer/partial-and-non-numeric-results.http"
POINTS = "http/suite/points-rerun-reinstate.http"
DEVIATIONS = {
    ("Q11", TIMER): (
        ["002 poll-after-a-lane-without-a-result"],
        "PHP leaves lane 4, which the timer did not report, without a result."
        " Django gives the car a did-not-finish time, 9.999 s, and places the"
        " heat from the times (D42).",
    ),
    ("Q16", TIMER): (
        ["004 heat-results-with-a-dashed-time", "005 standings-with-a-dashed-time"],
        "PHP stores a time of --- as text, shows it as 0 and places it first."
        " Django gives the car a did-not-finish time, 9.999 s, so it places"
        " last (D42).",
    ),
    ("Q4", POINTS): (
        ["003 reinstate-under-points"],
        "Under points racing PHP's rerun saves only the places, so a reinstate"
        " leaves the times empty. Django brings back each lane's time and place.",
    ),
}
FPS_TO_SCALE_MPH = 3600 * 25 / 5280
"""`json_heat_results` in `website/inc/json-heat-results.inc`, with the
default 40 ft track."""


def php(fixture):
    return read_transcript(GOLDEN_DIR / fixture)


def django_replay():
    """Both cases through the services. Returns the Django side of each
    exchange the services compute, keyed by exchange name."""
    from core.models import RaceSettings
    from racing.models import HeatLane
    from racing.services.heats import reinstate, rerun, select_heat
    from racing.services.results import record_timer_result
    from racing.services.standings import standings_snapshot
    from racing.services.tests.race import fields, setup_race

    computed = {}
    replay, round = setup_race()
    event = replay.event
    select_heat(event, round_id=round.id, now_racing=True)
    for name, exchange in php(TIMER).items():
        body = exchange.get("request-body", "")
        if "message=FINISHED" in body:
            record_timer_result(event, fields(body.split("message=FINISHED&", 1)[1]))
        elif "heat=" in exchange["request"]:
            heat = int(re.search(r"heat=(\d+)", exchange["request"])[1])
            rows = HeatLane.objects.filter(round=round, heat=heat).order_by("lane")
            computed[name] = [
                {"lane": lane, "time": time, "place": place}
                for lane, time, place in rows.values_list(
                    "lane", "finish_time", "finish_place"
                )
                if time is not None or place is not None
            ]
        elif "query=standings" in exchange["request"]:
            replay.ids.sync()
            ladder = standings_snapshot(event).standings.ladders[f"st-r{round.id}"]
            computed[name] = {
                "st-r1": [
                    sorted(replay.ids.php["racer"][pk] for pk in rung)
                    for rung in ladder
                ]
            }

    replay, round = setup_race()
    event = replay.event
    RaceSettings.objects.filter(event=event).update(use_points=True)
    select_heat(event, round_id=round.id, now_racing=True)
    for name, exchange in php(POINTS).items():
        body = exchange["request-body"]
        if "message=FINISHED" in body:
            record_timer_result(event, fields(body.split("message=FINISHED&", 1)[1]))
        elif "heat.rerun" in body:
            rerun(event, "last")
        elif "heat.reinstate" in body:
            reinstate(event)
            rows = HeatLane.objects.filter(round=round, heat=1).order_by("lane")
            computed[name] = list(
                rows.values_list("lane", "finish_time", "finish_place")
            )
    return computed


def _speed(time):
    if not time:
        return "--"
    return float(f"{40 / time * FPS_TO_SCALE_MPH:4.1f}")


def expected_body(name, php_body, computed):
    """PHP's body with the computed fields replaced."""
    body = json.loads(php_body)
    if isinstance(computed, list) and computed and isinstance(computed[0], dict):
        body["heat-results"] = [
            {"lane": r["lane"], "time": r["time"], "place": r["place"]}
            | {"speed": _speed(r["time"])}
            for r in computed
        ]
    elif name.endswith("standings-with-a-dashed-time"):
        body["standings"] = computed
    else:
        by_lane = {lane: (time, place) for lane, time, place in computed}
        for racer in body["racers"]:
            time, place = by_lane[racer["lane"]]
            racer["finishtime"] = "" if time is None else time
            racer["finishplace"] = "" if place is None else place
    return json.dumps(body, indent=4, ensure_ascii=False)


pytestmark = pytest.mark.django_db


@pytest.fixture
def computed():
    return django_replay()


@pytest.mark.parametrize(
    ("fixture", "name"),
    [(fixture, n) for (_, fixture), (names, _) in DEVIATIONS.items() for n in names],
)
def test_the_services_give_the_deviation(computed, fixture, name):
    quirk, deviation = transcript_deviations(fixture)[name]
    assert json.loads(deviation["body"]) == json.loads(
        expected_body(name, php(fixture)[name]["body"], computed[name])
    ), quirk
    assert json.loads(deviation["body"]) != json.loads(php(fixture)[name]["body"])


def write_deviations():
    computed = django_replay()
    listing = json.loads(DEVIATIONS_FILE.read_text())
    entries = [
        e for e in listing["deviations"] if (e["quirk"], e["fixture"]) not in DEVIATIONS
    ]
    for (quirk, fixture), (names, reason) in DEVIATIONS.items():
        raw = (GOLDEN_DIR / fixture).read_bytes()
        out = b""
        for name in names:
            start = raw.index(b"### " + name.encode())
            header = raw[start : raw.index(b"\nbody:\n", start)].decode()
            body = expected_body(name, php(fixture)[name]["body"], computed[name])
            data = body.encode()
            header = re.sub(r"length: \d+", f"length: {len(data)}", header)
            header = re.sub(r"\nelapsed-ms: \d+", "", header)
            out += header.encode() + b"\nbody:\n" + data + b"\n"
        expected = f"deviations/{quirk}/{fixture.rsplit('/', 1)[1]}"
        (GOLDEN_DIR / expected).parent.mkdir(parents=True, exist_ok=True)
        (GOLDEN_DIR / expected).write_bytes(out)
        entries.append(
            {
                "quirk": quirk,
                "fixture": fixture,
                "exchanges": names,
                "reason": reason,
                "expected": expected,
            }
        )
        print(f"{quirk} {fixture}: written to {expected}")
    listing["deviations"] = sorted(entries, key=lambda e: int(e["quirk"][1:]))
    DEVIATIONS_FILE.write_text(json.dumps(listing, indent=2, ensure_ascii=False) + "\n")


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the deviation files"
)
def test_write_deviations():
    write_deviations()
