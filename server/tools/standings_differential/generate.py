"""Seeded random events in the shape `harness.php` loads: PHP's tables as
JSON, the RaceInfo settings, the finalist requests to replay, and extra
`current_award_key` values to resolve.
"""

import random
from dataclasses import dataclass, replace

LAST_NAMES = (
    "Ames",
    "ames",
    "AMES",
    "Bell",
    "bell",
    "Cole",
    "Dunn",
    "Éclair",
    "éclair",
    "Eclair",
    "Zoë",
    "Zoe",
    "Ångström",
    "Østby",
    "Mañana",
    "Smith",
    "smith",
    "O'Neil",
    "Van Dyke",
    "van dyke",
)
PLAIN_LAST_NAMES = ("Ames", "Bell", "Cole", "Dunn", "Ford", "Gray", "Hale", "Ives")
FIRST_NAMES = ("Al", "al", "Bo", "Cy", "Émile", "emile", "Zoë", "Li", "Ann", "ann")
PLAIN_FIRST_NAMES = ("Al", "Bo", "Cy", "Di", "Ed", "Flo")
GROUP_NAMES = (
    "Lions",
    "Tigers",
    "Wolves",
    "Bears",
    "Webelos",
    "Arrow of Light",
    "Élan",
    "élan",
    "Zebras",
    "alpha",
    "Beta",
)
AGGREGATE_NAMES = ("Grand Finals", "Finals", "Open", "Champions", "Siblings")
SUBGROUP_NAMES = ("A", "B", "C", "Den 1", "den 1", "Pack", "Élite", "élite", "Zed")
SUPERGROUP_LABELS = ("Pack", "Troop", "Club", "Élan Pack")
TIE_PALETTE = (3.0, 3.1, 3.2, 3.125, 3.25)


@dataclass(frozen=True)
class Knobs:
    use_points: float = 0.25
    scoring: tuple[int, ...] = (0, 1, 2)
    use_subgroups: float = 0.5
    groups: tuple[int, int] = (1, 4)
    subgroups: tuple[int, int] = (1, 3)
    racers: tuple[int, int] = (2, 9)
    lanes: tuple[int, ...] = (2, 3, 4, 6)
    tie_heavy: float = 0.5
    one_heat: float = 0.15
    place_only_lane: float = 0.04
    not_passed: float = 0.08
    excluded: float = 0.08
    chart_not_roster: float = 0.15
    unfinished_first_round: float = 0.08
    follow_on: float = 0.35
    aggregates: tuple[int, int] = (0, 3)
    subgroup_aggregate: float = 0.35
    aggregate_of_aggregates: float = 0.3
    racing_aggregate: float = 0.5
    one_trophy_per: float = 0.5
    full_field_calc: tuple[str, ...] = ("0", "-1", "aggregate")
    ntrophies_override: float = 0.2
    trophy_settings: float = 0.4
    case_names: float = 0.5
    accented_names: float = 0.3
    shared_sort_order: float = 0.15
    permuted_times: float = 0.1
    requests: int = 6


BATCHES: dict[str, dict] = {
    "ties": {"tie_heavy": 1.0},
    "scoring-0": {"scoring": (0,)},
    "scoring-1-one-heat": {"scoring": (1,), "one_heat": 0.5},
    "scoring-2": {"scoring": (2,)},
    "points": {"use_points": 1.0},
    "subgroups-on": {"use_subgroups": 1.0, "subgroups": (2, 3)},
    "subgroups-off": {"use_subgroups": 0.0, "subgroups": (2, 3)},
    "many-groups-subgroups": {"groups": (3, 5), "subgroups": (2, 3)},
    "aggregate-by-group": {"aggregates": (1, 3), "subgroup_aggregate": 0.0},
    "aggregate-by-subgroup": {"aggregates": (1, 3), "subgroup_aggregate": 1.0},
    "racing-aggregates": {"aggregates": (1, 3), "racing_aggregate": 1.0},
    "nonracing-aggregates": {"aggregates": (1, 3), "racing_aggregate": 0.0},
    "aggregates-of-aggregates": {
        "groups": (2, 4),
        "aggregates": (2, 4),
        "aggregate_of_aggregates": 1.0,
    },
    "follow-on": {"follow_on": 1.0},
    "not-passed-excluded": {"not_passed": 0.3, "excluded": 0.3},
    "chart-not-roster": {"chart_not_roster": 1.0},
    "case-names": {"case_names": 1.0, "accented_names": 0.0, "tie_heavy": 1.0},
    "accented-names": {"accented_names": 1.0, "tie_heavy": 1.0},
    "one-trophy-per-on": {"one_trophy_per": 1.0},
    "one-trophy-per-off": {"one_trophy_per": 0.0},
    "full-field-calc-0": {"full_field_calc": ("0",)},
    "full-field-calc--1": {"full_field_calc": ("-1",)},
    "full-field-calc-aggregate": {
        "full_field_calc": ("aggregate",),
        "aggregates": (1, 3),
    },
    "ntrophies-overrides": {"ntrophies_override": 0.8},
    "trophy-settings": {"trophy_settings": 1.0},
    "shared-sort-orders": {"shared_sort_order": 1.0},
    "permuted-times": {"permuted_times": 1.0, "lanes": (4, 6), "use_points": 0.0},
    "q7": {
        "one_trophy_per": 1.0,
        "tie_heavy": 1.0,
        "aggregates": (1, 3),
        "subgroup_aggregate": 0.7,
        "racers": (3, 6),
    },
}


def knobs_for(batch: str | None) -> Knobs:
    return replace(Knobs(), **BATCHES[batch]) if batch else Knobs()


class _Builder:
    def __init__(self, rng: random.Random, knobs: Knobs):
        self.rng = rng
        self.k = knobs
        self.classes: list[dict] = []
        self.ranks: list[dict] = []
        self.racers: list[dict] = []
        self.rounds: list[dict] = []
        self.roster: list[dict] = []
        self.chart: list[dict] = []
        self.next_round = rng.randrange(1, 20)
        self.use_points = rng.random() < knobs.use_points
        self.lanes = rng.choice(knobs.lanes)

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def ntrophies(self, high: int) -> int:
        return (
            self.rng.randrange(0, high + 1)
            if self.chance(self.k.ntrophies_override)
            else -1
        )

    def sort_order(self, i: int) -> int:
        return self.rng.randrange(1, 3) if self.chance(self.k.shared_sort_order) else i

    def add_groups(self) -> None:
        rng = self.rng
        n = rng.randint(*self.k.groups)
        names = rng.sample(GROUP_NAMES, n)
        ids = rng.sample(range(1, 40), n)
        rank_ids = iter(rng.sample(range(100, 400), n * self.k.subgroups[1]))
        racer_ids = iter(rng.sample(range(1000, 3000), n * self.k.racers[1]))
        car_numbers = iter(rng.sample(range(100, 999), n * self.k.racers[1]))
        if self.chance(self.k.accented_names):
            last_pool = LAST_NAMES
            first_pool = FIRST_NAMES
        elif self.chance(self.k.case_names):
            last_pool = tuple(n for n in LAST_NAMES if n.isascii())
            first_pool = tuple(n for n in FIRST_NAMES if n.isascii())
        else:
            last_pool, first_pool = PLAIN_LAST_NAMES, PLAIN_FIRST_NAMES
        for i, (classid, name) in enumerate(zip(ids, names, strict=True), 1):
            self.classes.append(
                {
                    "classid": classid,
                    "class": name,
                    "sortorder": self.sort_order(i),
                    "ntrophies": self.ntrophies(4),
                    "constituents": [],
                    "rankids": [],
                }
            )
            ranks = []
            for j in range(rng.randint(*self.k.subgroups)):
                rank = {
                    "rankid": next(rank_ids),
                    "classid": classid,
                    "rank": rng.choice(SUBGROUP_NAMES),
                    "sortorder": self.sort_order(j + 1),
                    "ntrophies": self.ntrophies(3),
                }
                ranks.append(rank)
                self.ranks.append(rank)
            for _ in range(rng.randint(*self.k.racers)):
                self.racers.append(
                    {
                        "racerid": next(racer_ids),
                        "firstname": rng.choice(first_pool),
                        "lastname": rng.choice(last_pool),
                        "carnumber": next(car_numbers),
                        "carname": rng.choice(["", "Zoom", "Bolt"]),
                        "classid": classid,
                        "rankid": rng.choice(ranks)["rankid"],
                        "passedinspection": 0 if self.chance(self.k.not_passed) else 1,
                        "exclude": 1 if self.chance(self.k.excluded) else 0,
                    }
                )

    def racing_groups(self) -> list[dict]:
        return [c for c in self.classes if not c["constituents"]]

    def racers_under(self, classid: int) -> list[dict]:
        """The racers an aggregate draws from, through nested aggregates,
        each once: Django's roster holds a racer once per round, where PHP
        can enter a racer who qualifies from two constituents twice."""
        group = next(c for c in self.classes if c["classid"] == classid)
        if not group["constituents"]:
            return [r for r in self.racers if r["classid"] == classid]
        found = []
        for c in group["constituents"]:
            found += [r for r in self.racers_under(c) if r not in found]
        if group["rankids"]:
            found = [r for r in found if r["rankid"] in group["rankids"]]
        return found

    def add_aggregates(self) -> None:
        rng = self.rng
        used = {c["class"].lower() for c in self.classes}
        names = [n for n in AGGREGATE_NAMES if n.lower() not in used]
        free_ids = [
            i for i in range(1, 60) if i not in {c["classid"] for c in self.classes}
        ]
        for _ in range(rng.randint(*self.k.aggregates)):
            racing = self.racing_groups()
            if self.chance(self.k.subgroup_aggregate):
                rankids = [
                    r["rankid"]
                    for r in rng.sample(self.ranks, rng.randint(1, len(self.ranks)))
                ]
                constituents = []
                for rank in self.ranks:
                    if (
                        rank["rankid"] in rankids
                        and rank["classid"] not in constituents
                    ):
                        constituents.append(rank["classid"])
            else:
                rankids = []
                pool = list(racing)
                aggregates = [c for c in self.classes if c["constituents"]]
                if aggregates and self.chance(self.k.aggregate_of_aggregates):
                    chosen = [rng.choice(aggregates)]
                    pool += aggregates
                    pool.remove(chosen[0])
                else:
                    chosen = []
                chosen += rng.sample(pool, rng.randint(0 if chosen else 1, len(pool)))
                constituents = [c["classid"] for c in chosen]
            classid = free_ids.pop(rng.randrange(len(free_ids)))
            self.classes.append(
                {
                    "classid": classid,
                    "class": names.pop(0),
                    "sortorder": self.sort_order(len(self.classes) + 1),
                    "ntrophies": self.ntrophies(4),
                    "constituents": constituents,
                    "rankids": rankids,
                }
            )
            if self.chance(self.k.racing_aggregate):
                pool = self.racers_under(classid)
                if pool:
                    entrants = rng.sample(pool, rng.randint(1, min(len(pool), 8)))
                    self.add_rounds(classid, entrants)

    def time(self, tie_heavy: bool) -> float:
        rng = self.rng
        if self.chance(0.02):
            return 9.999
        if tie_heavy:
            return rng.choice(TIE_PALETTE)
        return round(rng.uniform(2.5, 4.5), rng.choice((1, 2, 3, 4)))

    def add_round(
        self, classid: int, number: int, entrants: list[dict], state: str
    ) -> None:
        rng = self.rng
        roundid = self.next_round
        self.next_round += rng.randint(1, 3)
        self.rounds.append({"roundid": roundid, "classid": classid, "round": number})
        for racer in entrants:
            self.roster.append({"roundid": roundid, "racerid": racer["racerid"]})
        if state == "unscheduled" or not entrants:
            return
        runners = list(entrants)
        if self.chance(self.k.chart_not_roster):
            outsiders = [r for r in self.racers if r not in entrants]
            if outsiders:
                runners.append(rng.choice(outsiders))
        runs = rng.randint(1, max(1, min(self.lanes, 4)))
        slots = []
        for run in range(runs):
            shift = run % len(runners)
            slots += runners[shift:] + runners[:shift]
        if self.chance(self.k.one_heat):
            for racer in rng.sample(runners, max(1, len(runners) // 3)):
                first = slots.index(racer)
                slots = [s for i, s in enumerate(slots) if s is not racer or i == first]
        width = min(self.lanes, len(runners))
        tie_heavy = self.chance(self.k.tie_heavy)
        # Every racer runs the same times in another order, so a sum that
        # depends on the order splits ties that PHP's SUM keeps.
        permuted = self.chance(self.k.permuted_times)
        base = [round(rng.uniform(2.5, 4.5), rng.choice((3, 4))) for _ in range(runs)]
        own_times = {r["racerid"]: rng.sample(base, len(base)) for r in runners}
        heats = []
        while slots:
            heat, rest = [], []
            for racer in slots:
                if len(heat) < width and racer not in heat:
                    heat.append(racer)
                else:
                    rest.append(racer)
            heats.append(heat)
            slots = rest
        rows = []
        for h, heat in enumerate(heats, 1):
            if permuted:
                times = [own_times[r["racerid"]].pop() for r in heat]
            else:
                times = [self.time(tie_heavy) for _ in heat]
            ranked = sorted(range(len(heat)), key=lambda i: (times[i], rng.random()))
            places = [0] * len(heat)
            for p, i in enumerate(ranked, 1):
                places[i] = p
            for lane, (racer, t, p) in enumerate(
                zip(heat, times, places, strict=True), 1
            ):
                rows.append(
                    {
                        "roundid": roundid,
                        "heat": h,
                        "lane": lane,
                        "racerid": racer["racerid"],
                        "finishtime": t,
                        "finishplace": p,
                    }
                )
        for row in rows:
            if self.use_points and self.chance(0.3):
                row["finishtime"] = None
            elif not self.use_points and self.chance(self.k.place_only_lane):
                row["finishtime"] = None
        if state == "unfinished":
            for row in rng.sample(rows, rng.randint(1, len(rows))):
                row["finishtime"] = row["finishplace"] = None
        self._keep_a_score(rows)
        self.chart += rows

    def _keep_a_score(self, rows: list[dict]) -> None:
        """Every racer's heats with a result keep at least one value in the
        scoring mode: PHP cannot place a row whose score is NULL."""
        field = "finishplace" if self.use_points else "finishtime"
        by_racer: dict[int, list[dict]] = {}
        for row in rows:
            if row["finishtime"] is not None or row["finishplace"] is not None:
                by_racer.setdefault(row["racerid"], []).append(row)
        for racer_rows in by_racer.values():
            if all(r[field] is None for r in racer_rows):
                racer_rows[0]["finishtime"] = self.time(False)
                racer_rows[0]["finishplace"] = racer_rows[0]["finishplace"] or 1

    def add_rounds(self, classid: int, entrants: list[dict]) -> None:
        rng = self.rng
        state = (
            "unfinished" if self.chance(self.k.unfinished_first_round) else "finished"
        )
        self.add_round(classid, 1, entrants, state)
        number = 1
        while (
            state == "finished"
            and entrants
            and self.chance(self.k.follow_on)
            and number < 3
        ):
            number += 1
            entrants = rng.sample(entrants, rng.randint(1, len(entrants)))
            state = rng.choice(("unscheduled", "unfinished", "finished"))
            self.add_round(classid, number, entrants, state)

    def race(self) -> None:
        for group in self.racing_groups():
            entrants = [r for r in self.racers if r["classid"] == group["classid"]]
            if self.chance(0.7):
                entrants = [r for r in entrants if r["passedinspection"]]
            self.add_rounds(group["classid"], entrants)

    def settings(self) -> dict:
        rng = self.rng
        aggregates = [c["classid"] for c in self.classes if c["constituents"]]
        ffc = rng.choice(self.k.full_field_calc)
        if ffc == "aggregate":
            ffc = str(rng.choice(aggregates)) if aggregates else "0"
        settings = {
            "use-points": "1" if self.use_points else "0",
            "scoring": str(rng.choice(self.k.scoring)),
            "lane_count": str(self.lanes),
            "use-subgroups": "1" if self.chance(self.k.use_subgroups) else "0",
            "full-field-calc": ffc,
            "one-trophy-per": "1" if self.chance(self.k.one_trophy_per) else "0",
            "supergroup-label": rng.choice(SUPERGROUP_LABELS),
        }
        if self.chance(self.k.trophy_settings):
            for key, high in (
                ("n-pack-trophies", 5),
                ("n-den-trophies", 5),
                ("n-rank-trophies", 3),
            ):
                if self.chance(0.7):
                    settings[key] = str(rng.randint(0, high))
        return settings

    def requests(self) -> list[dict]:
        rng = self.rng
        classids = [c["classid"] for c in self.classes]
        rankids = [r["rankid"] for r in self.ranks]
        out = []
        for _ in range(self.k.requests):
            kind = rng.choice(("follow_on", "classes", "subgroups"))
            limit = rng.choice((0, 1, 2, 3, 3, 4, 5))
            bucketed = rng.random() < 0.5
            if kind == "follow_on":
                out.append(
                    {
                        "kind": kind,
                        "classid": rng.choice(classids),
                        "bucketed": bucketed,
                        "limit": limit,
                    }
                )
            elif kind == "classes":
                ids = rng.sample(classids, rng.randint(1, len(classids)))
                out.append(
                    {
                        "kind": kind,
                        "classids": ids,
                        "bucketed": bucketed,
                        "limit": limit,
                    }
                )
            else:
                ids = rng.sample(rankids, rng.randint(1, len(rankids)))
                out.append(
                    {"kind": kind, "rankids": ids, "bucketed": bucketed, "limit": limit}
                )
        return out

    def extra_award_keys(self, settings: dict) -> list[str]:
        rng = self.rng
        group = rng.choice(self.classes)["classid"]
        rank = rng.choice(self.ranks)
        return [
            "speed-1",
            "speed-2b",
            "speed-9",
            "speed-1z",
            "speed-0",
            "speed-01",
            f"speed-1-{group}",
            f"speed-2-{rank['classid']}-{rank['rankid']}",
            f"speed-1-{settings['full-field-calc']}",
            "speed-1-0",
            "speed-1-abc",
            f"speed-1-{rank['classid']}-x",
            "speed-x",
            "speed-",
            "speed-1-2-3-4",
            "speed-1-",
            "speed-1--",
            "speed-1.0",
            "speed-2.0",
            "speed-02",
            f"speed-1-0{group}",
            "speed",
            "award-1",
            "other-1",
        ]


def random_event(seed: int, batch: str | None = None) -> dict:
    rng = random.Random(f"{batch}:{seed}")
    b = _Builder(rng, knobs_for(batch))
    b.add_groups()
    b.race()
    b.add_aggregates()
    settings = b.settings()
    return {
        "id": f"{batch or 'random'}:{seed}",
        "settings": settings,
        "Classes": b.classes,
        "Ranks": b.ranks,
        "RegistrationInfo": b.racers,
        "Rounds": b.rounds,
        "Roster": b.roster,
        "RaceChart": b.chart,
        "requests": b.requests(),
        "extra_award_keys": b.extra_award_keys(settings),
    }
