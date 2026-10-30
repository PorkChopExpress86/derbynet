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

Three Fixes of #20 make Django differ. Q7: PHP gives a tie's place to a
null racer when the tie's first racer already holds a trophy under
`one-trophy-per`, and Django gives it to the next racer of the tie. Q22:
PHP's tie letter runs past "z", and from the 32nd racer the key is not
UTF-8, so `award.list` answers an empty line; Django names those awards
`aa`, `ab`, and on. Q23: time scoring over heats that hold places only
gives a racer a NULL score, and PHP answers 500 or awards from rung -1. The
exchanges where Django differs are listed in `tests/golden/deviations.json`
with Django's exchanges under `tests/golden/deviations/<quirk>/`. To write
both again after a change, run
`uv run python -m racing.engine.tests.test_speed_awards_parity` from
`server/`.
"""

import json
import re
from dataclasses import replace

from racing.engine.standings import oracle
from racing.engine.standings.eligibility import PACK, AwardUnit
from racing.engine.standings.oracle import Standings
from racing.engine.standings.speed_awards import (
    CurrentAward,
    SpeedAward,
    current_speed_award,
    speed_awards,
)

from .golden import (
    GOLDEN_DIR,
    read_transcript,
    write_deviation_entries,
)
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
            assert differs_from_php(fixture, name, django_body(fixture, name)), (
                f"{name} no longer differs from PHP"
            )
    assert wrong == {}
    assert compared == sum(len(Fixture(s).named(AWARD_LIST)) for s in FIXTURES)


def listing_every_partition(event: oracle.Event) -> oracle.Event:
    """The event with a partition per subgroup, and every award unit's list
    narrowed to every partition (D35)."""
    everyone = frozenset(s.id for s in event.subgroups)
    units = [
        PACK,
        *(AwardUnit(group_id=g.id) for g in event.groups),
        *(AwardUnit(subgroup_id=s.id) for s in event.subgroups),
    ]
    return replace(
        event,
        racers=tuple(replace(r, partition_id=r.subgroup_id) for r in event.racers),
        partition_lists=dict.fromkeys(units, everyone),
    )


def test_every_award_list_matches_php_with_every_list_at_all_partitions():
    wrong = []
    for fixture, name in compared_award_lists():
        standings = Standings(listing_every_partition(fixture.event(name)))
        awards = speed_awards(standings, fixture.state(name).trophies).awards
        django = [speed_award_json(a) for a in awards]
        if django != body(fixture.expected(name))["speed-awards"]:
            wrong.append((fixture.stem, name))
    assert wrong == []


def test_every_awards_sheet_matches_php_or_its_deviation():
    for stem in FIXTURES:
        fixture = Fixture(stem)
        for name in fixture.named(EXPORT):
            standings, awards = django_awards(fixture, name)
            django = awards_sheet_rows(standings, awards)
            assert django == speed_rows(body(fixture.expected(name))), (stem, name)
            if name in fixture.deviated:
                assert django != speed_rows(body(fixture.exchanges[name])), name


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
    pack's, under `speed-<place>`, and no name says "(Tie)". The awards of a
    tie of more than 26 racers are found by their `aa`-style letters
    (Q22)."""
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
    """Django's answer to a `query=standings`, `award.list` or
    `export-json.php` exchange: PHP's, with the standings or the speed
    awards replaced by the engine's. Where PHP answered no JSON, the rest of
    the answer comes from another exchange of the same request in the
    transcript, which reads no standings."""
    item = fixture.exchanges[exchange]
    request = item["request"]
    if request not in (STANDINGS, AWARD_LIST, EXPORT):
        return None
    template = item
    if not item["body"].startswith(b"{") and not item["body"].startswith(b"["):
        template = next(
            fixture.exchanges[n]
            for n in fixture.named(request)
            if fixture.exchanges[n]["body"].startswith(b"{")
        )
    php = json.loads(template["body"])
    rest = template["body"].removeprefix(php_json(php))
    assert rest in (b"", b"\n")
    standings, awards = django_awards(fixture, exchange)
    if request == STANDINGS:
        php = {
            "standings": {
                key: [list(rung) for rung in ladder]
                for key, ladder in standings.ladders.items()
            }
        }
    elif request == AWARD_LIST:
        php["speed-awards"] = [speed_award_json(a) for a in awards]
    else:
        sheet = dict(php)["Awards"]
        sheet[1 : 1 + len(speed_rows(php))] = awards_sheet_rows(standings, awards)
    return php_json(php) + rest


def differs_from_php(fixture: Fixture, exchange: str, django: bytes) -> bool:
    item = fixture.exchanges[exchange]
    if item["status"] != "200" or not item["body"].startswith((b"{", b"[")):
        return True
    if item["request"] == STANDINGS:
        return json.loads(django) != json.loads(item["body"])
    return django != item["body"]


def quirk_of(fixture: Fixture, exchange: str) -> str:
    """Why Django's exchange differs from PHP's (#20)."""
    if fixture.stem == Q7:
        return "Q7"
    standings, _ = django_awards(fixture, exchange)
    if any(r.summary.score is None for r in standings.rows):
        return "Q23"
    if any(len(rung) > 26 for ladder in standings.ladders.values() for rung in ladder):
        return "Q22"
    raise AssertionError(f"{fixture.stem} {exchange} differs from PHP")


REASONS = {
    "Q7": Q7_REASON,
    "Q22": (
        "Django names the 27th award of a tie and on aa, ab, and on; PHP's "
        "letter runs past z, and from the 32nd its key is not UTF-8."
    ),
    "Q23": (
        "Django gives a racer with no score in time scoring no place; PHP "
        "throws a TypeError or files the racer under rung -1."
    ),
}


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


def DEVIATION_CLAIM(quirk: str, fixture: str) -> bool:  # noqa: N802
    """Each quirk of REASONS in each standings transcript: which exchanges
    differ is known only once the writer runs."""
    return quirk in REASONS and fixture.startswith("http/standings/")


def write_standings_deviations():
    """Every standings transcript exchange where Django differs, listed
    under its quirk, with Django's exchanges under `deviations/<quirk>/`."""
    written: dict[tuple[str, str], tuple[list[str], bytes]] = {}
    for path in sorted(STANDINGS_DIR.glob("*.http")):
        if path.stem in ("award-settings", "edge-cases-setup"):
            continue
        fixture = Fixture(path.stem)
        headers = raw_headers(path)
        for name in fixture.exchanges:
            django = django_body(fixture, name)
            if django is None or not differs_from_php(fixture, name, django):
                continue
            head = re.sub(
                rb"\nlength: \d+", f"\nlength: {len(django)}".encode(), headers[name]
            )
            head = re.sub(rb"\nstatus: \d+", b"\nstatus: 200", head)
            names, out = written.get((fixture.stem, quirk_of(fixture, name)), ([], b""))
            written[(fixture.stem, quirk_of(fixture, name))] = (
                [*names, name],
                out + head + b"\nbody:\n" + django + b"\n",
            )
    entries = []
    for (stem, quirk), (names, out) in sorted(written.items()):
        expected = f"deviations/{quirk}/{stem}.http"
        path = GOLDEN_DIR / expected
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(out)
        entries.append(
            {
                "quirk": quirk,
                "fixture": f"http/standings/{stem}.http",
                "exchanges": names,
                "reason": REASONS[quirk],
                "expected": expected,
            }
        )
        print(f"{quirk} {stem}: {len(names)} exchanges, written to {expected}")
    write_deviation_entries(DEVIATION_CLAIM, entries)


if __name__ == "__main__":
    write_standings_deviations()
