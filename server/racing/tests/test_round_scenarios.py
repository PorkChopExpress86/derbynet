"""The round-structure steps of the shell suite's round scripts, replayed
through the services.

`http/suite/<script>.http` records each `roster.new` and `roster.delete`
reply, which carries every round with its counts and the ready aggregates
(#7). Finalists come from the recorded reply until #26 chooses them. The
racing between steps is `heat.*` and timer traffic, which these fixtures do
not record, so it is replaced by a chart from `generate_schedule` and a
result on every lane.
"""

import json
from urllib.parse import parse_qsl, quote

import pytest
from django.utils import timezone

from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HeatLane, Round
from racing.services.rounds import (
    RoundFailure,
    delete_round_roster,
    prepare_new_round,
    ready_aggregate_groups,
    round_counts,
    write_roster,
)
from racing.services.schedule import generate_schedule
from roster.models import Group, GroupConstituentSubgroup, Racer, Subgroup
from roster.services.groups import create_aggregate_group
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db

SUITE = GOLDEN_DIR / "http" / "suite"


def suite(name):
    return read_transcript(SUITE / f"{name}.http")


class Rounds(Replay):
    """A replay that also knows the scripts' name lookups and their racing."""

    def import_roster(self):
        """`import-roster.sh` on an empty database: the state the recorder
        saw before `test-den-changes.sh`."""
        first = next(iter(suite("den-changes-basic").values()))
        self.seed(json.loads(first["body"])["state"])

    def group(self, prefix) -> Group:
        """`classid_of`: the first group in sort order whose name starts
        with `prefix`."""
        return next(
            g
            for g in Group.objects.filter(event=self.event).order_by("sort_order")
            if g.name.startswith(prefix)
        )

    def subgroup(self, prefix) -> Subgroup:
        return next(
            s
            for s in Subgroup.objects.filter(event=self.event).order_by(
                "group__sort_order", "sort_order"
            )
            if s.name.startswith(prefix)
        )

    def racer(self, car=None, name=None) -> Racer:
        racers = Racer.objects.filter(event=self.event).order_by("pk")
        if car is not None:
            return racers.filter(car_number=car).first()
        return next(r for r in racers if f"{r.first_name} {r.last_name}" == name)

    def php(self, kind, row) -> int:
        return self.ids.of(kind, row)

    def first_round(self, prefix, number=1) -> Round:
        return Round.objects.get(group=self.group(prefix), number=number)

    def race(self, round):
        """Schedule a round and give every lane a result, which stands in for
        `run_heat`."""
        if not HeatLane.objects.filter(round=round).exists():
            generate_schedule(round, 1)
        for lane in HeatLane.objects.filter(round=round):
            lane.finish_time = 3.0 + lane.lane / 100
            lane.completed = timezone.now()
            lane.save()
        self.ids.sync()

    def run_scout_heats(self):
        self.post("action=settings.write&unused-lane-mask=0&n-lanes=4")
        self.post("action=racer.bulk&who=all&what=checkin&value=1")
        for prefix in ("Lions", "White", "Bears", "Webelos", "Arrows"):
            self.race(self.first_round(prefix))

    def move_racers(self, subgroup, racers):
        for racer in racers:
            self.post(
                f"action=racer.edit&rankid={self.php('rank', self.subgroup(subgroup))}"
                f"&racer={self.php('racer', racer)}"
            )

    def roster_change(self, request_body, recorded):
        """Make the change `roster.new` or `roster.delete` made, with PHP's
        finalists, and return the outcome code."""
        params = parse_qsl(request_body, keep_blank_values=True)
        values = dict(params)
        if values["action"] == "roster.delete":
            round = self.ids.get("round", values["roundid"])
            if round is None:
                return "bad_param"
            try:
                delete_round_roster(round)
            except RoundFailure as failure:
                return failure.code
            self.ids.sync()
            return "success"

        finalists = [self.get("racer", f["racerid"]) for f in recorded["finalists"]]
        if values.get("roundid"):
            previous = self.get("round", values["roundid"])
            group, number = previous.group, previous.number + 1
        elif "classname" in values:
            group = create_aggregate_group(
                self.event.id,
                values["classname"],
                [
                    self.get("class", k[8:])
                    for k, _ in params
                    if k.startswith("classid_")
                ],
                [self.get("rank", k[7:]) for k, _ in params if k.startswith("rankid_")],
            )
            number = 1
        else:
            group, number = self.get("class", values["classid"]), 1
        round = prepare_new_round(group, number)
        write_roster(round, finalists)
        self.ids.sync()
        assert self.php("round", round) == recorded["new-round"]["roundid"]
        return "success"

    def rounds(self):
        return [
            {
                "roundid": self.php("round", c.round),
                "classid": self.php("class", c.round.group),
                "class": c.round.group.name,
                "round": c.round.number,
                "aggregate": c.aggregate,
                "roster_size": c.roster_size,
                "passed": c.passed,
                "unscheduled": c.unscheduled,
                "heats_scheduled": c.heats_scheduled,
                "heats_run": c.heats_run,
                "name": c.name,
            }
            for c in round_counts(self.event)
        ]

    def ready(self):
        return [
            {
                "classid": self.php("class", g),
                "by-subgroup": GroupConstituentSubgroup.objects.filter(
                    aggregate=g
                ).exists(),
                "class": g.name,
            }
            for g in ready_aggregate_groups(self.event)
        ]

    def check(self, name, exchange):
        recorded = json.loads(exchange["body"])
        code = self.roster_change(exchange["request-body"], recorded)
        assert code == recorded["outcome"]["code"], name
        if code == "success":
            php_rounds = [
                {k: v for k, v in r.items() if k not in ("adjustments", "next-round")}
                for r in recorded["rounds"]
            ]
            assert self.rounds() == php_rounds, name
            assert self.ready() == recorded["ready-aggregate"], name


# Who is in each group for `test-new-rounds.sh`, by PHP racer id. The
# racers before it are made by unrecorded scripts, so this is read off the
# replies: the follow-on finalists of 002 and 005, and the buckets of 006,
# whose concerns name 3 eligible Bears and 3 eligible Webelos.
NEW_ROUNDS_FINALISTS = {
    "Lions & Tigers": [31, 1, 41, 21],
    "White's Wolves": [27, 7, 47, 37],
    "Bears and Frères": [39, 3, 43],
    'Webelos ("Webes")': [49, 5, 9],
    "Arrows <<--<<": [],
}


def test_new_rounds_basic():
    """`test-new-rounds.sh` on the `test-basic-racing.sh` database. That
    database is rebuilt from PHP's first rounds reply: the groups, with
    `TheNotLastClass` made and deleted as `test-den-changes.sh` does so the
    ids match, round 1 rosters, passed racers and heats of the sizes shown,
    and the finalists in their groups."""
    replay = Rounds()
    exchanges = suite("new-rounds-basic")
    shown = {
        r["class"]: r
        for r in json.loads(exchanges["002 roster.new"]["body"])["rounds"]
        if r["round"] == 1
    }
    for name in [*NEW_ROUNDS_FINALISTS, "TheNotLastClass", "TheLastClass"]:
        replay.post(f"action=class.add&name={quote(name)}")
    unused = replay.php("class", replay.group("TheNotLastClass"))
    replay.post(f"action=class.delete&classid={unused}")

    members = {name: list(ids) for name, ids in NEW_ROUNDS_FINALISTS.items()}
    free = iter(range(1, 1 + sum(r["roster_size"] for r in shown.values())))
    for name, ids in members.items():
        while len(ids) < shown[name]["roster_size"]:
            n = next(free)
            if all(n not in other for other in members.values()):
                ids.append(n)
    group_of = {php_id: name for name, ids in members.items() for php_id in ids}
    for php_id in sorted(group_of):
        replay.import_racer("Racer", str(php_id), group_of[php_id], str(100 + php_id))
    replay.ids.sync()

    for name, ids in members.items():
        racers = [replay.get("racer", php_id) for php_id in ids]
        _give_round_one(replay, replay.first_round(name), racers, shown[name])

    for name, exchange in exchanges.items():
        replay.check(name, exchange)


def _give_round_one(replay, round, racers, row):
    """Pass the first `passed` racers and give them `heats_scheduled` heats,
    the first `heats_run` of them with results."""
    passed = racers[: row["passed"]]
    Racer.objects.filter(pk__in=[r.pk for r in passed]).update(passed_inspection=True)
    for heat in range(1, row["heats_scheduled"] + 1):
        for lane in range(min(len(passed), 4)):
            HeatLane.objects.create(
                event=replay.event,
                round=round,
                heat=heat,
                lane=lane + 1,
                racer=passed[(heat + lane) % len(passed)],
                finish_time=3.0 if heat <= row["heats_run"] else None,
            )


def test_aggregate_rounds():
    """`test-aggregate-rounds.sh`: aggregates by subgroup and by group, a
    second round of an aggregate, and an aggregate of aggregates."""
    replay = Rounds()
    replay.import_roster()
    replay.post("action=settings.write&do-use-subgroups=1&do-use-subgroups-checkbox")
    lions = replay.php("rank", replay.subgroup("Lions"))
    replay.post(f"action=rank.edit&name=Lions&rankid={lions}")
    lions_group = replay.php("class", replay.group("Lions"))
    replay.post(f"action=rank.add&classid={lions_group}&name=Tigers")
    replay.post(f"action=rank.add&classid={lions_group}&name=Cougars")
    cars = {
        "Tigers": (106, 116, 126, 146, 156, 171, 176, 181),
        "Cougars": (121, 136, 141),
    }
    for subgroup, numbers in cars.items():
        replay.move_racers(subgroup, [replay.racer(car=n) for n in numbers])
    replay.run_scout_heats()

    exchanges = suite("aggregate-rounds")
    steps = list(exchanges.items())
    racing_after = {
        "003 roster.new": ("Younger Finals", 1),
        "004 roster.new": ("Older Finals", 1),
        "005 roster.new": ("Older Finals", 2),
    }
    for name, exchange in steps:
        replay.check(name, exchange)
        if name in racing_after:
            replay.race(replay.first_round(*racing_after[name]))


def test_aggregate_classes():
    """`test-aggregate-classes.sh`: durable aggregates made with `class.add`,
    an aggregate of aggregates, and one by subgroup, then a round of the
    aggregate by subgroup."""
    replay = Rounds()
    replay.import_roster()
    csv = GOLDEN_DIR.parents[2] / "testing" / "data" / "outlaw-class.csv"
    for line in csv.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        first, last, car, partition = (f.strip('"') for f in line.strip().split(","))
        replay.post(
            f"action=racer.import&firstname={first}&lastname={last}"
            f"&carnumber={car}&partition={partition}"
        )
    replay.post("action=settings.write&do-use-subgroups=1&do-use-subgroups-checkbox")
    outlaw = replay.php("class", replay.group("Outlaw"))
    replay.post(
        f"action=rank.edit&rankid={replay.php('rank', replay.subgroup('Outlaw'))}"
        "&name=Siblings"
    )
    replay.post(f"action=rank.add&classid={outlaw}&name=Parents")
    replay.post(f"action=rank.add&classid={outlaw}&name=Desperados")
    moves = {
        "Parents": (
            "Belle Starr",
            "Pearl Hart",
            "Sam Bass",
            "Cherokee Bill",
            "Fred Waite",
            "Hoodoo Brown",
            "John Wesley Hardin",
        ),
        "Desperados": ("Doc Holliday", "Butch Cassidy", "Jesse James", "Zip Wyatt"),
    }
    for subgroup, names in moves.items():
        replay.move_racers(subgroup, [replay.racer(name=n) for n in names])
    replay.run_scout_heats()
    replay.race(replay.first_round("Outlaw"))

    def flags(kind, prefix, names):
        return "&".join(
            f"{prefix}{replay.php(kind, lookup(n))}=1"
            for n in names
            for lookup in [replay.group if kind == "class" else replay.subgroup]
        )

    replay.post(
        f"action=class.add&{flags('class', 'constituent_', ['Lions', 'White'])}"
        "&name=Younger"
    )
    replay.post(
        "action=class.add&"
        f"{flags('class', 'constituent_', ['Bears', 'Webelos', 'Arrows'])}&name=Older"
    )
    replay.post(
        f"action=class.add&{flags('class', 'constituent_', ['Younger', 'Older'])}"
        "&name=Scouts"
    )
    replay.post(
        f"action=class.add&{flags('rank', 'rankid_', ['Lions', 'Siblings', 'Parents'])}"
        "&name=Firsts%20and%20Families"
    )
    for name, exchange in suite("aggregate-classes").items():
        replay.check(name, exchange)


def test_aggregate_nonracing():
    """`test-aggregate-nonracing.sh`. Its fixture records nothing, because the
    script stops before its `roster.new`, so this checks what the script
    asserts against PHP: both Tigers and Younger are ready once their
    constituents have raced."""
    replay = Rounds()
    replay.import_roster()
    replay.post("action=settings.write&unused-lane-mask=0&n-lanes=4")
    lions = replay.php("class", replay.group("Lions"))
    replay.post(f"action=class.edit&classid={lions}&name=Tiger%20A")
    replay.post("action=class.add&name=Tiger%20B")
    a, b = (replay.php("class", replay.group(n)) for n in ("Tiger A", "Tiger B"))
    replay.post(f"action=class.add&constituent_{a}=1&constituent_{b}=1&name=Tigers")
    replay.post("action=racer.bulk&who=all&what=checkin&value=1")
    tiger_b = replay.subgroup("Tiger B")
    for car in (106, 116, 126, 136, 146, 156, 166, 176):
        replay.post(
            f"action=racer.edit&rankid={replay.php('rank', tiger_b)}"
            f"&racerid={replay.php('racer', replay.racer(car=car))}"
        )
    for prefix in ("Tiger A", "Tiger B", "White"):
        replay.race(replay.first_round(prefix))
    white, tigers = (replay.php("class", replay.group(n)) for n in ("White", "Tigers"))
    replay.post(
        f"action=class.add&constituent_{white}=1&constituent_{tigers}=1&name=Younger"
    )

    assert sorted(g["class"] for g in replay.ready()) == ["Tigers", "Younger"]
