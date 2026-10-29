"""Preferences files: `preferences.upload` and `preferences.php` (#36).

Ported from `website/inc/preferences.inc`. A file is lines of `tag = value`
settings, `GROUP`, `SUBGROUP`, `AGGREGATE`, `AWARD`, `NTROPHIES` and
`SEGMENT` lines that build the group structure, and bare tags for yes/no
settings. Like PHP's, an upload is not one transaction: a line that fails
leaves the lines before it applied.
"""

import re
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Max

from awards.models import Award, AwardType
from core import race_settings
from core.models import ActionLog, Event
from core.services import lock_race_state
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Subgroup,
)
from roster.services import groups

PREF_BOOL_SETTINGS = {
    "use-subgroups": "use-subgroups",
    "one-trophy-per": "one-trophy-per",
    "reverse-lanes": "reverse-lanes",
    "show-car-photos-on-deck": "show-cars-on-deck",
    "show-car-photos-rr": "show-car-photos-rr",
    "show-racer-photos-rr": "show-racer-photos-rr",
    "upload-videos": "upload-videos",
    "use-master-sched": "use-master-sched",
    "use-points": "use-points",
    "use-xbs": "use-xbs",
    "warn-no-timer": "warn-no-timer",
}
PREF_STRING_SETTINGS = {
    "n-lanes": "lane_count",
    "track-length": "track-length",
    "n-pack-trophies": "n-pack-trophies",
    "n-den-trophies": "n-den-trophies",
    "n-rank-trophies": "n-rank-trophies",
    "name-style": "name-style",
    "xbs-award": "xbs-award",
    "partition-label": "partition-label",
    "photos-on-now-racing": "photos-on-now-racing",
    "supergroup-label": "supergroup-label",
    "update-period": "update-period",
    "replay-skipback": "replay-skipback",
    "replay-num-showings": "replay-num-showings",
    "replay-rate": "replay-rate",
    "unused-lane-mask": "unused-lane-mask",
    "max-runs-per-car": "max-runs-per-car",
    "rotation-schedule": "rotation-schedule",
    "finish-formatting": "finish-formatting",
    "scoring": "scoring",
    "now-racing-linger-ms": "now-racing-linger-ms",
    "racing_scene": "racing_scene",
    "ballot_password": "ballot_password",
    "group-formation-rule": "group-formation-rule",
    "car-numbering": "car-numbering",
}
DESIGN_GENERAL = "Design General"
# AwardTypes 2 and 5, which a preferences file leaves out.
SKIPPED_AWARD_TYPES = ("Speed General", "Speed Trophy")


@dataclass(frozen=True)
class PrefsFailure(Exception):
    code: str
    description: str


def _fail(code: str, description: str):
    raise PrefsFailure(code, description)


class _Parser:
    def __init__(self, event: Event):
        self.event = event
        self.group: Group | None = None
        self.subgroup: Subgroup | None = None
        self.aggregate: Group | None = None
        self.agg_groups: list[Group] = []
        self.agg_subgroups: list[Subgroup] = []

    def line(self, number: int, line: str) -> None:
        tag, has_value, value = line.partition("=")
        val = value.strip() if has_value else None
        tag = tag.strip()
        arg = None
        if " " in tag:
            tag, _, arg = tag.partition(" ")
            arg = arg.strip()
        method = getattr(self, f"on_{tag.lower()}", None) if tag.isupper() else None
        handled = False
        if method is not None and (val is not None or tag.startswith("END")):
            handled = method(number, val, arg) is not False
        if not handled:
            self.setting(number, line, tag, val)

    # ----- structure -----

    def on_group(self, n, val, arg):
        if self.group or self.aggregate:
            _fail(
                "group-within-group",
                f"At line {n}, GROUP {val} within another group or aggregate",
            )
        self.group = groups.find_or_create_group(self.event.id, val)

    def on_endgroup(self, n, val, arg):
        if self.group is None:
            _fail("endgroup-no-group", f"At line {n}, ENDGROUP without GROUP")
        self.group = None

    def on_subgroup(self, n, val, arg):
        if self.group is None:
            _fail(
                "subgroup-needs-a-group",
                f"At line {n}, SUBGROUP {val} without an enclosing GROUP",
            )
        if self.subgroup is not None:
            _fail(
                "subgroup-within-subgroup",
                f"At line {n}, SUBGROUP {val} within another subgroup",
            )
        self.subgroup = groups.find_or_create_subgroup(self.group, val, False)
        self._partition(val, self.subgroup)

    def on_endsubgroup(self, n, val, arg):
        if self.subgroup is None:
            _fail(
                "endsubgroup-no-subgroup", f"At line {n}, ENDSUBGROUP without SUBGROUP"
            )
        self.subgroup = None

    def on_simple(self, n, val, arg):
        if arg == "GROUP":
            if self.group or self.aggregate:
                _fail(
                    "group-within-group",
                    f"At line {n}, GROUP {val} within another group or aggregate",
                )
            group = groups.find_or_create_group(self.event.id, val)
            self._partition(val, groups.find_or_create_subgroup(group, val, False))
            return
        # PHP assigns where it compares, so every other argument is a SUBGROUP.
        if self.group is None:
            _fail(
                "subgroup-needs-a-group",
                f"At line {n}, SUBGROUP {val} without an enclosing GROUP",
            )
        self._partition(val, groups.find_or_create_subgroup(self.group, val, False))

    def on_segment(self, n, val, arg):
        if groups._partition_named(Partition.objects.filter(event=self.event), val):
            _fail("division-exists", f"At line {n} , SEGMENT {val} already exists.")
        if self.subgroup is not None:
            groups.create_partition_in_subgroup(self.subgroup, val)
        elif self.group is not None:
            subgroup = groups.find_or_create_subgroup(self.group, val, False)
            groups.create_partition_in_subgroup(subgroup, val)
        else:
            if groups._rule(self.event.id) == groups.Rule.ONE_GROUP:
                groups.find_or_create_group(self.event.id, groups.ONE_GROUP_NAME)
            groups.find_or_create_partition(self.event.id, val)
        if arg:
            partition = groups._partition_named(
                Partition.objects.filter(event=self.event), val
            )
            if partition is not None and re.fullmatch(r"-?\d+", arg):
                with transaction.atomic():
                    lock_race_state(self.event.id)
                    Partition.objects.filter(pk=partition.pk).update(
                        sort_order=int(arg)
                    )

    def on_award(self, n, val, arg):
        name = val.strip()
        award_type = AwardType.objects.get(name=DESIGN_GENERAL)
        with transaction.atomic():
            lock_race_state(self.event.id)
            self._create_award(name, award_type)

    def _create_award(self, name, award_type) -> None:
        top = Award.objects.filter(event=self.event).aggregate(top=Max("sort"))["top"]
        award = Award.objects.create(
            event=self.event,
            name=name,
            award_type=award_type,
            group=self.group if self.subgroup is None else None,
            subgroup=self.subgroup,
            sort=0 if top is None else top + 1,
        )
        ActionLog.objects.create(
            event=self.event,
            request={
                "action": "award.*create",
                "awardname": name,
                "awardtype": DESIGN_GENERAL,
                "classid": award.group_id or 0,
                "rankid": award.subgroup_id or 0,
            },
        )

    def on_aggregate(self, n, val, arg):
        if self.group is not None:
            _fail("aggregate-within", f"At line {n}, AGGREGATE {val} within a group.")
        self.aggregate = groups.find_or_create_group(self.event.id, val, False)
        self.agg_groups, self.agg_subgroups = [], []

    def on_endaggregate(self, n, val, arg):
        if self.aggregate is None:
            _fail(
                "endaggregate-no-aggregate",
                f"At line {n}, ENDAGGREGATE without an aggregate.",
            )
        aggregate = self.aggregate
        with transaction.atomic():
            lock_race_state(self.event.id)
            if self.agg_subgroups:
                Group.objects.filter(pk=aggregate.pk).update(durable=True)
                GroupConstituentSubgroup.objects.filter(aggregate=aggregate).delete()
                GroupConstituentSubgroup.objects.bulk_create(
                    GroupConstituentSubgroup(
                        event=self.event, aggregate=aggregate, subgroup=s
                    )
                    for s in dict.fromkeys(self.agg_subgroups)
                )
            if self.agg_groups:
                Group.objects.filter(pk=aggregate.pk).update(durable=True)
                GroupConstituent.objects.filter(aggregate=aggregate).delete()
                GroupConstituent.objects.bulk_create(
                    GroupConstituent(
                        event=self.event, aggregate=aggregate, constituent=g
                    )
                    for g in dict.fromkeys(self.agg_groups)
                )
        self.aggregate = None

    def on_ntrophies(self, n, val, arg):
        target = self.subgroup or self.group or self.aggregate
        if target is None:
            _fail("ntrophies", f"At line {n}, NTROPHIES without context.")
        try:
            count = int(val)
        except ValueError:
            count = 0
        with transaction.atomic():
            lock_race_state(self.event.id)
            type(target).objects.filter(pk=target.pk).update(n_trophies=count)

    def on_constituentgroup(self, n, val, arg):
        return self.on_constituent(n, val, "GROUP")

    def on_constituentsubgroup(self, n, val, arg):
        return self.on_constituent(n, val, "SUBGROUP")

    def on_constituent(self, n, val, arg):
        if arg == "GROUP":
            if self.aggregate is None:
                _fail("constituentgroup", f"At line {n}, no aggregate")
            if self.agg_subgroups:
                _fail(
                    "constituentgroup-rank", f"At line {n}, constituentgroup for ranks"
                )
            self.agg_groups.append(groups.find_or_create_group(self.event.id, val))
        elif arg == "SUBGROUP":
            if self.aggregate is None:
                _fail("constituentsubgroup", f"At line {n}, no aggregate")
            if not self.agg_subgroups and self.agg_groups:
                _fail(
                    "constituentsubgroup-rank",
                    f"At line {n}, constituentgroup for classes",
                )
            subgroup = (
                Subgroup.objects.filter(event=self.event, name=val)
                .order_by("id")
                .first()
            )
            if subgroup is None:
                _fail(
                    "no-such-rank", f"At line {n}, constituentsubgroup {val} not found."
                )
            self.agg_subgroups.append(subgroup)
            self.agg_groups.append(subgroup.group)
        else:
            return False

    def _partition(self, name: str, subgroup: Subgroup) -> None:
        partitions = Partition.objects.filter(event=self.event)
        if groups._partition_named(partitions, name) is None:
            groups.create_partition_in_subgroup(subgroup, name)

    # ----- settings -----

    def setting(self, number: int, line: str, tag: str, val: str | None) -> None:
        if val is None:
            key = PREF_BOOL_SETTINGS.get(tag)
            if key is not None:
                return self._write(number, line, tag, key, "1")
            if tag.startswith("no-") and tag[3:] in PREF_BOOL_SETTINGS:
                return self._write(number, line, tag, PREF_BOOL_SETTINGS[tag[3:]], "0")
        else:
            key = PREF_STRING_SETTINGS.get(tag)
            if key is not None:
                return self._write(number, line, tag, key, val.strip())
        _fail("prefs-syntax", f"Preferences file syntax, line {number}: {line} ({val})")

    def _write(self, number, line, tag, key, text) -> None:
        try:
            race_settings.write(self.event, key, text)
        except race_settings.NotWritten:
            _fail(
                "prefs-syntax",
                f"Preferences file syntax, line {number}: {line} ({text})",
            )


def apply(event: Event, content: str) -> PrefsFailure | None:
    """Apply a preferences file. Returns the failure, or None on success."""
    parser = _Parser(event)
    try:
        for number, raw in enumerate(re.split(r"\r\n|\n|\r", content), start=1):
            line = raw.strip()
            if not line or line[0] == "#" or line.startswith("//"):
                continue
            parser.line(number, line)
        _check_partition_order(event)
    except PrefsFailure as failure:
        return failure
    except groups.GroupFailure as failure:
        return PrefsFailure(str(failure.code), failure.description)
    return None


def _check_partition_order(event: Event) -> None:
    """Partitions must be numbered 1 to n, which car numbering by partition
    relies on."""
    orders = list(
        Partition.objects.filter(event=event).values_list("sort_order", flat=True)
    )
    starts = [o for o in orders if orders.count(o - 1) != 1]
    if starts and not (len(starts) == 1 and min(starts) == 1):
        _fail("partition-suborder", "Partition sortorder fails.")


# ----- the file `preferences.php` writes -----


class _Peephole:
    """`PreferencePeepholer`: writes `SIMPLE` forms for groups and subgroups
    with nothing to say."""

    def __init__(self):
        self.out: list[str] = []
        self.group = ""
        self.simple_group = False
        self.subgroup = ""
        self.simple_subgroup = False
        self.matching_seen = False
        self.depth = 0

    def raw(self, text: str) -> None:
        self.out.append("  " * self.depth + text)

    def write(self, text: str) -> None:
        self._flush_group()
        self._flush_subgroup()
        self.raw(text)

    def _flush_group(self) -> None:
        if self.simple_group:
            self.raw(f"GROUP = {self.group}\n")
            self.depth += 1
            if self.matching_seen:
                self.raw(f"SIMPLE SUBGROUP = {self.group}\n")
            self.group = ""
            self.simple_group = False

    def _flush_subgroup(self) -> None:
        if self.simple_subgroup:
            self._flush_group()
            self.raw(f"SUBGROUP = {self.subgroup}\n")
            self.depth += 1
            self.subgroup = ""
            self.simple_subgroup = False

    def begin_group(self, group: str) -> None:
        self.group, self.simple_group = group, True
        self.subgroup, self.matching_seen = "", False

    def end_group(self) -> None:
        if self.simple_group:
            if self.matching_seen:
                self.raw(f"SIMPLE GROUP = {self.group}\n")
            else:
                self.raw(f"GROUP = {self.group}\n")
                self.raw("### Empty group!\n")
                self.raw("ENDGROUP\n")
        else:
            self.depth -= 1
            self.raw("ENDGROUP\n")
        self.group = self.subgroup = ""

    def begin_subgroup(self, subgroup: str) -> None:
        self.subgroup, self.simple_subgroup = subgroup, True

    def end_subgroup(self) -> None:
        if self.simple_subgroup:
            if self.simple_group:
                if self.subgroup == self.group:
                    self.matching_seen = True
                else:
                    self._flush_group()
                    self.raw(f"SIMPLE SUBGROUP = {self.subgroup}\n")
            else:
                self.raw(f"SIMPLE SUBGROUP = {self.subgroup}\n")
        else:
            self.depth -= 1
            self.raw("ENDSUBGROUP\n")
        self.simple_subgroup = False


# Keys the database writes when it is made, which a file leaves out.
DEFAULT_KEYS = {
    "photos-on-now-racing": "head",
    "show-cars-on-deck": "1",
    "show-racer-photos-rr": "1",
    "show-car-photos-rr": "1",
    "upload-videos": "1",
    "lane-colors": "",
}
# S4: PHP also writes `ballot_password`, so anyone could read it.
NOT_WRITTEN = ("group-formation-rule", "ballot_password")


def _awards(event, **where):
    return (
        Award.objects.filter(event=event, **where)
        .exclude(award_type__name__in=SKIPPED_AWARD_TYPES)
        .order_by("id")
        .values_list("name", flat=True)
    )


def dump(event: Event) -> str:
    out = _Peephole()
    out.raw(f"# Preferences written {date.today().isoformat()}\n\n")
    rule = race_settings.read(event, "group-formation-rule")
    if rule != "by-partition":
        out.raw(f"group-formation-rule = {rule}\n")
    out.raw("\n")

    previous = None
    rows = (
        Partition.objects.filter(event=event)
        .select_related("subgroup__group")
        .order_by("subgroup__group__sort_order", "subgroup__sort_order", "id")
    )
    for partition in rows:
        subgroup = partition.subgroup
        group = subgroup.group
        if group.pk != previous:
            if previous is not None:
                out.end_group()
            out.begin_group(group.name)
            previous = group.pk
            if group.n_trophies is not None and group.n_trophies >= 0:
                out.write(f"NTROPHIES = {group.n_trophies}\n")
            for name in _awards(event, group=group, subgroup__isnull=True):
                out.write(f"AWARD = {name}\n")
        out.begin_subgroup(subgroup.name)
        if subgroup.n_trophies is not None and subgroup.n_trophies >= 0:
            out.write(f"NTROPHIES = {subgroup.n_trophies}\n")
        for name in _awards(event, subgroup=subgroup):
            out.write(f"AWARD = {name}\n")
        out.end_subgroup()
    if previous is not None:
        out.end_group()

    loose = list(_awards(event, group__isnull=True, subgroup__isnull=True))
    if loose:
        out.raw("\n")
    for name in loose:
        out.raw(f"AWARD = {name}\n")

    first = True
    bool_tags = {key: tag for tag, key in PREF_BOOL_SETTINGS.items()}
    string_tags = {key: tag for tag, key in PREF_STRING_SETTINGS.items()}
    for key, value in race_settings.all_keys(event).items():
        if DEFAULT_KEYS.get(key) == value or key in NOT_WRITTEN:
            continue
        if key in bool_tags or key in string_tags:
            if first:
                out.raw("\n")
                first = False
        if key in bool_tags:
            tag = bool_tags[key]
            out.raw(f"{tag}\n" if int(value or 0) else f"no-{tag}\n")
        elif key in string_tags:
            out.raw(f"{string_tags[key]} = {value}\n")

    first = True
    for aggregate in Group.objects.filter(event=event, durable=True).order_by("id"):
        subgroups = list(
            GroupConstituentSubgroup.objects.filter(aggregate=aggregate)
            .order_by("id")
            .values_list("subgroup__name", flat=True)
        )
        members = list(
            GroupConstituent.objects.filter(aggregate=aggregate)
            .order_by("id")
            .values_list("constituent__name", flat=True)
        )
        if not (subgroups or members):
            continue
        if first:
            out.raw("\n")
            first = False
        out.raw(f"AGGREGATE = {aggregate.name}\n")
        if aggregate.n_trophies is not None and aggregate.n_trophies >= 0:
            out.raw(f"  NTROPHIES = {aggregate.n_trophies}\n")
        if subgroups:
            for name in subgroups:
                out.raw(f"  CONSTITUENT SUBGROUP = {name}\n")
        else:
            for name in members:
                out.raw(f"  CONSTITUENT GROUP = {name}\n")
        out.raw("ENDAGGREGATE\n")
    return "".join(out.out)
