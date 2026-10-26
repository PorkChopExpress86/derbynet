"""`speed_awards()` and `current_speed_award()` against the PHP transcripts.

Every `query=award.list` exchange under `tests/golden/http/standings/` is
compared with the engine, award for award and in order: key, place, racer,
category, group and subgroup. Every `export-json.php` exchange's Awards
sheet is compared on the speed-award rows: name, type, group, subgroup and
racer. The events and settings come from `standings_fixtures`. Between them
the fixtures cover `one-trophy-per` on and off, `n-rank-trophies` 0, 1 and
3, `full-field-calc` 0, -1 and an aggregate's id, and group and subgroup
trophy counts, which `test_the_award_settings_the_ticket_lists_are_compared`
checks.

An award's `sort` is in no fixture: `award.list` leaves it out and the
export only orders by it. The differential check against PHP (#26) covers
it.

Q7 is a Fix (#20): PHP gives a tie's place to a null racer when the tie's
first racer already holds a trophy under `one-trophy-per`, and Django gives
it to the next racer of the tie. The exchanges of
`q7-tie-after-earlier-trophy.http` where Django differs are listed in
`tests/golden/deviations.json` with Django's exchanges under
`tests/golden/deviations/Q7/`. To write both again after a change, run
`uv run python -m racing.engine.tests.test_speed_awards_parity` from
`server/`.
"""

import json
import re

import pytest

from racing.engine.standings import oracle
from racing.engine.standings.oracle import Standings
from racing.engine.standings.speed_awards import (
    CurrentAward,
    SpeedAward,
    current_speed_award,
    speed_awards,
)

from .golden import DEVIATIONS_FILE, GOLDEN_DIR, read_transcript
from .standings_fixtures import (
    EXPORT,
    STANDINGS,
    STANDINGS_DIR,
    Fixture,
    body,
    groups_of,
    php_json,
)

AWARD_LIST = "GET action.php?query=award.list"
Q7 = "q7-tie-after-earlier-trophy"
Q7_FIXTURE = f"http/standings/{Q7}.http"
Q7_REASON = (
    "Django gives a tie's place to the tie's next racer when its first racer "
    "already holds a trophy; PHP gives it to a null racer."
)

# See NULL_SCORE_500 in test_standings_parity.py: the awards read the same
# standings, so PHP answers 500, or awards places from a ladder with a rung
# -1. With points on and best-heat scoring, 56 racers tie for first in the
# full field of suite-aggregate-classes, and `chr(ord('a') + $i)` (awards.inc
# line 119) runs past "z" and, from the 32nd racer, past 0x7f. The key is
# then not UTF-8, `json_encode()` returns false, and action.php answers 200
# with an empty line.
NULL_SCORE_500 = "a NULL score in time scoring: TypeError at standings.inc:382"
NULL_SCORE_RUNG = "a NULL score in time scoring: PHP awards from rung -1"
TIE_PAST_Z = "a tie of more than 31 racers: the key's letter is not UTF-8"
POINTS = "suite-points-racing"
AGGREGATES = "suite-aggregate-classes"
UNCOMPARABLE = {
    (POINTS, "005 points=0 scoring=0 subgroups=0 award.list"): NULL_SCORE_500,
    (POINTS, "007 points=0 scoring=0 subgroups=1 award.list"): NULL_SCORE_500,
    (POINTS, "009 points=0 scoring=1 subgroups=0 award.list"): NULL_SCORE_RUNG,
    (POINTS, "011 points=0 scoring=1 subgroups=1 award.list"): NULL_SCORE_RUNG,
    (POINTS, "013 points=0 scoring=2 subgroups=0 award.list"): NULL_SCORE_500,
    (POINTS, "015 points=0 scoring=2 subgroups=1 award.list"): NULL_SCORE_500,
    (POINTS, "028 one-trophy-per=0 award.list"): NULL_SCORE_500,
    (POINTS, "029 one-trophy-per=1 award.list"): NULL_SCORE_500,
    (POINTS, "030 n-rank-trophies=0 award.list"): NULL_SCORE_500,
    (POINTS, "031 n-rank-trophies=1 award.list"): NULL_SCORE_500,
    (POINTS, "032 n-rank-trophies=3 award.list"): NULL_SCORE_500,
    (AGGREGATES, "025 points=1 scoring=2 subgroups=0 award.list"): TIE_PAST_Z,
    (AGGREGATES, "027 points=1 scoring=2 subgroups=1 award.list"): TIE_PAST_Z,
}

FIXTURES = sorted(
    path.stem
    for path in STANDINGS_DIR.glob("*.http")
    if any(e["request"] == AWARD_LIST for e in read_transcript(path).values())
)


def django_awards(fixture: Fixture, exchange: str):
    standings = Standings(fixture.event(exchange))
    return standings, speed_awards(standings, fixture.state(exchange).trophies).awards


def speed_award_json(award: SpeedAward) -> dict:
    """One entry of `json_speed_awards()`."""
    entry = {
        "type": "speed-award",
        "racerid": award.racer_id,
        "key": award.key,
        "place": award.place,
        "category": str(award.category),
    }
    if award.group_id is not None:
        entry["classid"] = award.group_id
    if award.subgroup_id is not None:
        entry["rankid"] = award.subgroup_id
    return entry


def awards_sheet_rows(standings: Standings, awards) -> list[list]:
    """The speed-award rows of `export_awards()`."""
    event = standings.event
    rows = []
    for award in awards:
        row = [award.name, "Speed Trophy"]
        if len(event.groups) > 1:
            group = standings.groups.get(award.group_id)
            row.append(group.name if group else None)
        if event.settings.use_subgroups:
            subgroup = standings.subgroups.get(award.subgroup_id)
            row.append(subgroup.name if subgroup else None)
        racer = standings.racers[award.racer_id]
        row += [racer.first_name, racer.last_name, racer.car_number, racer.car_name]
        rows.append(row)
    return rows


def speed_rows(sheets) -> list[list]:
    """The Awards sheet's speed-award rows, which come first."""
    rows = dict(sheets)["Awards"][1:]
    speed = [r for r in rows if r[1] == "Speed Trophy"]
    assert rows[: len(speed)] == speed
    return speed


def compared_award_lists():
    for stem in FIXTURES:
        fixture = Fixture(stem)
        for name in fixture.named(AWARD_LIST):
            if (stem, name) not in UNCOMPARABLE:
                yield fixture, name


def test_every_award_list_matches_php_or_its_deviation():
    wrong = {}
    compared = 0
    for fixture, name in compared_award_lists():
        _, awards = django_awards(fixture, name)
        django = [speed_award_json(a) for a in awards]
        expected = body(fixture.expected(name))["speed-awards"]
        compared += 1
        if django != expected:
            wrong[(fixture.stem, name)] = (django, expected)
        if name in fixture.deviated:
            php = body(fixture.exchanges[name])["speed-awards"]
            assert django != php, f"{name} no longer differs from PHP"
    assert wrong == {}
    total = sum(len(Fixture(s).named(AWARD_LIST)) for s in FIXTURES)
    assert compared + len(UNCOMPARABLE) == total


def test_every_awards_sheet_matches_php_or_its_deviation():
    for stem in FIXTURES:
        fixture = Fixture(stem)
        for name in fixture.named(EXPORT):
            standings, awards = django_awards(fixture, name)
            django = awards_sheet_rows(standings, awards)
            assert django == speed_rows(body(fixture.expected(name))), (stem, name)
            if name in fixture.deviated:
                assert django != speed_rows(body(fixture.exchanges[name])), name


@pytest.mark.parametrize(("stem", "exchange"), sorted(UNCOMPARABLE))
def test_uncomparable_exchanges_are_what_the_list_says(stem, exchange):
    fixture = Fixture(stem)
    php = fixture.exchanges[exchange]
    standings, awards = django_awards(fixture, exchange)
    reason = UNCOMPARABLE[(stem, exchange)]
    if reason == TIE_PAST_Z:
        assert (php["status"], php["body"]) == ("200", b"\n")
        assert any(len(rung) > 31 for rung in standings.ladders.values())
        assert any(not a.key.isascii() for a in awards)
        return
    scores = {r.summary.score is None for r in standings.rows}
    assert scores == {True, False}, "some racers have a score and some have none"
    if reason == NULL_SCORE_500:
        assert (php["status"], php["body"]) == ("500", b"")
    else:
        assert php["status"] == "200"
        (standings_exchange,) = [
            n
            for n in fixture.named(STANDINGS)
            if n.split(" ")[1:-1] == exchange.split(" ")[1:-1]
        ]
        ladders = body(fixture.exchanges[standings_exchange])["standings"]
        assert all("-1" in ladder for ladder in ladders.values())


def test_the_award_settings_the_ticket_lists_are_compared():
    seen = set()
    for fixture, name in compared_award_lists():
        event = fixture.event(name)
        trophies = fixture.state(name).trophies
        calc = event.settings.full_field_calc
        seen.add(("one-trophy-per", trophies.one_trophy_per))
        seen.add(("n-rank-trophies", trophies.n_subgroup))
        seen.add(("full-field-calc", "aggregate" if calc > 0 else calc))
        if any(g.n_trophies is not None for g in event.groups):
            seen.add(("group ntrophies",))
        if any(s.n_trophies is not None for s in event.subgroups):
            seen.add(("subgroup ntrophies",))
    assert seen >= {
        ("one-trophy-per", True),
        ("one-trophy-per", False),
        ("n-rank-trophies", 0),
        ("n-rank-trophies", 1),
        ("n-rank-trophies", 3),
        ("full-field-calc", 0),
        ("full-field-calc", -1),
        ("full-field-calc", "aggregate"),
        ("group ntrophies",),
        ("subgroup ntrophies",),
    }


def test_current_speed_award_finds_each_award_by_its_key():
    """`query=award.current` looks an award up by its key among all the
    speed awards. A `full-field-calc` aggregate's award is presented as the
    pack's, under `speed-<place>`, and no name says "(Tie)". A tie of more
    than 26 racers gives keys whose letter is past "z", which PHP's lookup
    cannot read, so those awards are never found."""
    wrong = []
    for fixture, name in compared_award_lists():
        standings, awards = django_awards(fixture, name)
        pack = standings.settings.full_field_calc
        for award in awards:
            key = award.key
            if award.subgroup_id is None and award.group_id == pack:
                key = f"speed-{award.place}"
            expected = CurrentAward(
                key, award.name.removesuffix(" (Tie)"), award.racer_id
            )
            if not re.fullmatch(r"speed-\d+[a-z]?(-\d+){0,2}", award.key):
                expected = None
            found = current_speed_award(award.key, standings, awards)
            if found != expected:
                wrong.append((fixture.stem, name, award.key, found, expected))
    assert wrong == []


def test_current_speed_award_matches_php_award_current():
    """`http/poll/race-day-polls.http` presents `speed-1`. Looked up among
    the speed awards PHP listed then, it is PHP's award."""
    race_day = read_transcript(GOLDEN_DIR / "http" / "poll" / "race-day-polls.http")
    listed = body(race_day["038 08-award-revealed award.list"])
    groups, subgroups = groups_of(listed["classes"])
    standings = Standings(oracle.Event(groups, subgroups, (), (), ()))
    awards = [
        SpeedAward(
            key=a["key"],
            name="",
            place=a["place"],
            sort=0,
            racer_id=a["racerid"],
            group_id=a.get("classid"),
            subgroup_id=a.get("rankid"),
        )
        for a in listed["speed-awards"]
    ]
    car_of = {}

    def find_racers(value):
        if isinstance(value, dict):
            if "racerid" in value and "carnumber" in value:
                assert (
                    car_of.setdefault(value["racerid"], value["carnumber"])
                    == value["carnumber"]
                )
            value = list(value.values())
        if isinstance(value, list):
            for item in value:
                find_racers(item)

    for item in race_day.values():
        if item["body"].startswith((b"{", b"[")):
            find_racers(json.loads(item["body"]))
    for exchange in (
        "035 08-award-presented award.current",
        "037 08-award-revealed award.current",
    ):
        php = body(race_day[exchange])["current"]
        found = current_speed_award(php["key"], standings, awards)
        assert (found.key, found.name, car_of[found.racer_id]) == (
            php["key"],
            php["awardname"],
            php["carnumber"],
        )


def test_php_json_writes_every_fixture_body_as_php_did():
    checked = 0
    for path in sorted((GOLDEN_DIR / "http").rglob("*.http")):
        for item in read_transcript(path).values():
            data = item["body"]
            if item["request"].startswith("SNAPSHOT") or not data.startswith(
                (b"{", b"[")
            ):
                continue
            assert data in (
                php_json(json.loads(data)),
                php_json(json.loads(data)) + b"\n",
            )
            checked += 1
    assert checked > 700


def django_body(fixture: Fixture, exchange: str) -> bytes | None:
    """Django's answer to an `award.list` or `export-json.php` exchange:
    PHP's, with the speed awards replaced by the engine's."""
    item = fixture.exchanges[exchange]
    if item["request"] not in (AWARD_LIST, EXPORT):
        return None
    php = json.loads(item["body"])
    rest = item["body"].removeprefix(php_json(php))
    assert rest in (b"", b"\n")
    standings, awards = django_awards(fixture, exchange)
    if item["request"] == AWARD_LIST:
        php["speed-awards"] = [speed_award_json(a) for a in awards]
    else:
        sheet = dict(php)["Awards"]
        sheet[1 : 1 + len(speed_rows(php))] = awards_sheet_rows(standings, awards)
    return php_json(php) + rest


def raw_headers(path) -> dict[str, bytes]:
    data = path.read_bytes()
    headers = {}
    pos = 0
    while pos < len(data):
        end = data.index(b"\nbody:\n", pos)
        head = data[pos:end]
        name = head.split(b"\n", 1)[0].removeprefix(b"### ").decode()
        headers[name] = head
        length = int(re.search(rb"\nlength: (\d+)", head)[1])
        pos = end + len(b"\nbody:\n") + length + 1
    return headers


def write_q7_deviations():
    fixture = Fixture(Q7)
    headers = raw_headers(GOLDEN_DIR / Q7_FIXTURE)
    out = b""
    differing = []
    for name, item in fixture.exchanges.items():
        django = django_body(fixture, name)
        if django is None or django == item["body"]:
            continue
        differing.append(name)
        head = re.sub(
            rb"\nlength: \d+", f"\nlength: {len(django)}".encode(), headers[name]
        )
        out += head + b"\nbody:\n" + django + b"\n"
    expected = f"deviations/Q7/{Q7}.http"
    path = GOLDEN_DIR / expected
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    entry = {
        "quirk": "Q7",
        "fixture": Q7_FIXTURE,
        "exchanges": differing,
        "reason": Q7_REASON,
        "expected": expected,
    }
    listing = json.loads(DEVIATIONS_FILE.read_text())
    entries = listing["deviations"]
    at = next((i for i, e in enumerate(entries) if e["quirk"] == "Q7"), len(entries))
    listing["deviations"] = [*entries[:at], entry, *entries[at + 1 :]]
    DEVIATIONS_FILE.write_text(json.dumps(listing, indent=2) + "\n")
    print(f"{Q7_FIXTURE}: {differing} differ, written to {expected}")


if __name__ == "__main__":
    write_q7_deviations()
