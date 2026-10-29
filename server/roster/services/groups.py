"""Partitions, groups (legacy classes), subgroups (legacy ranks), and the
group formation rules (#25).

Ported from `website/inc/partitions.inc`, `form_groups_by_rule.inc`,
`class_add_or_delete.inc`, `rank_add_or_delete.inc`, `newracer.inc`, and the
`action.partition.*`, `action.class.*` and `action.rank.*` endpoints. Every
public service runs in one transaction that first takes the `RaceState`
lock, because `generate_schedule` rechecks the roster under that lock. The
caller resolves model instances inside the active event, and checks the
permission. A domain failure raises `GroupFailure` with PHP's code.

PHP deletes groups and subgroups that racers still name and repairs the
references afterwards. Django's foreign keys forbid that, so these services
move racers and partitions first and delete second, with the same result.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from django.db import connection, transaction
from django.db.models import Count, Max, Min, Q, Value
from django.db.models.functions import Left, Lower

from awards.models import Award, BallotAward
from awards.services import reconcile_partition_lists
from core.models import ActionLog, FullFieldRule, RaceSettings, RaceState
from core.services import lock_race_state
from racing.engine.heat_state import Cancel
from racing.models import (
    HAS_RESULT,
    HeatLane,
    PlaylistEntry,
    RosterEntry,
    Round,
    TimerStatus,
)
from racing.services import heats
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Racer,
    Subgroup,
)
from roster.services.structure import partition_structure

# CLASS_COLUMN_WIDTH and RANK_COLUMN_WIDTH. PHP cuts bytes; this cuts
# characters, so a multibyte name never ends in half a character.
NAME_WIDTH: Final = 75
DEFAULT_PARTITION_NAME: Final = "Default"
ONE_GROUP_NAME: Final = "All Racers"


class Rule(StrEnum):
    ONE_GROUP = "one-group"
    BY_PARTITION = "by-partition"
    CUSTOM = "custom"


class Failure(StrEnum):
    RESULTS_EXIST = "results-exist"
    BAD_RULE = "bad-rule"
    NO_NAME = "no-name"
    ALREADY_EXISTS = "already-exists"
    EMPTY_NAME = "empty-name"
    NAME_TAKEN = "name-taken"
    IN_USE = "in-use"
    NONAME = "noname"
    NOCLASS = "noclass"
    LAST_RANK = "last-rank"
    EXISTING_RACERS = "existing-racers"
    CONSTITUENT = "constituent"


class GroupFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


class GroupField(StrEnum):
    """The legacy field that names a group (`classid`) or a subgroup
    (`rankid`) in `partition.move`."""

    GROUP = "classid"
    SUBGROUP = "rankid"


@dataclass(frozen=True)
class GroupRef:
    """A group or subgroup by legacy field and id.

    Built from posted text, it accepts only the two field names, so no other
    text reaches a query. For a move target, a negative id asks for a new
    group named after the partition (`classid`), or a new subgroup in group
    `-id` (`rankid`), as in PHP.
    """

    field: GroupField
    id: int

    def __post_init__(self):
        object.__setattr__(self, "field", GroupField(self.field))
        if type(self.id) is not int:
            raise ValueError(f"group id must be an int, not {self.id!r}")


@dataclass(frozen=True)
class Labels:
    supergroup: str
    partition: str
    group: str
    subgroup: str


KEEP: Final = object()  # a keyword default that leaves a field unchanged


def labels(settings: RaceSettings) -> Labels:
    """The singular labels `labels()` in `partitions.inc` picks per rule."""
    rule = settings.group_formation_rule
    group, subgroup = "Group", "Subgroup"
    if rule == Rule.BY_PARTITION:
        group = settings.partition_label
    elif rule == Rule.ONE_GROUP:
        subgroup = settings.partition_label
    return Labels(settings.supergroup_label, settings.partition_label, group, subgroup)


def _rule(event_id) -> Rule:
    return Rule(RaceSettings.objects.get(event_id=event_id).group_formation_rule)


def _cut(name: str) -> str:
    return name.strip()[:NAME_WIDTH]


def _named(queryset, name: str):
    """Rows whose name equals `name` ignoring case, by the same `lower()` as
    the unique constraint on group names. SQLite folds ASCII letters only,
    as PHP's `COLLATE NOCASE` did."""
    return queryset.alias(lower_name=Lower("name")).filter(
        lower_name=Lower(Value(name))
    )


def _fold(name: str) -> str:
    """`COLLATE NOCASE` in Python: ASCII letters only."""
    return name.translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _partition_named(partitions, name: str) -> Partition | None:
    """The first of `partitions`, by id, whose name equals `name` ignoring
    case (D31)."""
    key = Partition.name_key(name)
    return next(
        (p for p in partitions.order_by("id") if Partition.name_key(p.name) == key),
        None,
    )


def _temporary_name() -> str:
    return f"(regrouping {uuid.uuid4().hex})"


def _name_taken(name: str) -> GroupFailure:
    return GroupFailure(Failure.NAME_TAKEN, f"The name {name} is already in use.")


def _record(event_id, **request) -> None:
    """`record_action()` of a synthetic entry, such as `class.*create`, with
    PHP's parameter names, which `history.php` reads. It is written in the
    caller's transaction, so a change and its entry commit together."""
    ActionLog.objects.create(event_id=event_id, request=request)


def record_partition_structure(event_id) -> None:
    """`record_action_partition_structure()`."""
    ActionLog.objects.create(event_id=event_id, request=partition_structure(event_id))


def is_aggregate(group_id) -> bool:
    return (
        GroupConstituent.objects.filter(aggregate_id=group_id).exists()
        or GroupConstituentSubgroup.objects.filter(aggregate_id=group_id).exists()
    )


def _group_name_taken(event_id, name: str, exclude: Group | None = None) -> bool:
    groups = _named(Group.objects.filter(event_id=event_id), name)
    if exclude is not None:
        groups = groups.exclude(pk=exclude.pk)
    return groups.exists()


# ----- Roster -----------------------------------------------------------


def fill_in_missing_roster_entries(event_id) -> int:
    """Enter every racer in round 1 of the racer's group, once. Returns the
    entries added.

    One `INSERT ... SELECT`, so running it again adds nothing. A racer is
    entered when an entry joins the racer to the round, whatever its group,
    as PHP decides.
    """
    with transaction.atomic():
        lock_race_state(event_id)
        q = connection.ops.quote_name
        entry, round_, racer = (m._meta for m in (RosterEntry, Round, Racer))

        def col(meta, name):
            return q(meta.get_field(name).column)

        sql = (
            f"INSERT INTO {q(entry.db_table)}"
            f" ({col(entry, 'event')}, {col(entry, 'round')}, {col(entry, 'racer')})"
            f" SELECT r.{col(round_, 'event')}, r.{col(round_, 'id')},"
            f" g.{col(racer, 'id')}"
            f" FROM {q(round_.db_table)} r"
            f" INNER JOIN {q(racer.db_table)} g"
            f" ON g.{col(racer, 'group')} = r.{col(round_, 'group')}"
            f" WHERE r.{col(round_, 'event')} = %s AND r.{col(round_, 'number')} = 1"
            f" AND NOT EXISTS (SELECT 1 FROM {q(entry.db_table)} e"
            f" WHERE e.{col(entry, 'round')} = r.{col(round_, 'id')}"
            f" AND e.{col(entry, 'racer')} = g.{col(racer, 'id')})"
            f" ORDER BY r.{col(round_, 'id')}, g.{col(racer, 'id')}"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, [event_id])
            return cursor.rowcount


# ----- Row primitives ----------------------------------------------------


def _cancel_running_round(state: RaceState) -> None:
    """`cancel_running_round()`: racing off and no current heat."""
    heats.transition(state, Cancel())


def delete_rounds(event_id, rounds) -> None:
    """Delete rounds with their charts and rosters, and every reference the
    race state and the timer hold to them."""
    with transaction.atomic():
        state = lock_race_state(event_id)
        round_ids = list(rounds.values_list("id", flat=True))
        if not round_ids:
            return
        if state.current_round_id in round_ids:
            _cancel_running_round(state)
        timers = TimerStatus.objects.filter(event_id=event_id)
        timers.filter(staged_round_id__in=round_ids).update(
            staged_round=None, staged_heat=None
        )
        timers.filter(test_saved_round_id__in=round_ids).update(test_saved_round=None)
        HeatLane.objects.filter(round_id__in=round_ids).delete()
        RosterEntry.objects.filter(round_id__in=round_ids).delete()
        Round.objects.filter(id__in=round_ids).delete()


def reset_full_field(event_id, groups) -> None:
    with transaction.atomic():
        lock_race_state(event_id)
        RaceSettings.objects.filter(
            event_id=event_id, full_field_group__in=groups
        ).update(full_field_rule=FullFieldRule.STANDARD, full_field_group=None)


def _delete_awards(awards) -> None:
    """Delete awards with their ballot entries and votes. PHP deletes the
    `Awards` rows and leaves their `BallotAwards` rows behind."""
    BallotAward.objects.filter(award__in=awards).delete()
    awards.delete()


def _delete_subgroup(subgroup: Subgroup) -> None:
    """`delete_rank()`: the subgroup, its awards and its partitions. The
    caller has checked that no racer is in it."""
    _record(subgroup.event_id, action="rank.*delete", rankid=subgroup.pk)
    _delete_awards(Award.objects.filter(subgroup=subgroup))
    Partition.objects.filter(subgroup=subgroup).delete()
    subgroup.delete()


def delete_group_rows(group: Group) -> None:
    """`delete_class()`: the group, its subgroups, rounds, awards, playlist
    steps and aggregate links. The caller has checked that no racer is in
    it."""
    with transaction.atomic():
        lock_race_state(group.event_id)
        for subgroup in Subgroup.objects.filter(group=group).order_by("id"):
            _delete_subgroup(subgroup)
        delete_rounds(group.event_id, Round.objects.filter(group=group))
        _delete_awards(Award.objects.filter(group=group))
        PlaylistEntry.objects.filter(group=group).delete()
        RaceState.objects.filter(event_id=group.event_id, current_group=group).update(
            current_group=None
        )
        reset_full_field(group.event_id, [group])
        group_id = group.pk
        group.delete()
        _record(group.event_id, action="class.*delete", classid=group_id)


def _create_partition(event_id, name: str, subgroup: Subgroup) -> Partition:
    """`create_partition_in_existing_rank()`."""
    top = Partition.objects.filter(event_id=event_id).aggregate(
        top=Max("sort_order"), n=Count("id")
    )
    sort_order = 1 + (top["top"] if top["top"] is not None else top["n"])
    partition = Partition.objects.create(
        event_id=event_id, name=name, subgroup=subgroup, sort_order=sort_order
    )
    _record(
        event_id,
        action="partition.*create",
        partitionid=partition.pk,
        name=name,
        rankid=subgroup.pk,
    )
    return partition


def create_partition_in_subgroup(subgroup: Subgroup, name: str) -> Partition:
    """`create_partition_in_existing_rank()`."""
    with transaction.atomic():
        lock_race_state(subgroup.event_id)
        return _create_partition(subgroup.event_id, name.strip(), subgroup)


def _create_group(event_id, name: str, with_first_round: bool = True) -> Group:
    """`create_class()`. The caller trims and cuts the name."""
    if _group_name_taken(event_id, name):
        raise GroupFailure(Failure.ALREADY_EXISTS, f"A group named {name} exists.")
    top = Group.objects.filter(event_id=event_id).aggregate(
        top=Max("sort_order"), n=Count("id")
    )
    sort_order = 1 + top["top"] if top["top"] is not None else 0
    if not sort_order:
        sort_order = 1 + top["n"]
    group = Group.objects.create(event_id=event_id, name=name, sort_order=sort_order)
    _record(event_id, action="class.*create", **{"class": name}, classid=group.pk)
    if with_first_round:
        first, _ = Round.objects.get_or_create(event_id=event_id, group=group, number=1)
        _record(
            event_id,
            action="round.*create",
            roundid=first.pk,
            classid=group.pk,
            round=1,
        )
    return group


def find_or_create_group(event_id, name: str, with_first_round: bool = True) -> Group:
    """`find_or_create_class()`: by name, ignoring case."""
    name = _cut(name)
    with transaction.atomic():
        lock_race_state(event_id)
        found = _named(Group.objects.filter(event_id=event_id), name).first()
        return found or _create_group(event_id, name, with_first_round)


def choose_unused_group_name(event_id, seed: str) -> str:
    """`choose_unused_class_name()`: `seed`, else `seed-1`, `seed-2`, ..."""
    name, n = seed, 0
    while _group_name_taken(event_id, name):
        n += 1
        name = f"{seed}-{n}"
    return name


def find_or_create_subgroup(
    group: Group, name: str, with_partition: bool = True
) -> Subgroup:
    """`find_or_create_rank()`: by name within the group, ignoring case.

    With `with_partition`, a new subgroup gets the partition of its name. If
    that partition already exists, or `find_or_create_partition` makes it for
    another subgroup, the new subgroup gets `<group>-<subgroup>` instead,
    plus `-1`, `-2`, ... until the name is free.
    """
    with transaction.atomic():
        lock_race_state(group.event_id)
        name = _cut(name)
        found = _named(Subgroup.objects.filter(group=group), name).first()
        if found:
            return found
        siblings = Subgroup.objects.filter(group=group)
        top = siblings.aggregate(top=Max("sort_order"))["top"]
        subgroup = Subgroup.objects.create(
            event_id=group.event_id,
            group=group,
            name=name,
            sort_order=1 if top is None else 1 + top,
        )
        if with_partition:
            partition = find_or_create_partition(group.event_id, name)
            if partition.subgroup_id != subgroup.id:
                base = f"{group.name}-{name}"
                candidate, n = base, 0
                partitions = Partition.objects.filter(event_id=group.event_id)
                while _partition_named(partitions, candidate):
                    n += 1
                    candidate = f"{base}-{n}"
                _create_partition(group.event_id, candidate, subgroup)
        _record_subgroup_created(subgroup)
        return subgroup


def _record_subgroup_created(subgroup: Subgroup) -> None:
    _record(
        subgroup.event_id,
        action="rank.*create",
        rank=subgroup.name,
        rankid=subgroup.pk,
        classid=subgroup.group_id,
    )


def find_or_create_partition(event_id, name: str) -> Partition:
    """`find_or_create_partition()`: by name, ignoring case. PHP matches the
    exact name, and so makes `cubs` beside `Cubs`.

    A new partition gets a subgroup of its name: under `one-group` in the
    first group, otherwise in the group of its name.
    """
    name = name.strip()
    with transaction.atomic():
        lock_race_state(event_id)
        found = _partition_named(Partition.objects.filter(event_id=event_id), name)
        if found:
            return found
        if _rule(event_id) == Rule.ONE_GROUP:
            group = Group.objects.filter(event_id=event_id).order_by("id").first()
            if group is None:
                group = find_or_create_group(event_id, ONE_GROUP_NAME)
        else:
            group = find_or_create_group(event_id, name)
        subgroup = find_or_create_subgroup(group, name, with_partition=False)
        return _create_partition(event_id, name, subgroup)


def clean_up_empty_groups_and_subgroups(event_id) -> None:
    """`clean_up_empty_classes_and_ranks()`.

    A subgroup with no partition goes, except the first subgroup of a group
    that an aggregate draws from, so that group keeps one. Outside
    `one-group`, a group with no subgroup that is not an aggregate and that
    no aggregate draws from goes too. A subgroup or group that racers still
    name stays; PHP deletes it and leaves the racers pointing at nothing.
    """
    with transaction.atomic():
        lock_race_state(event_id)
        unused = (
            Subgroup.objects.filter(event_id=event_id, partitions__isnull=True)
            .exclude(racers__isnull=False)
            .order_by("id")
        )
        for subgroup in list(unused):
            drawn_from = GroupConstituent.objects.filter(
                constituent_id=subgroup.group_id
            ).exists()
            first = (
                Subgroup.objects.filter(group_id=subgroup.group_id)
                .order_by("id")
                .values_list("id", flat=True)
                .first()
            )
            if drawn_from and first == subgroup.id:
                continue
            _delete_subgroup(subgroup)

        if _rule(event_id) != Rule.ONE_GROUP:
            empty = (
                Group.objects.filter(event_id=event_id, subgroups__isnull=True)
                .exclude(aggregate_links__isnull=False)
                .exclude(constituent_links__isnull=False)
                .exclude(constituent_subgroup_links__isnull=False)
                .exclude(racers__isnull=False)
                .order_by("id")
            )
            for group in list(empty):
                delete_group_rows(group)


def _reorder_partitions(event_id) -> None:
    """`reorder_partitions()`: number the partitions 1, 2, ... in the order
    of their groups, then of their subgroups."""
    ids = Partition.objects.filter(event_id=event_id).order_by(
        "subgroup__group__sort_order", "subgroup__sort_order", "id"
    )
    for position, partition_id in enumerate(ids.values_list("id", flat=True), 1):
        Partition.objects.filter(pk=partition_id).update(sort_order=position)


def _positions(rows: Sequence, positions: Sequence[int] | None) -> Sequence[int]:
    return range(1, len(rows) + 1) if positions is None else positions


def _renumber_densely(rows, posted) -> None:
    """Renumber every row 1, 2, ... by sort order (C16). Among rows that
    share one, the posted rows come first: posting B, C over A=1, B=2, C=3
    leaves B=1, A=1, C=2, which becomes B, A, C, and posted rows that share
    one keep the posted order. Gaps close too, so the
    orders are always 1 to n.

    A post that names only some rows can leave rows sharing a sort order.
    PHP's cleanup in `class.order` and `partition.order` fails (`class.order`
    binds a whole row as the id, and `$db->execute_once` does not exist), and
    the one in `rank.order` bumps every row with a higher id, which can put
    an unposted row between two posted ones.
    """
    rank = {row.pk: n for n, row in enumerate(posted)}
    ordered = sorted(
        rows, key=lambda r: (r.sort_order, rank.get(r.pk, len(rank)), r.pk)
    )
    for position, row in enumerate(ordered, 1):
        if row.sort_order != position:
            type(row).objects.filter(pk=row.pk).update(sort_order=position)


# ----- Group formation rules --------------------------------------------


def form_groups_by_rule(event, rule: str) -> None:
    """`partition.apply-rule`: rebuild groups and subgroups from the
    partitions under `rule`, in one transaction.

    `one-group` makes one group, `All Racers`, with a subgroup per partition.
    `by-partition` makes a group per partition with one subgroup of the same
    name, each with round 1. Both first delete every round, roster, chart,
    aggregate link, group and subgroup award, and playlist step, and reset the
    full-field rule. `custom` changes only the rule. PHP leaves the playlist
    rows in place, where they name whichever new group took the old id.

    The rule already in force changes nothing and succeeds, even once
    results exist (D31). PHP rebuilds the groups again.
    """
    try:
        rule = Rule(rule)
    except ValueError:
        raise GroupFailure(
            Failure.BAD_RULE, f"Rule value {rule} not recognized."
        ) from None
    with transaction.atomic():
        lock_race_state(event.id)
        settings = RaceSettings.objects.get(event=event)
        if settings.group_formation_rule == rule:
            return
        if HeatLane.objects.filter(event=event).filter(HAS_RESULT).exists():
            raise GroupFailure(Failure.RESULTS_EXIST, "Racing results already exist.")
        settings.group_formation_rule = rule
        settings.save(update_fields=["group_formation_rule"])
        if rule == Rule.ONE_GROUP:
            _form_one_group(event.id)
        elif rule == Rule.BY_PARTITION:
            _form_by_partition(event.id)
        clean_up_empty_groups_and_subgroups(event.id)
        record_partition_structure(event.id)
        settings.use_subgroups = rule != Rule.BY_PARTITION
        settings.save(update_fields=["use_subgroups"])


def _clear_group_structure(event_id) -> tuple[list[Group], list[Subgroup]]:
    old_groups = list(Group.objects.filter(event_id=event_id))
    old_subgroups = list(Subgroup.objects.filter(event_id=event_id))
    delete_rounds(event_id, Round.objects.filter(event_id=event_id))
    GroupConstituent.objects.filter(event_id=event_id).delete()
    GroupConstituentSubgroup.objects.filter(event_id=event_id).delete()
    _delete_awards(
        Award.objects.filter(event_id=event_id).filter(
            Q(group__isnull=False) | Q(subgroup__isnull=False)
        )
    )
    PlaylistEntry.objects.filter(event_id=event_id).delete()
    RaceState.objects.filter(event_id=event_id).update(current_group=None)
    RaceSettings.objects.filter(event_id=event_id).update(
        full_field_rule=FullFieldRule.STANDARD, full_field_group=None
    )
    return old_groups, old_subgroups


def _retire(old_groups, old_subgroups, renames: dict[Group, str]) -> None:
    for subgroup in old_subgroups:
        subgroup.delete()
    for group in old_groups:
        group.delete()
    for group, name in renames.items():
        Group.objects.filter(pk=group.pk).update(name=name)


def _partitions(event_id) -> list[Partition]:
    """In id order, the order PHP's unordered `SELECT ... FROM Partitions`
    returns them. `group_key` is the group name each would give, folded by
    the database's `lower()`, which is what the unique constraint on group
    names compares: every letter on Postgres, ASCII letters on SQLite."""
    return list(
        Partition.objects.filter(event_id=event_id)
        .annotate(group_key=Lower(Left("name", NAME_WIDTH)))
        .order_by("id")
    )


def _form_one_group(event_id) -> None:
    old_groups, old_subgroups = _clear_group_structure(event_id)
    group = Group.objects.create(
        event_id=event_id, name=_temporary_name(), sort_order=1
    )
    Round.objects.create(event_id=event_id, group=group, number=1)
    partitions = _partitions(event_id)
    subgroups = [
        Subgroup.objects.create(
            event_id=event_id,
            group=group,
            name=p.name[:NAME_WIDTH],
            sort_order=p.sort_order,
        )
        for p in partitions
    ]
    by_name: dict[str, Subgroup] = {}
    for subgroup in subgroups:
        by_name.setdefault(_fold(subgroup.name), subgroup)
    for p in partitions:
        subgroup = by_name[_fold(p.name[:NAME_WIDTH])]
        Partition.objects.filter(pk=p.pk).update(subgroup=subgroup)
        Racer.objects.filter(partition=p).update(group=group, subgroup=subgroup)
    _retire(old_groups, old_subgroups, {group: ONE_GROUP_NAME})
    fill_in_missing_roster_entries(event_id)


def _form_by_partition(event_id) -> None:
    old_groups, old_subgroups = _clear_group_structure(event_id)
    partitions = _partitions(event_id)
    groups: dict[str, tuple[Group, str]] = {}
    for p in partitions:
        if p.group_key not in groups:
            group = Group.objects.create(
                event_id=event_id, name=_temporary_name(), sort_order=p.sort_order
            )
            groups[p.group_key] = (group, p.name[:NAME_WIDTH])
    # PHP makes the subgroups and rounds with `INSERT ... SELECT ... FROM
    # Classes`, which SQLite reads through the class name index, so in name
    # order. Making them in that order gives them the ids PHP gives them.
    by_name = sorted(groups.items(), key=lambda item: _fold(item[1][1]))
    subgroups = {
        key: Subgroup.objects.create(
            event_id=event_id, group=group, name=name, sort_order=1
        )
        for key, (group, name) in by_name
    }
    for p in partitions:
        Partition.objects.filter(pk=p.pk).update(subgroup=subgroups[p.group_key])
        Racer.objects.filter(partition=p).update(
            group=groups[p.group_key][0], subgroup=subgroups[p.group_key]
        )
    _retire(old_groups, old_subgroups, dict(groups.values()))
    for _key, (group, _name) in by_name:
        Round.objects.create(event_id=event_id, group=group, number=1)
    fill_in_missing_roster_entries(event_id)


# ----- Partitions ---------------------------------------------------------


def add_partition(event, name: str) -> Partition:
    """`partition.add`. The exact name of another partition is
    `already-exists`, as in PHP, and the name with case ignored is
    `name-taken` (D31)."""
    name = name.strip()
    with transaction.atomic():
        lock_race_state(event.id)
        label = labels(RaceSettings.objects.get(event=event)).partition.lower()
        if not name:
            raise GroupFailure(Failure.NO_NAME, f"A {label} must have a name.")
        found = _partition_named(Partition.objects.filter(event=event), name)
        if found is not None and found.name == name:
            raise GroupFailure(
                Failure.ALREADY_EXISTS, f"A {label} named {name} already exists."
            )
        if found is not None:
            raise _name_taken(name)
        return find_or_create_partition(event.id, name)


def rename_partition(partition: Partition, name: str) -> None:
    """`partition.edit`: rename the partition and its subgroup, and under
    `by-partition` its group too.

    A name another partition holds, ignoring case, is `name-taken` (D31);
    PHP compares the exact name. Under `by-partition`, a name another group
    holds is `name-taken` as well; PHP fails there with a database error
    after renaming the partition.
    """
    name = name.strip()
    event_id = partition.event_id
    with transaction.atomic():
        lock_race_state(event_id)
        if not name:
            raise GroupFailure(Failure.EMPTY_NAME, "Name must not be empty")
        subgroup = Subgroup.objects.get(pk=partition.subgroup_id)
        by_partition = _rule(event_id) == Rule.BY_PARTITION
        others = Partition.objects.filter(event_id=event_id).exclude(pk=partition.pk)
        if _partition_named(others, name) or (
            by_partition
            and _group_name_taken(event_id, name[:NAME_WIDTH], exclude=subgroup.group)
        ):
            raise _name_taken(name)
        Partition.objects.filter(pk=partition.pk).update(name=name)
        if by_partition:
            Group.objects.filter(pk=subgroup.group_id).update(name=name[:NAME_WIDTH])
        Subgroup.objects.filter(pk=subgroup.pk).update(name=name[:NAME_WIDTH])


def delete_partition(partition: Partition) -> None:
    """`partition.delete`.

    Its subgroup goes when no racer or partition is left in it, and under
    `by-partition` its group goes, with its rounds and its place in any
    aggregate, when no racer or partition is left in the group. PHP deletes
    the group whenever no racer is left, and the group's other partitions
    with it.
    """
    event_id = partition.event_id
    with transaction.atomic():
        lock_race_state(event_id)
        if Racer.objects.filter(partition=partition).exists():
            label = labels(RaceSettings.objects.get(event_id=event_id)).partition
            raise GroupFailure(
                Failure.IN_USE,
                f"The {label.lower()} cannot be deleted because it has racers assigned",
            )
        subgroup = Subgroup.objects.get(pk=partition.subgroup_id)
        group = Group.objects.get(pk=subgroup.group_id)
        partition.delete()
        if not (
            Racer.objects.filter(subgroup=subgroup).exists()
            or Partition.objects.filter(subgroup=subgroup).exists()
        ):
            _delete_subgroup(subgroup)
        if (
            _rule(event_id) == Rule.BY_PARTITION
            and not Racer.objects.filter(group=group).exists()
            and not Partition.objects.filter(subgroup__group=group).exists()
        ):
            GroupConstituent.objects.filter(constituent=group).delete()
            delete_group_rows(group)
        reconcile_partition_lists(event_id)
        record_partition_structure(event_id)


def move_partition(
    partition: Partition, target: GroupRef, from_filter: GroupRef | None = None
) -> None:
    """`partition.move`: put the partition, and its racers, in another group
    or subgroup.

    A `classid` target puts it in a subgroup of its name in that group. With
    `from_filter`, only the partition's racers in that group or subgroup
    move. Every roster entry of the partition's racers is deleted and round 1
    entries are made again, as in PHP. Empty groups and subgroups are then
    cleaned up and the partitions renumbered.
    """
    event_id = partition.event_id
    with transaction.atomic():
        lock_race_state(event_id)
        name = Partition.objects.values_list("name", flat=True).get(pk=partition.pk)
        if target.field == GroupField.GROUP:
            if target.id < 0:
                group = find_or_create_group(event_id, name)
            else:
                group = Group.objects.get(event_id=event_id, pk=target.id)
            subgroup = find_or_create_subgroup(group, name)
        else:
            if target.id < 0:
                in_group = Group.objects.get(event_id=event_id, pk=-target.id)
                subgroup = find_or_create_subgroup(in_group, name)
            else:
                subgroup = Subgroup.objects.get(event_id=event_id, pk=target.id)
            group = Group.objects.get(pk=subgroup.group_id)

        RosterEntry.objects.filter(racer__partition=partition).delete()
        Partition.objects.filter(pk=partition.pk).update(subgroup=subgroup)
        racers = Racer.objects.filter(partition=partition)
        if from_filter is not None:
            if from_filter.field == GroupField.GROUP:
                racers = racers.filter(group_id=from_filter.id)
            else:
                racers = racers.filter(subgroup_id=from_filter.id)
        racers.update(group=group, subgroup=subgroup)
        fill_in_missing_roster_entries(event_id)
        clean_up_empty_groups_and_subgroups(event_id)
        _reorder_partitions(event_id)
        reconcile_partition_lists(event_id)
        record_partition_structure(event_id)


def order_partitions(
    event, partitions: Sequence[Partition], positions: Sequence[int] | None = None
) -> None:
    """`partition.order`: `partitions[i]` gets sort order `positions[i]`, by
    default `i + 1`, then every partition is renumbered 1 to n. Under
    `by-partition` each group takes its partition's sort order, and under
    `one-group` each subgroup does."""
    with transaction.atomic():
        lock_race_state(event.id)
        for position, partition in zip(
            _positions(partitions, positions), partitions, strict=True
        ):
            Partition.objects.filter(pk=partition.pk).update(sort_order=position)
        _renumber_densely(Partition.objects.filter(event=event), partitions)
        rule = _rule(event.id)
        if rule == Rule.ONE_GROUP:
            for subgroup in Subgroup.objects.filter(event=event):
                first = (
                    Partition.objects.filter(subgroup=subgroup)
                    .order_by("id")
                    .values_list("sort_order", flat=True)
                    .first()
                )
                _take_sort_order(subgroup, first)
        elif rule == Rule.BY_PARTITION:
            for group in Group.objects.filter(event=event):
                lowest = Partition.objects.filter(subgroup__group=group).aggregate(
                    lowest=Min("sort_order")
                )["lowest"]
                _take_sort_order(group, lowest)


def _take_sort_order(row, sort_order: int | None) -> None:
    """A subgroup takes the sort order of its first partition by id, as PHP
    does. A group takes the lowest of its partitions'; PHP takes whichever
    its subquery reads first, which follows subgroup ids. A row with no
    partition keeps its own; PHP sets it to null."""
    if sort_order is not None:
        type(row).objects.filter(pk=row.pk).update(sort_order=sort_order)


# ----- Groups -------------------------------------------------------------


def create_aggregate_group(
    event_id,
    name: str,
    constituents: Sequence[Group] = (),
    subgroups: Sequence[Subgroup] = (),
    durable: bool = False,
) -> Group:
    """`create_aggregate_class()`: a group with no round that draws from
    `constituents`, or from `subgroups` and their groups."""
    with transaction.atomic():
        lock_race_state(event_id)
        group = _create_group(event_id, name, with_first_round=False)
        Group.objects.filter(pk=group.pk).update(durable=durable)
        group.durable = durable
        members: list[int] = []
        for group_id in [g.pk for g in constituents] + [s.group_id for s in subgroups]:
            if group_id not in members:
                members.append(group_id)
        GroupConstituent.objects.bulk_create(
            GroupConstituent(event_id=event_id, aggregate=group, constituent_id=m)
            for m in members
        )
        GroupConstituentSubgroup.objects.bulk_create(
            GroupConstituentSubgroup(event_id=event_id, aggregate=group, subgroup=s)
            for s in subgroups
        )
        return group


def add_group(
    event,
    name: str,
    constituents: Sequence[Group] = (),
    subgroups: Sequence[Subgroup] = (),
    n_trophies=KEEP,
) -> Group:
    """`class.add`: an organic group with round 1 and a subgroup and
    partition of its name, or, given constituents or subgroups, a durable
    aggregate. A name another group holds is `already-exists`; PHP fails
    there with a database error."""
    name = _cut(name)
    with transaction.atomic():
        lock_race_state(event.id)
        if not name:
            label = labels(RaceSettings.objects.get(event=event)).group
            raise GroupFailure(Failure.NONAME, f"No name provided for the new {label}")
        if constituents or subgroups:
            group = create_aggregate_group(
                event.id, name, constituents, subgroups, durable=True
            )
        else:
            group = _create_group(event.id, name)
            find_or_create_subgroup(group, name)
        if n_trophies is not KEEP:
            Group.objects.filter(pk=group.pk).update(n_trophies=n_trophies)
            group.n_trophies = n_trophies
        return group


def rename_group(group: Group, name: str, n_trophies=KEEP) -> None:
    """`class.edit`. Under `by-partition` the group's subgroups and their
    partitions take the name too. A name another group or partition holds,
    ignoring case, is `name-taken`; PHP fails there with a database
    error."""
    name = _cut(name)
    event_id = group.event_id
    with transaction.atomic():
        lock_race_state(event_id)
        by_partition = _rule(event_id) == Rule.BY_PARTITION
        partitions = Partition.objects.filter(subgroup__group=group)
        if _group_name_taken(event_id, name, exclude=group) or (
            by_partition
            and (
                partitions.count() > 1
                or _partition_named(
                    Partition.objects.filter(event_id=event_id).exclude(
                        subgroup__group=group
                    ),
                    name,
                )
            )
        ):
            raise _name_taken(name)
        Group.objects.filter(pk=group.pk).update(name=name)
        if by_partition:
            Subgroup.objects.filter(group=group).update(name=name)
            partitions.update(name=name)
        if n_trophies is not KEEP:
            Group.objects.filter(pk=group.pk).update(n_trophies=n_trophies)


def delete_group(group: Group) -> None:
    """`class.delete`: refused while racers are in the group or an aggregate
    draws from it."""
    with transaction.atomic():
        lock_race_state(group.event_id)
        if Racer.objects.filter(group=group).exists():
            raise GroupFailure(
                Failure.EXISTING_RACERS, "Unable to delete a class with racers"
            )
        if GroupConstituent.objects.filter(constituent=group).exists():
            raise GroupFailure(
                Failure.CONSTITUENT, "Unable to delete a constituent class"
            )
        delete_group_rows(group)
        reconcile_partition_lists(group.event_id)


def order_groups(
    event, groups: Sequence[Group], positions: Sequence[int] | None = None
) -> None:
    """`class.order`: `groups[i]` gets sort order `positions[i]`, by default
    `i + 1`, every group is renumbered 1 to n, then the partitions are
    renumbered to match."""
    with transaction.atomic():
        lock_race_state(event.id)
        for position, group in zip(_positions(groups, positions), groups, strict=True):
            Group.objects.filter(pk=group.pk).update(sort_order=position)
        _renumber_densely(Group.objects.filter(event=event), groups)
        _reorder_partitions(event.id)
        record_partition_structure(event.id)


# ----- Subgroups ----------------------------------------------------------


def add_subgroup(event, group_id: int, name: str) -> Subgroup:
    """`rank.add`: a subgroup at the end of the group, with no partition,
    as in PHP."""
    name = _cut(name)
    with transaction.atomic():
        lock_race_state(event.id)
        if not name:
            label = labels(RaceSettings.objects.get(event=event)).group
            raise GroupFailure(Failure.NONAME, f"No name provided for the new {label}")
        group = Group.objects.filter(event=event, pk=group_id).first()
        if group is None:
            raise GroupFailure(Failure.NOCLASS, f"No class has classid {group_id}")
        n = Subgroup.objects.filter(group=group).count()
        subgroup = Subgroup.objects.create(
            event=event, group=group, name=name, sort_order=n + 1
        )
        _record_subgroup_created(subgroup)
        return subgroup


def rename_subgroup(subgroup: Subgroup, name: str, n_trophies=KEEP) -> None:
    """`rank.edit`: rename the subgroup and its partitions, and under
    `by-partition` its group too (D49, C49: PHP leaves the group).

    For a subgroup with partitions, a name another partition holds, ignoring
    case, is `name-taken`, and so is any name when it has more than one,
    since they cannot share it. PHP fails both with a database error after
    renaming the subgroup. A subgroup with no partition takes any name.
    """
    name = _cut(name)
    with transaction.atomic():
        lock_race_state(subgroup.event_id)
        partitions = Partition.objects.filter(subgroup=subgroup)
        n = partitions.count()
        others = Partition.objects.filter(event_id=subgroup.event_id).exclude(
            subgroup=subgroup
        )
        # The group follows only when it is this partition's own: the group
        # of a subgroup with a partition and no sibling subgroup. Any other
        # group holds names of more than one row, so it keeps its name.
        # The legacy `use-subgroups` setting turns the subgroups on without
        # changing the rule field, so by-partition holds only with it off.
        settings = RaceSettings.objects.get(event_id=subgroup.event_id)
        renames_group = (
            n == 1
            and settings.group_formation_rule == Rule.BY_PARTITION
            and not settings.use_subgroups
            and Subgroup.objects.filter(group_id=subgroup.group_id).count() == 1
        )
        if (
            n > 1
            or (n == 1 and _partition_named(others, name))
            or (
                renames_group
                and _group_name_taken(subgroup.event_id, name, exclude=subgroup.group)
            )
        ):
            raise _name_taken(name)
        if renames_group:
            Group.objects.filter(pk=subgroup.group_id).update(name=name)
        Subgroup.objects.filter(pk=subgroup.pk).update(name=name)
        partitions.update(name=name)
        if n_trophies is not KEEP:
            Subgroup.objects.filter(pk=subgroup.pk).update(n_trophies=n_trophies)


def delete_subgroup(subgroup: Subgroup) -> None:
    """`rank.delete`: refused for a group's last subgroup and while racers
    are in it."""
    with transaction.atomic():
        lock_race_state(subgroup.event_id)
        if Subgroup.objects.filter(group_id=subgroup.group_id).count() < 2:
            raise GroupFailure(
                Failure.LAST_RANK, "Can't delete the last rank in a class"
            )
        if Racer.objects.filter(subgroup=subgroup).exists():
            raise GroupFailure(
                Failure.EXISTING_RACERS, "Unable to delete a rank with racers"
            )
        _delete_subgroup(subgroup)
        reconcile_partition_lists(subgroup.event_id)


def move_subgroup(subgroup: Subgroup, group: Group | None) -> None:
    """`rank.move`: put the subgroup and its racers in `group`, or in a new
    group named after it.

    The racers' round 1 entries of their old group move to round 1 of the
    new one. PHP moves their entries in every round 1, an aggregate's too,
    which can enter a racer twice.
    """
    event_id = subgroup.event_id
    with transaction.atomic():
        lock_race_state(event_id)
        subgroup = Subgroup.objects.get(pk=subgroup.pk)
        old_group_id = subgroup.group_id
        if group is None:
            group = _create_group(
                event_id, choose_unused_group_name(event_id, subgroup.name)
            )
        n = Subgroup.objects.filter(group=group).count()
        Subgroup.objects.filter(pk=subgroup.pk).update(group=group, sort_order=n + 1)
        Racer.objects.filter(subgroup=subgroup).update(group=group)
        entries = RosterEntry.objects.filter(
            racer__subgroup=subgroup, round__group_id=old_group_id, round__number=1
        )
        first_round = Round.objects.filter(group=group, number=1).first()
        if first_round is None:
            entries.delete()
        else:
            entries.update(round=first_round)
        clean_up_empty_groups_and_subgroups(event_id)
        reconcile_partition_lists(event_id)
        record_partition_structure(event_id)


def order_subgroups(
    event, subgroups: Sequence[Subgroup], positions: Sequence[int] | None = None
) -> None:
    """`rank.order`: `subgroups[i]` gets sort order `positions[i]`, by default
    `i + 1`, the subgroups of each group are renumbered 1 to n, then the
    partitions are renumbered to match."""
    with transaction.atomic():
        lock_race_state(event.id)
        for position, subgroup in zip(
            _positions(subgroups, positions), subgroups, strict=True
        ):
            Subgroup.objects.filter(pk=subgroup.pk).update(sort_order=position)
        for group_id in (
            Subgroup.objects.filter(event=event)
            .values_list("group_id", flat=True)
            .distinct()
        ):
            _renumber_densely(Subgroup.objects.filter(group_id=group_id), subgroups)
        _reorder_partitions(event.id)
