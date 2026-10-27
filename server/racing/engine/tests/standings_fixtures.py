"""The PHP standings transcripts under `tests/golden/http/standings/` as
engine events, with every id mapped through the fixtures (ADR-12).

A transcript records PHP's answers, not the rows behind them, so the event
is rebuilt from what the fixtures do show:

- Racers, from the Roster sheet of `export-json.php`: names, car number,
  group and subgroup by name, and `Excluded?`. The sheet lists only racers
  who passed check-in, as the standings read them.
- Results, from the Results sheet: group and round number, then car number
  and name, time and place. The sheet casts a missing time to 0, so a time
  of 0 is no time.
- Group and subgroup ids, names, order, aggregates and trophy counts, from
  the `classes` list that `class.list` and `award.list` return.
- Racer ids, from `racer.list` where the scenario captured it, else from the
  `.by-car.json` file, which names each racer of the as-left standings
  ladders by car number and name, rung by rung.
- Round ids, by matching each `st-r<roundid>` ladder's racers with the
  racers who have a result in a group's round, checked against the round
  table of the suite transcript in `ROUND_TABLE` where there is one. That
  table also holds the rounds with a roster and no heats yet, which no
  export sheet shows. A round no fixture names gets an id above PHP's.
- Settings, from the capture scenarios (`capture/scenarios/`,
  `standings_matrix` in `capture/capture-http.sh`) and the `testing/`
  scripts they run. `AS_LEFT` holds what each script leaves set.

`q13-drop-slowest` has no export; its uploaded snapshot holds the tables,
ids included.
"""

import json
import re
from dataclasses import dataclass, field, replace
from functools import cached_property

from racing.engine.standings import oracle
from racing.engine.standings.eligibility import AwardSettings
from racing.engine.standings.scoring import Scoring
from racing.engine.standings.speed_awards import TrophySettings

from .golden import GOLDEN_DIR, deviations, read_transcript

STANDINGS_DIR = GOLDEN_DIR / "http" / "standings"
STANDINGS = "GET action.php?query=standings"
EXPORT = "GET export-json.php"
SNAPSHOT = "q13-drop-slowest"
SYNTHETIC_ID_BASE = 1000


@dataclass(frozen=True)
class State:
    """The settings an exchange ran under. A script that sets
    `full-field-calc` to an aggregate names it in `full_field_calc_group`,
    and its id comes from the fixture's groups. Group and subgroup trophy
    counts are in the `classes` lists. `time_digits` is the
    `finish-formatting` precision."""

    use_points: bool = False
    scoring: Scoring = Scoring.AVERAGE
    use_subgroups: bool = False
    lane_count: int = 0
    full_field_calc: int = 0
    full_field_calc_group: str | None = None
    trophies: TrophySettings = TrophySettings()
    time_digits: int = 3

    def with_trophies(self, **changes) -> State:
        return replace(self, trophies=replace(self.trophies, **changes))


# What each script leaves set when `standings_matrix` or the scenario reads
# the standings. Unlisted settings keep the defaults of a fresh database.
AS_LEFT = {
    # 07-standings-edge-cases.sh: n-lanes=2.
    "edge-cases-six-heats-of-nine": State(lane_count=2),
    "edge-cases-all-heats": State(lane_count=2),
    # test-basic-racing.sh sets n-lanes=4 and one-trophy-per=1;
    # test-awards.sh sets use-subgroups.
    "suite-basic-racing-and-awards": State(
        lane_count=4, use_subgroups=True, trophies=TrophySettings(one_trophy_per=True)
    ),
    "suite-basic-racing-after-new-rounds": State(
        lane_count=4, use_subgroups=True, trophies=TrophySettings(one_trophy_per=True)
    ),
    # test-standing-by-rank.sh: use-subgroups, n-lanes=4, n-rank-trophies=3.
    "suite-standing-by-rank": State(
        lane_count=4, use_subgroups=True, trophies=TrophySettings(n_subgroup=3)
    ),
    # test-subgroup-ntrophies.sh uploads these preferences.
    "suite-subgroup-ntrophies": State(
        lane_count=4,
        use_subgroups=True,
        trophies=TrophySettings(n_pack=0, n_group=0, n_subgroup=3),
        time_digits=4,
    ),
    # test-points-racing.sh: use-points, n-lanes=4, one-trophy-per=1.
    "suite-points-racing": State(
        use_points=True, lane_count=4, trophies=TrophySettings(one_trophy_per=True)
    ),
    # test-playlist.sh: n-lanes=2.
    "suite-playlist": State(lane_count=2),
    # test-master-schedule.sh: n-lanes=4; test-awards.sh: use-subgroups.
    "suite-master-schedule": State(lane_count=4, use_subgroups=True),
    # test-aggregate-rounds.sh: use-subgroups; its last roster.new creates
    # Final Finals, which writes full-field-calc (action.roster.new.inc).
    "suite-aggregate-rounds": State(
        use_subgroups=True, full_field_calc_group="Final Finals"
    ),
    # test-aggregate-classes.sh: use-subgroups.
    "suite-aggregate-classes": State(use_subgroups=True),
    # test-aggregate-nonracing.sh: n-lanes=4.
    "suite-aggregate-nonracing": State(lane_count=4),
    # 12-standings-quirks.sh.
    "q7-tie-after-earlier-trophy": State(
        lane_count=2,
        use_subgroups=True,
        trophies=TrophySettings(n_pack=0, n_group=1, one_trophy_per=True),
    ),
    "award-settings": State(lane_count=2, use_subgroups=True),
    "q13-drop-slowest": State(lane_count=2),
}

# The suite transcript whose last `rounds` list is the round table the
# standings read. A round with a roster and no heats yet is in no export
# sheet, and it still decides which round is a group's final round.
ROUND_TABLE = {
    "suite-basic-racing-after-new-rounds": "new-rounds-basic",
    "suite-master-schedule": "master-schedule",
    "suite-aggregate-rounds": "aggregate-rounds",
    "suite-aggregate-classes": "aggregate-classes",
}

SETUP = {
    "edge-cases-six-heats-of-nine": "edge-cases-setup",
    "edge-cases-all-heats": "edge-cases-setup",
}


def standings_transcripts() -> list[str]:
    return [
        path.stem
        for path in sorted(STANDINGS_DIR.glob("*.http"))
        if any(e["request"] == STANDINGS for e in read_transcript(path).values())
    ]


def label(exchange: str) -> str:
    words = exchange.split(" ")[1:-1]
    return " ".join(words)


def body(exchange: dict):
    return json.loads(exchange["body"])


def matrix_states(left: State) -> dict[str, State]:
    """`standings_matrix` in `capture-http.sh`, label by label."""
    states = {"as-the-script-left-it": left}
    for points in (0, 1):
        for scoring in (0, 1, 2):
            for subgroups in (0, 1):
                states[f"points={points} scoring={scoring} subgroups={subgroups}"] = (
                    replace(
                        left,
                        use_points=bool(points),
                        scoring=Scoring(scoring),
                        use_subgroups=bool(subgroups),
                    )
                )
    after = replace(
        left, use_points=False, scoring=Scoring.AVERAGE, use_subgroups=False
    )
    for one in (0, 1):
        states[f"one-trophy-per={one}"] = after.with_trophies(one_trophy_per=bool(one))
    after = after.with_trophies(one_trophy_per=False)
    for n in (0, 1, 3):
        states[f"n-rank-trophies={n}"] = after.with_trophies(n_subgroup=n)
    return states


def award_settings_states(exchanges: dict[str, dict], left: State) -> dict[str, State]:
    """`12-standings-quirks.sh`'s award settings, each kept for the
    exchanges after it."""
    states = {}
    state = left
    for name, exchange in exchanges.items():
        calc = re.search(r"full-field-calc=(-?\d+)", exchange.get("request-body", ""))
        if calc:
            state = replace(state, full_field_calc=int(calc[1]))
        one = re.fullmatch(r"one-trophy-per=(\d)", label(name))
        if one:
            state = state.with_trophies(one_trophy_per=one[1] == "1")
        states[label(name)] = state
    return states


@dataclass
class _Round:
    group: str
    number: int
    finished: bool = True
    racers: set[str] = field(default_factory=set)


class Fixture:
    def __init__(self, stem: str):
        self.stem = stem
        self.exchanges = read_transcript(STANDINGS_DIR / f"{stem}.http")
        self.deviated = deviated_exchanges(f"http/standings/{stem}.http")
        self._left = AS_LEFT[stem]
        self._states = _states(stem, self.exchanges, self._left)
        self._roster: dict[str, dict] = {}
        self._rounds: dict[tuple[str, int], _Round] = {}
        self._heats: list[tuple[_Round, str, float | None, int | None]] = []
        self._known_ids: dict[str, int] = {}
        self._round_table: list[dict] | None = None
        self._classes: list[dict] = []
        if stem == SNAPSHOT:
            self._read_snapshot()
        else:
            self._read_export()
        if stem in ROUND_TABLE:
            suite = GOLDEN_DIR / "http" / "suite" / f"{ROUND_TABLE[stem]}.http"
            self._round_table = [
                data["rounds"]
                for item in read_transcript(suite).values()
                if isinstance(data := _json_object(item), dict) and "rounds" in data
            ][-1]

    def expected(self, exchange: str) -> dict:
        """What Django must answer: PHP's exchange, or its deviation (D25)."""
        return self.deviated.get(exchange, self.exchanges[exchange])

    def named(self, request: str) -> list[str]:
        return [n for n, e in self.exchanges.items() if e["request"] == request]

    def state(self, exchange: str) -> State:
        return self._states.get(label(exchange), self._left)

    def classes(self, exchange: str | None = None) -> list[dict]:
        lists = [
            (_number(name), data["classes"])
            for name, item in self.exchanges.items()
            if isinstance(data := _json_object(item), dict) and "classes" in data
        ]
        if not lists:
            return self._classes
        number = _number(exchange) if exchange else 0
        before = [classes for n, classes in lists if n <= number]
        return before[-1] if before else lists[0][1]

    def event(self, exchange: str) -> oracle.Event:
        state = self.state(exchange)
        classes = self.classes(exchange)
        groups, subgroups = groups_of(classes)
        full_field_calc = state.full_field_calc
        if state.full_field_calc_group is not None:
            (full_field_calc,) = [
                c["classid"]
                for c in classes
                if c["name"] == state.full_field_calc_group
            ]
        return oracle.Event(
            groups=groups,
            subgroups=subgroups,
            rounds=self.rounds,
            racers=tuple(
                oracle.Racer(
                    id=self._racer_id(r["key"]),
                    first_name=r["first"],
                    last_name=r["last"],
                    car_number=r["car"],
                    car_name=r["car_name"],
                    group_id=(where := _membership(r["groups"], classes))[0],
                    subgroup_id=where[1],
                    awards=AwardSettings(not_eligible_for_any_award=r["excluded"]),
                )
                for r in self._roster.values()
            ),
            results=self.results,
            settings=oracle.Settings(
                use_points=state.use_points,
                scoring=state.scoring,
                lane_count=state.lane_count,
                use_subgroups=state.use_subgroups,
                full_field_calc=full_field_calc,
            ),
        )

    def car_of(self, racer_id: int) -> str:
        return {v: k for k, v in self.racer_ids.items()}.get(racer_id, str(racer_id))

    def _read_export(self) -> None:
        export = body(self.exchanges[self.named(EXPORT)[0]])
        sheets = dict(export[:2])
        roster, results = sheets["Roster"], sheets["Results"]
        header = roster[0]
        car = header.index("Car Number")
        excluded = header.index("Excluded?")
        for row in roster[1:]:
            self._add_racer(
                row[0],
                row[1],
                row[car],
                row[car + 1],
                row[car + 2 : excluded],
                bool(row[excluded]),
            )
        col = {h: i for i, h in enumerate(results[0])}
        for row in results[1:]:
            self._add_heat(
                row[col["Class"]],
                row[col["Round"]],
                racer_key(
                    row[col["CarNumber"]], row[col["FirstName"]], row[col["LastName"]]
                ),
                row[col["FinishTime"]] or None,
                row[col["FinishPlace"]],
            )

    def _read_snapshot(self) -> None:
        text = (STANDINGS_DIR / f"{SNAPSHOT}.snapshot.xml").read_text()
        cdata = text.split("<![CDATA[", 1)[1].rsplit("]]>", 1)[0]
        tables = {t["table"]: t["rows"] for t in json.loads(cdata)}
        groups = {c["classid"]: c["class"] for c in tables["Classes"]}
        subgroups = {r["rankid"]: r["rank"] for r in tables["Ranks"]}
        self._classes = [
            {
                "classid": c["classid"],
                "name": c["class"],
                "ntrophies": -1,
                "subgroups": [
                    {"rankid": r["rankid"], "name": r["rank"], "ntrophies": -1}
                    for r in tables["Ranks"]
                    if r["classid"] == c["classid"]
                ],
            }
            for c in tables["Classes"]
        ]
        keys = {}
        for r in tables["RegistrationInfo"]:
            keys[r["racerid"]] = racer_key(
                r["carnumber"], r["firstname"], r["lastname"]
            )
            if r["passedinspection"]:
                self._known_ids[keys[r["racerid"]]] = r["racerid"]
                self._add_racer(
                    r["firstname"],
                    r["lastname"],
                    r["carnumber"],
                    r["carname"],
                    [groups[r["classid"]], subgroups[r["rankid"]]],
                    bool(r["exclude"]),
                )
        rounds = {r["roundid"]: r for r in tables["Rounds"]}
        self._round_table = [
            {
                "class": groups[r["classid"]],
                "round": r["round"],
                "roundid": r["roundid"],
            }
            for r in tables["Rounds"]
        ]
        for heat in tables["RaceChart"]:
            if heat["racerid"] is not None:
                round = rounds[heat["roundid"]]
                self._add_heat(
                    groups[round["classid"]],
                    round["round"],
                    keys[heat["racerid"]],
                    heat["finishtime"],
                    heat["finishplace"],
                )

    def _add_racer(self, first, last, car, car_name, groups, excluded) -> None:
        key = racer_key(car, first, last)
        assert key not in self._roster, (self.stem, key)
        self._roster[key] = {
            "key": key,
            "first": first,
            "last": last,
            "car": car,
            "car_name": car_name,
            "groups": groups,
            "excluded": excluded,
        }

    def _add_heat(self, group: str, number: int, key: str, time, place) -> None:
        round = self._rounds.setdefault((group, number), _Round(group, number))
        if time is None and place is None:
            round.finished = False
        elif key in self._roster:
            round.racers.add(key)
            self._heats.append((round, key, time, place))

    @cached_property
    def racer_ids(self) -> dict[str, int]:
        ids = dict(self._known_ids)

        def add(key, racer_id):
            assert ids.setdefault(key, racer_id) == racer_id, (self.stem, key)

        stems = [self.stem] + ([SETUP[self.stem]] if self.stem in SETUP else [])
        for stem in stems:
            for item in read_transcript(STANDINGS_DIR / f"{stem}.http").values():
                if item["request"].startswith("GET action.php?query=racer.list"):
                    for r in body(item)["racers"]:
                        key = racer_key(r["carnumber"], r["firstname"], r["lastname"])
                        add(key, r["racerid"])
        by_car = STANDINGS_DIR / f"{self.stem}.by-car.json"
        if by_car.exists():
            named = json.loads(by_car.read_text())
            (left,) = [
                n for n in self.named(STANDINGS) if label(n) == "as-the-script-left-it"
            ]
            ladders = body(self.exchanges[left])["standings"]
            assert named.keys() == ladders.keys()
            for key, ladder in ladders.items():
                for rung, names in zip(ladder, named[key], strict=True):
                    for racer_id, name in zip(rung, names, strict=True):
                        add(name, racer_id)
        return ids

    def _racer_id(self, key: str) -> int:
        if key in self.racer_ids:
            return self.racer_ids[key]
        unnamed = [k for k in self._roster if k not in self.racer_ids]
        top = max(self.racer_ids.values(), default=0)
        return SYNTHETIC_ID_BASE + top + unnamed.index(key)

    @cached_property
    def round_ids(self) -> dict[tuple[str, int], int]:
        by_id = {v: k for k, v in self.racer_ids.items()}
        named: dict[int, set[str]] = {}
        for name in self.named(STANDINGS):
            if self.exchanges[name]["status"] != "200":
                continue
            for key, ladder in body(self.exchanges[name])["standings"].items():
                if found := re.fullmatch(r"st-r(\d+)", key):
                    rungs = ladder.values() if isinstance(ladder, dict) else ladder
                    named.setdefault(int(found[1]), set()).update(
                        by_id[r] for rung in rungs for r in rung
                    )
        excluded = {k for k, r in self._roster.items() if r["excluded"]}
        ids = {}
        for round_id, racers in named.items():
            (where,) = [
                k for k, r in self._rounds.items() if r.racers - excluded == racers
            ]
            ids[where] = round_id
        if self._round_table is not None:
            for r in self._round_table:
                where = (r["class"], r["round"])
                assert ids.setdefault(where, r["roundid"]) == r["roundid"], where
                self._rounds.setdefault(where, _Round(*where, finished=False))
            assert len(self._rounds) == len(self._round_table), self.stem
        else:
            # Without a round table: PHP creates round 1 with every group
            # that has subgroups (`create_class` in class_add_or_delete.inc).
            for c in self.classes():
                if c["subgroups"]:
                    where = (c["name"], 1)
                    self._rounds.setdefault(where, _Round(*where, finished=False))
        unnamed = [k for k in self._rounds if k not in ids]
        top = max(ids.values(), default=0)
        for i, where in enumerate(unnamed):
            ids[where] = SYNTHETIC_ID_BASE + top + i
        return ids

    @cached_property
    def rounds(self) -> tuple[oracle.Round, ...]:
        ids = self.round_ids
        group_ids = {c["name"]: c["classid"] for c in self.classes()}
        return tuple(
            oracle.Round(ids[k], group_ids[r.group], r.number, r.finished)
            for k, r in self._rounds.items()
        )

    @cached_property
    def results(self) -> tuple[oracle.Result, ...]:
        ids = self.round_ids
        return tuple(
            oracle.Result(ids[(r.group, r.number)], self._racer_id(key), time, place)
            for r, key, time, place in self._heats
        )


def _states(stem: str, exchanges: dict[str, dict], left: State) -> dict[str, State]:
    if stem == "award-settings":
        return award_settings_states(exchanges, left)
    if stem == SNAPSHOT:
        # PHP turns the uploaded drop-slowest into scoring=1 as it reads the
        # standings, and Django's importer does it once (Q13), so both read
        # the standings after the upload with scoring=1.
        after = replace(left, scoring=Scoring.DROP_SLOWEST)
        return {label("004 standings after the snapshot"): after}
    if any(label(n) == "as-the-script-left-it" for n in exchanges):
        return matrix_states(left)
    return {}


def racer_key(car, first: str, last: str) -> str:
    """How `standings_by_car` names a racer: `"101 Ann Lee"`."""
    return f"{car} {first} {last}"


def _number(exchange: str) -> int:
    return int(exchange.split(" ", 1)[0])


def _json_object(item: dict):
    if not item["body"].strip().startswith((b"{", b"[")):
        return None
    return json.loads(item["body"])


def _ntrophies(value: int) -> int | None:
    return None if value < 0 else value


def groups_of(classes: list[dict]):
    groups = tuple(
        oracle.Group(
            id=c["classid"],
            name=c["name"],
            sort_order=i,
            n_trophies=_ntrophies(c["ntrophies"]),
            constituents=tuple(int(x["classid"]) for x in c.get("constituents", ())),
            subgroup_ids=tuple(
                int(x["rankid"]) for x in c.get("constituent-ranks", ())
            ),
        )
        for i, c in enumerate(classes)
    )
    subgroups = tuple(
        oracle.Subgroup(
            id=s["rankid"],
            group_id=c["classid"],
            name=s["name"],
            sort_order=i,
            n_trophies=_ntrophies(s["ntrophies"]),
        )
        for c in classes
        for i, s in enumerate(c["subgroups"])
    )
    return groups, subgroups


def _membership(names: list[str], classes: list[dict]) -> tuple[int, int]:
    """A racer's group and subgroup ids from the Roster sheet's group and
    subgroup columns. The sheet shows the group column when there is more
    than one group, and the subgroup column with subgroups on."""
    racing = [c for c in classes if c["subgroups"]]
    if len(names) == 2:
        (group,) = [c for c in classes if c["name"] == names[0]]
        (subgroup,) = [s for s in group["subgroups"] if s["name"] == names[1]]
        return group["classid"], subgroup["rankid"]
    if len(names) == 1 and len(classes) > 1:
        (group,) = [c for c in classes if c["name"] == names[0]]
        (subgroup,) = group["subgroups"]
        return group["classid"], subgroup["rankid"]
    if len(names) == 1:
        (group,) = racing
        (subgroup,) = [s for s in group["subgroups"] if s["name"] == names[0]]
        return group["classid"], subgroup["rankid"]
    assert not names, names
    (group,) = racing
    (subgroup,) = group["subgroups"]
    return group["classid"], subgroup["rankid"]


def deviated_exchanges(fixture: str) -> dict[str, dict]:
    found = {}
    for entry in deviations():
        if entry["fixture"] == fixture:
            expected = read_transcript(GOLDEN_DIR / entry["expected"])
            for name in entry["exchanges"]:
                found[name] = expected[name]
    return found


def php_json(value) -> bytes:
    """`json_encode($value, JSON_PRETTY_PRINT)` as PHP writes it: four
    spaces, non-ASCII as `\\uXXXX`, and `/` escaped. Every JSON body under
    `tests/golden/http/` reads back to itself through this, which
    `test_standings_parity.py` checks."""
    return json.dumps(value, indent=4, ensure_ascii=True).replace("/", "\\/").encode()
