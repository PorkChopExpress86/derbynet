"""Groups, subgroups and partitions: the `class.*`, `rank.*` and
`partition.*` actions and `query=class.list` (#30), over the services of #25.

The legacy names are kept: a class is a group, a rank a subgroup. An id from
the request names a row of the active event only (`ctx.resolve`). PHP acts
on whatever id it is given, so an id that names nothing changes nothing here
and answers as PHP does for it, except in `partition.move`, which refuses it.
"""

from django.db import DatabaseError

from accounts.permissions import Access, Perm
from legacy_api.registry import LegacyContext, action, query
from roster.models import Group, Partition, Subgroup
from roster.services import groups, structure
from roster.services.groups import KEEP, GroupFailure, GroupField, GroupRef

MISSING_ARGS = ("missing-args", "Unrecognized argument signature")
# The largest `ntrophies` the column holds.
MAX_TROPHIES = 32767
# The largest sort order a posted key may give; the services renumber 1 to n.
MAX_SORT_ORDER = 2**31 - 1


def _text(ctx: LegacyContext, key: str) -> str | None:
    value = ctx.args.get(key)
    return value if isinstance(value, str) else None


def _int(value) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _n_trophies(ctx: LegacyContext):
    """A posted `ntrophies`: a negative number clears it, as `-1` means
    "use the setting" in `class.list`. Anything else leaves it unchanged."""
    n = _int(ctx.args.get("ntrophies"))
    if n is None or n > MAX_TROPHIES:
        return KEEP
    return None if n < 0 else n


def _ordered(ctx: LegacyContext, model, prefix: str) -> tuple[list, list[int]]:
    """The rows posted as `<prefix><n>=<id>` and their `n`, by `n`, then in
    posted order. A repeated id keeps its first place. An id that names no
    row of the event, and an `n` that is not a sort order, are left out."""
    keyed = []
    for position, (key, raw_id) in enumerate(ctx.args.items()):
        if not key.startswith(prefix):
            continue
        n = _int(key.removeprefix(prefix))
        if n is not None and 0 <= n <= MAX_SORT_ORDER:
            keyed.append((n, position, raw_id))
    rows, positions, seen = [], [], set()
    for n, _position, raw_id in sorted(keyed):
        row = ctx.resolve(model, raw_id)
        if row is not None and row.pk not in seen:
            seen.add(row.pk)
            rows.append(row)
            positions.append(n)
    return rows, positions


def _flagged(ctx: LegacyContext, model, prefix: str) -> list:
    """The rows named by keys `<prefix><id>`, in posted order."""
    rows = []
    for key in ctx.args:
        if key.startswith(prefix):
            row = ctx.resolve(model, key.removeprefix(prefix))
            if row is not None and row not in rows:
                rows.append(row)
    return rows


def _put_classes(ctx: LegacyContext) -> None:
    ctx.put("classes", structure.classes(ctx.event))


def _fail(ctx: LegacyContext, failure: GroupFailure) -> None:
    ctx.failure(str(failure.code), failure.description)


# ----- Classes ------------------------------------------------------------


@query("class.list", permission=Access.LOGGED_IN)
def class_list(ctx: LegacyContext) -> None:
    # PHP answers anyone. No kiosk reads it, so it needs a login (D5).
    _put_classes(ctx)


@action("class.add", permission=Perm.SET_UP, failure_code="permission")
def class_add(ctx: LegacyContext) -> None:
    name = _text(ctx, "name")
    if name is None:
        ctx.failure(*MISSING_ARGS)
        return
    try:
        groups.add_group(
            ctx.event,
            name,
            constituents=_flagged(ctx, Group, "constituent_"),
            subgroups=_flagged(ctx, Subgroup, "rankid_"),
            n_trophies=_n_trophies(ctx),
        )
    except GroupFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()


@action("class.edit", permission=Perm.SET_UP, failure_code="permission")
def class_edit(ctx: LegacyContext) -> None:
    name = _text(ctx, "name")
    if name is None or "classid" not in ctx.args:
        ctx.failure(*MISSING_ARGS)
        return
    group = ctx.resolve(Group, ctx.args["classid"])
    if group is not None:
        try:
            groups.rename_group(group, name, n_trophies=_n_trophies(ctx))
        except GroupFailure as failure:
            _fail(ctx, failure)
            return
    ctx.success()


@action("class.delete", permission=Perm.SET_UP)
def class_delete(ctx: LegacyContext) -> None:
    group = ctx.resolve(Group, ctx.args.get("classid"))
    try:
        if group is not None:
            groups.delete_group(group)
    except GroupFailure as failure:
        _fail(ctx, failure)
        return
    except DatabaseError as error:
        ctx.failure("sql", str(error))
        return
    _put_classes(ctx)
    ctx.success()


@action("class.order", permission=Perm.SET_UP, failure_code="permission")
def class_order(ctx: LegacyContext) -> None:
    groups.order_groups(ctx.event, *_ordered(ctx, Group, "classid_"))
    ctx.success()


# ----- Ranks --------------------------------------------------------------


@action("rank.add", permission=Perm.SET_UP, failure_code="permission")
def rank_add(ctx: LegacyContext) -> None:
    name = _text(ctx, "name")
    raw_id = ctx.args.get("classid")
    if name is None or raw_id is None:
        ctx.failure(*MISSING_ARGS)
        return
    group = ctx.resolve(Group, raw_id)
    try:
        # With no group, the service still refuses an empty name first.
        groups.add_subgroup(ctx.event, group.pk if group else -1, name)
    except GroupFailure as failure:
        if failure.code == groups.Failure.NOCLASS:
            shown = raw_id if isinstance(raw_id, str) else ""
            ctx.failure(str(failure.code), f"No class has classid {shown}")
        else:
            _fail(ctx, failure)
        return
    _put_classes(ctx)
    ctx.success()


@action("rank.edit", permission=Perm.SET_UP)
def rank_edit(ctx: LegacyContext) -> None:
    name = _text(ctx, "name")
    if name is None or "rankid" not in ctx.args:
        ctx.failure(*MISSING_ARGS)
        return
    subgroup = ctx.resolve(Subgroup, ctx.args["rankid"])
    if subgroup is not None:
        try:
            groups.rename_subgroup(subgroup, name, n_trophies=_n_trophies(ctx))
        except GroupFailure as failure:
            _fail(ctx, failure)
            return
    _put_classes(ctx)
    ctx.success()


@action("rank.delete", permission=Perm.SET_UP)
def rank_delete(ctx: LegacyContext) -> None:
    subgroup = ctx.resolve(Subgroup, ctx.args.get("rankid"))
    try:
        if subgroup is None:
            # PHP counts the ranks of no class, finds none, and refuses.
            raise GroupFailure(
                groups.Failure.LAST_RANK, "Can't delete the last rank in a class"
            )
        groups.delete_subgroup(subgroup)
    except GroupFailure as failure:
        _fail(ctx, failure)
        return
    except DatabaseError as error:
        ctx.failure("sql", str(error))
        return
    _put_classes(ctx)
    ctx.success()


@action("rank.move", permission=Perm.SET_UP, failure_code="permission")
def rank_move(ctx: LegacyContext) -> None:
    subgroup = ctx.resolve(Subgroup, ctx.args.get("rankid"))
    target = _int(ctx.args.get("classid"))
    if subgroup is not None and target is not None:
        if target < 0:
            groups.move_subgroup(subgroup, None)
        else:
            group = ctx.resolve(Group, target)
            if group is not None:
                groups.move_subgroup(subgroup, group)
    ctx.success()


@action("rank.order", permission=Perm.SET_UP)
def rank_order(ctx: LegacyContext) -> None:
    groups.order_subgroups(ctx.event, *_ordered(ctx, Subgroup, "rankid_"))
    ctx.success()


# ----- Partitions ---------------------------------------------------------


@action("partition.add", permission=Perm.CONTROL_RACE)
def partition_add(ctx: LegacyContext) -> None:
    # The race-day crew adds walk-in dens, so this needs only CONTROL_RACE.
    try:
        partition = groups.add_partition(ctx.event, _text(ctx, "name") or "")
    except GroupFailure as failure:
        _fail(ctx, failure)
        return
    ctx.put("partitionid", partition.pk)
    ctx.success()


@action("partition.edit", permission=Perm.SET_UP)
def partition_edit(ctx: LegacyContext) -> None:
    name = _text(ctx, "name") or ""
    partition = ctx.resolve(Partition, ctx.args.get("partitionid"))
    try:
        if partition is None:
            if not name.strip():
                raise GroupFailure(groups.Failure.EMPTY_NAME, "Name must not be empty")
        else:
            groups.rename_partition(partition, name)
    except GroupFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()


@action("partition.delete", permission=Perm.SET_UP)
def partition_delete(ctx: LegacyContext) -> None:
    partition = ctx.resolve(Partition, ctx.args.get("partitionid"))
    if partition is not None:
        try:
            groups.delete_partition(partition)
        except GroupFailure as failure:
            _fail(ctx, failure)
            return
    ctx.success()


def _group_ref(ctx: LegacyContext, field_key: str, id_key: str) -> GroupRef | None:
    """A `classid` or `rankid` field and an integer id, or None. Only those
    two field names are accepted (S9): PHP appends `from_group_field` to its
    SQL as posted."""
    field = ctx.args.get(field_key)
    group_id = _int(ctx.args.get(id_key))
    if field not in tuple(GroupField) or group_id is None:
        return None
    return GroupRef(GroupField(field), group_id)


def _target_exists(ctx: LegacyContext, target: GroupRef) -> bool:
    """A move target names a row of the event. A negative `classid` makes a
    group; a negative `rankid` makes a subgroup in group `-id`."""
    if target.field == GroupField.GROUP:
        return target.id < 0 or ctx.resolve(Group, target.id) is not None
    if target.id < 0:
        return ctx.resolve(Group, -target.id) is not None
    return ctx.resolve(Subgroup, target.id) is not None


@action("partition.move", permission=Perm.CONTROL_RACE)
def partition_move(ctx: LegacyContext) -> None:
    partition = ctx.resolve(Partition, ctx.args.get("div_id"))
    if partition is None:
        ctx.failure("no-such-partition", "No such partition")
        return
    target = _group_ref(ctx, "group_field", "group_id")
    if target is None or not _target_exists(ctx, target):
        ctx.failure("bad-group", "group_field must be classid or rankid of a group")
        return
    source = None
    if ctx.args.get("from_group_field"):
        source = _group_ref(ctx, "from_group_field", "from_group_id")
        if source is None:
            ctx.failure("bad-from-group", "from_group_field must be classid or rankid")
            return
    groups.move_partition(partition, target, source)
    ctx.success()


@action("partition.order", permission=Perm.SET_UP)
def partition_order(ctx: LegacyContext) -> None:
    groups.order_partitions(ctx.event, *_ordered(ctx, Partition, "partitionid_"))
    ctx.success()


@action("partition.apply-rule", permission=Perm.CONTROL_RACE)
def partition_apply_rule(ctx: LegacyContext) -> None:
    rule = ctx.args.get("rule")
    try:
        groups.form_groups_by_rule(ctx.event, rule if isinstance(rule, str) else "")
    except GroupFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()
