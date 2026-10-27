"""The round-structure steps of the shell suite's round scripts, replayed
through the services.

`http/suite/<script>.http` records each `roster.new` and `roster.delete`
reply, which carries every round with its counts and the ready aggregates
(#7). `new_round_roster` chooses the finalists from the standings (#26).
The racing between steps is `heat.*` and timer traffic, which these
fixtures do not record. The export that `http/standings/suite-<script>.http`
records after each script holds every heat lane the database raced, so a
round's chart and results are entered from it.
"""

import json
from dataclasses import dataclass
from urllib.parse import parse_qsl

import pytest
from django.utils import timezone

from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HeatLane, Round
from racing.services.rounds import (
    FollowOn,
    FromAggregate,
    NewAggregate,
    NewRoster,
    RoundFailure,
    delete_round_roster,
    new_round_roster,
    ready_aggregate_groups,
    round_counts,
)
from roster.models import Group, GroupConstituentSubgroup, Racer, Subgroup
from roster.tests.php_replay import Replay, recorded_state

pytestmark = pytest.mark.django_db

SUITE = GOLDEN_DIR / "http" / "suite"
STANDINGS = GOLDEN_DIR / "http" / "standings"


def suite(name):
    return read_transcript(SUITE / f"{name}.http")


# The `racer.import` requests of `testing/import-roster.sh`, in its order.
IMPORT_ROSTER = [
    line.split('"', 1)[1].rsplit('" |', 1)[0].replace('\\"', '"')
    for line in (GOLDEN_DIR.parents[2] / "testing" / "import-roster.sh")
    .read_text()
    .splitlines()
    if line.startswith('curl_postj action.php "action=racer.import&')
]


@dataclass(frozen=True)
class Lane:
    heat: int
    lane: int
    racer: tuple[int, str]
    time: float | None
    place: int | None


class Export:
    """The Roster and Results sheets of the `export-json.php` reply that
    `http/standings/suite-<fixture>.http` records once the script is done:
    every passed racer, and every heat lane of every round, raced or not."""

    def __init__(self, fixture):
        transcript = read_transcript(STANDINGS / f"suite-{fixture}.http")
        sheets = dict(json.loads(next(iter(transcript.values()))["body"]))
        header, *rows = sheets["Roster"]
        self.roster = sorted(
            (
                f"{r['First Name']} {r['Last Name']}",
                r["Car Number"],
                r["Den"],
                r.get("Subgroup"),
                r["Excluded?"] == 1,
            )
            for r in (dict(zip(header, row, strict=True)) for row in rows)
        )
        header, *rows = sheets["Results"]
        self.lanes: dict[tuple[str, int], list[Lane]] = {}
        for r in (dict(zip(header, row, strict=True)) for row in rows):
            raced = r["Completed"] is not None
            self.lanes.setdefault((r["Class"], r["Round"]), []).append(
                Lane(
                    r["Heat"],
                    r["Lane"],
                    (r["CarNumber"], f"{r['FirstName']} {r['LastName']}"),
                    r["FinishTime"] if raced else None,
                    r["FinishPlace"] if raced else None,
                )
            )


class Rounds(Replay):
    def __init__(self, export: Export | None = None):
        super().__init__()
        self.export = export

    def import_roster(self):
        """`import-roster.sh` on an empty database, which gives racers the
        ids PHP gave them: its imports, the deletion of its last racer, and
        the renumbering of Webelos. Checked against the state the recorder
        saw before `test-den-changes.sh`."""
        for body in IMPORT_ROSTER:
            self.post(body)
        self.post(f"action=racer.delete&racer={self.racer_id(283)}")
        webelos = self.php("class", self.group("Webelos"))
        self.post(f"action=racer.bulk&what=number&who=c{webelos}&start=501&renumber=1")
        first = next(iter(suite("den-changes-basic").values()))
        assert self.state() == recorded_state(json.loads(first["body"])["state"])

    def den_changes(self):
        """`import-roster.sh`, then `test-den-changes.sh` as
        `den-changes-basic.http` records it."""
        self.import_roster()
        for exchange in list(suite("den-changes-basic").values())[1:]:
            self.post(exchange["request-body"])

    def award_setting_changes(self):
        """The racer, group and setting changes of `test-awards.sh`."""
        lions, white = (self.php("class", self.group(n)) for n in ("Lions", "White"))
        self.post(f"action=racer.edit&racer={self.racer_id(111)}&exclude=1")
        self.post(
            f"action=class.add&constituent_{lions}=1&constituent_{white}=1&name=Younger"
        )
        self.post(
            "action=settings.write&do-use-subgroups=1&do-use-subgroups-checkbox=1"
        )
        self.post(f"action=rank.add&name=Private&classid={lions}")
        private = self.php("rank", self.subgroup("Private"))
        self.post(f"action=class.add&rankid_{private}=1&name=AggExclusive")
        self.post(f"action=racer.edit&racer={self.racer_id(101)}&rankid={private}")
        for name in ("AggExclusive", "Younger"):
            self.post(
                f"action=class.delete&classid={self.php('class', self.group(name))}"
            )

    def check_in(self):
        cars = [row[1] for row in self.export.roster]
        Racer.objects.filter(event=self.event, car_number__in=cars).update(
            passed_inspection=True
        )

    def passed(self):
        use_subgroups = self.settings.use_subgroups
        return sorted(
            (
                f"{r.first_name} {r.last_name}",
                r.car_number,
                r.group.name,
                r.subgroup.name if use_subgroups else None,
                r.exclude,
            )
            for r in Racer.objects.filter(
                event=self.event, passed_inspection=True
            ).select_related("group", "subgroup")
        )

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

    def racer_id(self, car) -> int:
        """`racerid_of`."""
        return self.php("racer", self.racer(car=car))

    def php(self, kind, row) -> int:
        return self.ids.of(kind, row)

    def first_round(self, prefix, number=1) -> Round:
        return Round.objects.get(group=self.group(prefix), number=number)

    def race(self, round):
        assert not HeatLane.objects.filter(round=round).exists(), round
        racers = {
            (r.car_number, f"{r.first_name} {r.last_name}"): r
            for r in Racer.objects.filter(roster_entries__round=round)
        }
        HeatLane.objects.bulk_create(
            HeatLane(
                event=self.event,
                round=round,
                heat=lane.heat,
                lane=lane.lane,
                racer=racers[lane.racer],
                finish_time=lane.time,
                finish_place=lane.place,
                completed=None if lane.time is None else timezone.now(),
            )
            for lane in self.export.lanes[(round.group.name, round.number)]
        )
        self.ids.sync()

    def race_every_round(self):
        for group_name, number in self.export.lanes:
            self.race(Round.objects.get(group__name=group_name, number=number))

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

    def roster_change(self, request_body) -> tuple[str, NewRoster | None]:
        """Make the change `roster.new` or `roster.delete` made. Returns the
        outcome code, and the roster `roster.new` chose."""
        params = parse_qsl(request_body, keep_blank_values=True)
        values = dict(params)
        try:
            if values["action"] == "roster.delete":
                round = self.ids.get("round", values["roundid"])
                if round is None:
                    return "bad_param", None
                delete_round_roster(round)
                roster = None
            else:
                roster = new_round_roster(
                    self.roster_source(values, params),
                    int(values["top"]),
                    bucketed=values.get("bucketed", "") not in ("", "0"),
                )
        except RoundFailure as failure:
            return failure.code, None
        self.ids.sync()
        return "success", roster

    def roster_source(self, values, params):
        if values.get("roundid"):
            return FollowOn(self.get("round", values["roundid"]))
        if "classname" in values:
            return NewAggregate(
                values["classname"],
                [
                    self.get("class", k[8:])
                    for k, _ in params
                    if k.startswith("classid_")
                ],
                [self.get("rank", k[7:]) for k, _ in params if k.startswith("rankid_")],
            )
        return FromAggregate(self.get("class", values["classid"]))

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
        code, roster = self.roster_change(exchange["request-body"])
        assert code == recorded["outcome"]["code"], name
        if roster is not None:
            assert self.php("round", roster.round) == recorded["new-round"]["roundid"]
        if code == "success":
            php_rounds = [
                {k: v for k, v in r.items() if k not in ("adjustments", "next-round")}
                for r in recorded["rounds"]
            ]
            assert self.rounds() == php_rounds, name
            assert self.ready() == recorded["ready-aggregate"], name


def basic_racing() -> Rounds:
    """The database `test-new-rounds.sh` finds in the suite's first run:
    `import-roster.sh` and `test-den-changes.sh`, then the check-ins and the
    racer, group and setting changes of `test-basic-racing.sh` and
    `test-awards.sh basic`, and every heat raced."""
    replay = Rounds(Export("basic-racing-after-new-rounds"))
    replay.den_changes()
    replay.post("action=settings.write&unused-lane-mask=0&n-lanes=4")
    for car in BASIC_CHECKINS:
        replay.post(f"action=racer.pass&racer={replay.racer_id(car)}&value=1")
    replay.post(
        f"action=racer.edit&racer={replay.racer_id(111)}&firstname=Carroll"
        "&lastname=Cybulski&carno=111&carname=Vroom"
        f"&rankid={replay.php('rank', replay.subgroup('Lions'))}&exclude=1"
    )
    replay.post(
        f"action=racer.edit&racer={replay.racer_id(508)}&firstname=Jeffress"
        "&lastname=Jamison&carno=139&carname="
        f"&rankid={replay.php('rank', replay.subgroup('Bears'))}"
    )
    replay.import_racer("Fred", "Flintstone", "White's Wolves", "789")
    for car in (313, 323, 333):
        replay.post(f"action=racer.pass&racer={replay.racer_id(car)}&value=0")
    webelos = replay.php("rank", replay.subgroup("Webelos"))
    replay.post(
        f"action=racer.edit&racer={replay.racer_id(405)}&firstname=Zuzu"
        f"&lastname=Zingelo&carno=999&carname=Z-Car&rankid={webelos}"
        "&note_from=Suburbia"
    )
    replay.award_setting_changes()
    assert replay.passed() == replay.export.roster
    replay.race_every_round()
    return replay


# The racers `test-basic-checkins.sh` passes, by car number; its barcodes
# PWD237 and PWD508 name cars 237 and 508.
BASIC_CHECKINS = (101, 303, 405, 207, 502, 111, 313, 415, 217, 121, 323, 425)
BASIC_CHECKINS += (227, 131, 333, 435, 237, 508, 141, 343, 445, 247, 510)


def master_schedule() -> Rounds:
    """The database `test-master-schedule.sh` finds in the suite's third
    run, `import-roster.sh` and `test-den-changes.sh`, with the script's
    check-ins and its first rounds raced."""
    replay = Rounds(Export("master-schedule"))
    replay.den_changes()
    replay.post("action=settings.write&n-lanes=4")
    replay.check_in()
    replay.race_every_round()
    return replay


def aggregate_rounds() -> Rounds:
    """`test-aggregate-rounds.sh` up to its first `roster.new`."""
    replay = Rounds(Export("aggregate-rounds"))
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
    assert replay.passed() == replay.export.roster
    return replay


AGGREGATE_ROUNDS_RACED_AFTER = {
    "003 roster.new": ("Younger Finals", 1),
    "004 roster.new": ("Older Finals", 1),
    "005 roster.new": ("Older Finals", 2),
    "006 roster.new": ("Final Finals", 1),
}


def aggregate_classes() -> Rounds:
    """`test-aggregate-classes.sh` up to its `roster.new`."""
    replay = Rounds(Export("aggregate-classes"))
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
    assert replay.passed() == replay.export.roster

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
    return replay


def test_new_rounds_basic():
    """`test-new-rounds.sh` on the `test-basic-racing.sh` database."""
    replay = basic_racing()
    for name, exchange in suite("new-rounds-basic").items():
        replay.check(name, exchange)


def test_aggregate_rounds():
    """`test-aggregate-rounds.sh`: aggregates by subgroup and by group, a
    second round of an aggregate, and an aggregate of aggregates."""
    replay = aggregate_rounds()
    for name, exchange in suite("aggregate-rounds").items():
        replay.check(name, exchange)
        if name in AGGREGATE_ROUNDS_RACED_AFTER:
            replay.race(replay.first_round(*AGGREGATE_ROUNDS_RACED_AFTER[name]))


def test_aggregate_classes():
    """`test-aggregate-classes.sh`: durable aggregates made with `class.add`,
    an aggregate of aggregates, and one by subgroup, then a round of the
    aggregate by subgroup."""
    replay = aggregate_classes()
    for name, exchange in suite("aggregate-classes").items():
        replay.check(name, exchange)


def test_aggregate_nonracing():
    """`test-aggregate-nonracing.sh`, whose fixture records its `class.add`
    and `poll.coordinator` exchanges. The script polls before each heat, so
    poll 020, the first of White's round, is the first after both Tiger
    rounds have raced, when Tigers is ready. Poll 038 follows the `class.add`
    of Younger, and both are ready, as the script asserts."""
    php = suite("aggregate-nonracing")

    def php_ready(name):
        return json.loads(php[name]["body"])["ready-aggregate"]

    replay = Rounds(Export("aggregate-nonracing"))
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
    for prefix in ("Tiger A", "Tiger B"):
        replay.race(replay.first_round(prefix))
    assert replay.ready() == php_ready("020 poll.coordinator")
    replay.race(replay.first_round("White"))
    white, tigers = (replay.php("class", replay.group(n)) for n in ("White", "Tigers"))
    replay.post(
        f"action=class.add&constituent_{white}=1&constituent_{tigers}=1&name=Younger"
    )

    assert replay.ready() == php_ready("038 poll.coordinator")
    assert sorted(g["class"] for g in replay.ready()) == ["Tigers", "Younger"]
