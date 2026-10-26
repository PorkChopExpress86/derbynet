"""Replay the requests a shell script sent to PHP, as the #7 recorder kept
them, through the Django services, and read back the state the recorder
snapshots.

Requests name rows by PHP's ids. `PhpIds` gives each Django row the id
SQLite would have given it: one more than the highest id in the table when
the row was made, counted in Django's creation order after each request.
So a script that looked an id up by name gets the same row here.

Racer writes are `racer.*` handlers of #29. `Replay` does them with the ORM
and the roster services, as `insert_new_racer` and `action.racer.*.inc` do.
"""

import json
from urllib.parse import parse_qsl

from core.models import RaceSettings
from core.services import new_event
from racing.models import HAS_RESULT, HeatLane, RosterEntry, Round
from racing.services.rounds import round_counts
from racing.services.schedule import generate_schedule
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.groups import GroupFailure, GroupRef

KINDS = {
    "class": Group,
    "rank": Subgroup,
    "partition": Partition,
    "racer": Racer,
    "round": Round,
}


class PhpIds:
    def __init__(self, event):
        self.event = event
        self.php = {kind: {} for kind in KINDS}

    def sync(self):
        for kind, model in KINDS.items():
            known = self.php[kind]
            live = set(
                model.objects.filter(event=self.event).values_list("pk", flat=True)
            )
            for pk in set(known) - live:
                del known[pk]
            for pk in sorted(live - set(known)):
                known[pk] = max(known.values(), default=0) + 1

    def get(self, kind, php_id):
        php_id = int(php_id)
        for pk, known in self.php[kind].items():
            if known == php_id:
                return KINDS[kind].objects.get(pk=pk)
        return None

    def of(self, kind, row) -> int:
        return self.php[kind][row.pk]


def _ordered(params, prefix):
    """`[value]` of keys `<prefix><n>`, by `n`."""
    keyed = [
        (int(key.removeprefix(prefix)), value)
        for key, value in params
        if key.startswith(prefix)
    ]
    return [value for _n, value in sorted(keyed)]


def _flagged(params, prefix):
    """The ids `<n>` of keys `<prefix><n>`, in posted order."""
    return [
        key.removeprefix(prefix) for key, _value in params if key.startswith(prefix)
    ]


class Replay:
    def __init__(self):
        self._new_event()
        self.failures = []

    def _new_event(self):
        self.event = new_event("replay")
        self.ids = PhpIds(self.event)

    @property
    def settings(self):
        return RaceSettings.objects.get(event=self.event)

    def post(self, body: str):
        params = parse_qsl(body, keep_blank_values=True)
        values = dict(params)
        action = values["action"]
        handler = getattr(self, "do_" + action.replace(".", "_").replace("-", "_"))
        try:
            handler(values, params)
        except GroupFailure as failure:
            self.failures.append((action, failure.code))
        self.ids.sync()

    def get(self, kind, php_id):
        row = self.ids.get(kind, php_id)
        assert row is not None, f"no {kind} with PHP id {php_id}"
        return row

    # ----- setup and settings -----

    def do_database_execute(self, values, params):
        if values["script"] == "schema":
            self._new_event()

    def do_settings_write(self, values, params):
        settings = self.settings
        if "n-lanes" in values:
            settings.lane_count = int(values["n-lanes"])
        if "unused-lane-mask" in values:
            settings.unused_lane_mask = int(values["unused-lane-mask"])
        if "do-use-subgroups-checkbox" in values:
            settings.use_subgroups = values.get("do-use-subgroups") == "1"
        settings.save()

    # ----- racers (#29) -----

    def import_racer(self, first, last, partition_name="", car_number="", car_name=""):
        """`insert_new_racer()`."""
        partition = groups.find_or_create_partition(
            self.event.id, partition_name or groups.DEFAULT_PARTITION_NAME
        )
        subgroup = partition.subgroup
        racer = Racer.objects.create(
            event=self.event,
            car_number=int(car_number) if car_number else self._next_car(partition),
            car_name=car_name.strip(),
            first_name=first.strip(),
            last_name=last.strip(),
            group_id=subgroup.group_id,
            subgroup=subgroup,
            partition=partition,
        )
        groups.fill_in_missing_roster_entries(self.event.id)
        return racer

    def _next_car(self, partition):
        """`next_car_number_for_partition()` with the default `100+101`."""
        mult, smallest = (int(x) for x in self.settings.car_numbering.split("+"))
        low = smallest + (partition.sort_order - 1) * mult
        high = smallest + partition.sort_order * mult
        cars = set(
            Racer.objects.filter(event=self.event).values_list("car_number", flat=True)
        )
        if low not in cars:
            return low
        return 1 + min(
            c
            for c in cars
            if c >= low and (mult == 0 or c < high) and c + 1 not in cars
        )

    def do_racer_import(self, values, params):
        first, last = values.get("firstname", ""), values.get("lastname", "")
        if "first-last" in values:
            whole = values["first-last"].strip()
            if "," in whole:
                last, first = whole.split(",", 1)
            elif " " in whole:
                first, last = whole.rsplit(" ", 1)
            else:
                first, last = "", whole
        self.import_racer(
            first,
            last,
            values.get("partition", ""),
            values.get("carnumber", ""),
            values.get("carname", ""),
        )

    def do_racer_edit(self, values, params):
        """`racer.edit` with a new partition or subgroup: the racer's round 1
        entry follows it to the new group's round 1."""
        racer = self.get("racer", values.get("racerid") or values.get("racer"))
        old_group = racer.group_id
        if "partitionid" in values:
            racer.partition = self.get("partition", values["partitionid"])
            racer.subgroup = racer.partition.subgroup
            racer.group_id = racer.subgroup.group_id
        if "rankid" in values:
            racer.subgroup = self.get("rank", values["rankid"])
            racer.group_id = racer.subgroup.group_id
        racer.save()
        if racer.group_id != old_group:
            new_round = Round.objects.filter(group_id=racer.group_id, number=1).first()
            RosterEntry.objects.filter(
                racer=racer, round__group_id=old_group, round__number=1
            ).update(round=new_round)

    def do_racer_delete(self, values, params):
        racer = self.get("racer", values["racer"])
        RosterEntry.objects.filter(racer=racer).delete()
        racer.delete()

    def do_racer_bulk(self, values, params):
        who = values["who"]
        racers = Racer.objects.filter(event=self.event).order_by("pk")
        if who.startswith("c"):
            racers = racers.filter(group=self.get("class", who[1:]))
        else:
            assert who == "all", who
        if values["what"] == "number":
            for n, racer in enumerate(racers, int(values["start"])):
                Racer.objects.filter(pk=racer.pk).update(car_number=n)
        else:
            assert values["what"] == "checkin", values
            racers.update(passed_inspection=values.get("value", "1") == "1")

    def do_racer_pass(self, values, params):
        Racer.objects.filter(pk=self.get("racer", values["racer"]).pk).update(
            passed_inspection=values.get("value", "1") == "1"
        )

    def do_schedule_generate(self, values, params):
        generate_schedule(
            self.get("round", values["roundid"]),
            int(values.get("n_times_per_lane", 1)),
        )

    # ----- partitions -----

    def do_partition_add(self, values, params):
        groups.add_partition(self.event, values["name"])

    def do_partition_edit(self, values, params):
        groups.rename_partition(
            self.get("partition", values["partitionid"]), values["name"]
        )

    def do_partition_delete(self, values, params):
        groups.delete_partition(self.get("partition", values["partitionid"]))

    def do_partition_order(self, values, params):
        groups.order_partitions(
            self.event,
            [self.get("partition", i) for i in _ordered(params, "partitionid_")],
        )

    def do_partition_apply_rule(self, values, params):
        rule = values["rule"]
        if rule != self.settings.group_formation_rule:
            groups.form_groups_by_rule(self.event, rule)
            return
        # D31: the rule in force changes nothing in Django. PHP rebuilds the
        # groups with new ids, which later requests name, so the replay
        # rebuilds them the way a real rule change does.
        before = self.state()
        groups.form_groups_by_rule(self.event, rule)
        assert self.state() == before
        groups.form_groups_by_rule(self.event, groups.Rule.CUSTOM)
        groups.form_groups_by_rule(self.event, rule)

    def do_partition_move(self, values, params):
        source = None
        if values.get("from_group_field"):
            source = GroupRef(values["from_group_field"], int(values["from_group_id"]))
        groups.move_partition(
            self.get("partition", values["div_id"]),
            GroupRef(values["group_field"], int(values["group_id"])),
            source,
        )

    # ----- groups and subgroups -----

    def do_class_add(self, values, params):
        extra = {}
        if "ntrophies" in values:
            extra["n_trophies"] = _trophies(values["ntrophies"])
        groups.add_group(
            self.event,
            values["name"],
            constituents=[
                self.get("class", i) for i in _flagged(params, "constituent_")
            ],
            subgroups=[self.get("rank", i) for i in _flagged(params, "rankid_")],
            **extra,
        )

    def do_class_edit(self, values, params):
        extra = {}
        if "ntrophies" in values:
            extra["n_trophies"] = _trophies(values["ntrophies"])
        groups.rename_group(
            self.get("class", values["classid"]), values["name"], **extra
        )

    def do_class_delete(self, values, params):
        groups.delete_group(self.get("class", values["classid"]))

    def do_class_order(self, values, params):
        groups.order_groups(
            self.event, [self.get("class", i) for i in _ordered(params, "classid_")]
        )

    def do_rank_add(self, values, params):
        group = self.ids.get("class", values["classid"])
        groups.add_subgroup(self.event, group.pk if group else -1, values["name"])

    def do_rank_edit(self, values, params):
        extra = {}
        if "ntrophies" in values:
            extra["n_trophies"] = _trophies(values["ntrophies"])
        groups.rename_subgroup(
            self.get("rank", values["rankid"]), values["name"], **extra
        )

    def do_rank_delete(self, values, params):
        groups.delete_subgroup(self.get("rank", values["rankid"]))

    def do_rank_move(self, values, params):
        target = int(values["classid"])
        groups.move_subgroup(
            self.get("rank", values["rankid"]),
            None if target < 0 else self.get("class", target),
        )

    def do_rank_order(self, values, params):
        groups.order_subgroups(
            self.event, [self.get("rank", i) for i in _ordered(params, "rankid_")]
        )

    # ----- seeding and reading back -----

    def seed(self, state):
        """Rebuild a recorded state that an unrecorded script made, as
        `import-roster.sh` does: the partitions in order, then the racers in
        the order PHP gave them ids, which is car number mod 100 there."""
        for partition in state["partitions"]:
            groups.find_or_create_partition(self.event.id, partition["name"])
        rows = [json.loads(row) for row in state["roster"]]
        for row in sorted(rows, key=lambda r: r["carnumber"] % 100):
            first, _, last = row["name"].partition(" ")
            racer = self.import_racer(
                first, last, row["partition"], str(row["carnumber"]), row["carname"]
            )
            Racer.objects.filter(pk=racer.pk).update(passed_inspection=row["passed"])
        self.ids.sync()

    def state(self):
        return read_state(self.event)


def _trophies(posted):
    return None if int(posted) < 0 else int(posted)


def _fold(name):
    return groups._fold(name)


def read_state(event):
    """What `recorder.mjs` reads with `class.list`, `poll` and `racer.list`,
    keyed by name."""
    group_rows = sorted(
        Group.objects.filter(event=event), key=lambda g: (g.sort_order, _fold(g.name))
    )
    rounds_with_results = _count_rounds_with_results(event)
    classes = []
    for group in group_rows:
        subgroups = sorted(
            Subgroup.objects.filter(group=group),
            key=lambda s: (s.sort_order, _fold(s.name)),
        )
        classes.append(
            {
                "name": group.name,
                "count": Racer.objects.filter(group=group).count(),
                "nrounds": rounds_with_results.get(group.pk, 0),
                "ntrophies": -1 if group.n_trophies is None else group.n_trophies,
                "subgroups": [
                    {
                        "name": s.name,
                        "count": Racer.objects.filter(subgroup=s).count(),
                        "ntrophies": -1 if s.n_trophies is None else s.n_trophies,
                    }
                    for s in subgroups
                ],
            }
        )
    partitions = [
        {"name": p.name, "count": Racer.objects.filter(partition=p).count()}
        for p in Partition.objects.filter(event=event).order_by("sort_order", "id")
    ]
    rounds = [
        {
            "class": c.round.group.name,
            "round": c.round.number,
            "name": c.name,
            "aggregate": c.aggregate,
            "roster_size": c.roster_size,
            "passed": c.passed,
            "unscheduled": c.unscheduled,
            "heats_scheduled": c.heats_scheduled,
            "heats_run": c.heats_run,
        }
        for c in round_counts(event)
    ]
    roster = sorted(
        _row(
            {
                "carnumber": r.car_number,
                "name": f"{r.first_name} {r.last_name}",
                "carname": r.car_name,
                "partition": r.partition.name,
                "class": r.group.name,
                "rank": r.subgroup.name,
                "passed": r.passed_inspection,
            }
        )
        for r in Racer.objects.filter(event=event).select_related(
            "partition", "group", "subgroup"
        )
    )
    return {
        "classes": classes,
        "partitions": partitions,
        "rounds": rounds,
        "roster": roster,
    }


def _count_rounds_with_results(event):
    counts = {}
    for group_id, _round_id in (
        HeatLane.objects.filter(event=event)
        .filter(HAS_RESULT)
        .values_list("round__group_id", "round_id")
        .distinct()
    ):
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def expected_after(state, change):
    """Apply one recorded `changed` diff to a state as `read_state` gives
    it."""
    state = dict(state)
    for key in ("classes", "partitions", "rounds"):
        if key in change:
            state[key] = change[key]
    if "roster" in change:
        roster = list(state["roster"])
        for row in change["roster"]["removed"]:
            roster.remove(_row(row))
        roster += [_row(row) for row in change["roster"]["added"]]
        state["roster"] = sorted(roster)
    return state


def _row(row) -> str:
    """A racer row as `recorder.mjs` writes it, `JSON.stringify` style."""
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def recorded_state(state):
    """A recorded `state` in the form `read_state` gives: the recorder
    keeps its roster as `JSON.stringify` text."""
    return {**state, "roster": sorted(state["roster"])}
