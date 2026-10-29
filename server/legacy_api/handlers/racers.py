"""The racer, check-in and roster-import endpoints (#29): `racer.add`,
`racer.edit`, `racer.delete`, `racer.bulk`, `racer.pass`, `racer.import`,
`racer.fake`, and `query=racer.list`.

Thin adapters over `roster.services.racers`. PHP lets anyone check a racer in
and create walk-in racers by barcode (S23); here that takes `CHECK_IN_RACERS`
or `PHOTO_UPLOAD`, which the Photo role of the photo stand holds. PHP's
`racer.bulk` answers an un-check-in without `REVERT_CHECK_IN` in XML inside
the JSON (C21); here it is a JSON `notauthorized`.
"""

from accounts.permissions import Access, Perm, granted_for
from legacy_api.registry import REFUSALS, LegacyContext, action, query
from racing.engine.phpcompat import php_intval, php_numeric, php_truthy
from roster.models import Partition, Racer, Subgroup
from roster.services import barcode, car_numbers, racers
from roster.services.racers import Bulk, Edit, EditFailure, RacerFailure

# Checking in or creating a racer by barcode (#15, S23).
CHECK_IN = Perm.CHECK_IN_RACERS | Perm.PHOTO_UPLOAD
# The racer list holds names and photos, so it needs a role that works
# with racers. The anonymous role's VIEW_RACE_RESULTS is not enough (D5).
READ_RACERS = (
    Perm.CHECK_IN_RACERS
    | Perm.REVERT_CHECK_IN
    | Perm.REGISTER_NEW_RACER
    | Perm.EDIT_RACER
    | Perm.ASSIGN_RACER_IMAGE
    | Perm.JUDGING
    | Perm.CONTROL_RACE
    | Perm.PRESENT_AWARDS
    | Perm.EDIT_AWARDS
    | Perm.PHOTO_UPLOAD
    | Perm.SET_UP
)


RANGE_DIVERGENCES = (
    "D48: a partition with car number ranges answers range-full with its"
    " partitionid when every range is taken, and warns about a number outside"
    " the ranges; PHP has neither.",
)


def _text(ctx: LegacyContext, name: str, default: str = "") -> str:
    value = ctx.args.get(name)
    return value if isinstance(value, str) else default


def _optional(ctx: LegacyContext, name: str) -> str | None:
    """A posted field as PHP's `isset()` sees it: None when it is absent.
    A form array is not text, so it reads as absent too."""
    value = ctx.args.get(name)
    return value if isinstance(value, str) else None


def _refuse(ctx: LegacyContext) -> None:
    ctx.failure("notauthorized", REFUSALS["notauthorized"])


def _fail(ctx: LegacyContext, failure: RacerFailure) -> None:
    if failure.partition_id is not None:
        # The check-in page asks for another range of this partition.
        ctx.put("partitionid", failure.partition_id)
    ctx.failure(failure.code, failure.description)


@action("racer.add", permission=Perm.REGISTER_NEW_RACER, divergences=RANGE_DIVERGENCES)
def racer_add(ctx: LegacyContext) -> None:
    # PHP reads an undefined $partitionid, so with no valid partitionid the
    # racer lands in the Default partition (C15).
    partition = None
    posted = _text(ctx, "partitionid")
    number = php_numeric(posted)
    if number is not None and number > 0:
        partition = ctx.resolve(Partition, posted)
    try:
        new = racers.insert_new_racer(
            ctx.event,
            first_name=_text(ctx, "firstname"),
            last_name=_text(ctx, "lastname"),
            car_number=_optional(ctx, "carno"),
            car_name=_text(ctx, "carname"),
            note=_text(ctx, "note_from"),
            partition=partition,
            exclude=php_truthy(_text(ctx, "exclude")),
        )
    except RacerFailure as failure:
        _fail(ctx, failure)
        return
    if new.warnings:
        ctx.put("warnings", new.warnings)
    ctx.success()
    ctx.put("new-row", racers.checkin_row(new.racer))
    ctx.put("car-numbers", car_numbers.next_car_numbers(ctx.event.id))


def _place(ctx: LegacyContext, name: str, model):
    posted = _optional(ctx, name)
    if posted is None:
        return None, ""
    return ctx.resolve(model, posted) or racers.MISSING, posted


@action("racer.edit", permission=Perm.EDIT_RACER, divergences=RANGE_DIVERGENCES)
def racer_edit(ctx: LegacyContext) -> None:
    posted = _optional(ctx, "racer")
    if posted is None:
        posted = _optional(ctx, "racerid")
    if posted is None:
        ctx.failure("noracer", "No racerid provided.")
        return
    racer = ctx.resolve(Racer, posted)
    if racer is None:
        ctx.failure("noracer", "No such racer.")
        return
    partition, posted_partition = _place(ctx, "partitionid", Partition)
    subgroup, posted_subgroup = _place(ctx, "rankid", Subgroup)
    exclude = _optional(ctx, "exclude")
    edit = Edit(
        partition=partition,
        subgroup=subgroup,
        posted_partition=posted_partition,
        posted_subgroup=posted_subgroup,
        first_name=_optional(ctx, "firstname"),
        last_name=_optional(ctx, "lastname"),
        car_number=_optional(ctx, "carno"),
        car_name=_optional(ctx, "carname"),
        note=_optional(ctx, "note_from"),
        exclude=None if exclude is None else php_truthy(exclude),
    )
    try:
        result = racers.edit_racer(racer, edit)
    except EditFailure as failure:
        if partition is not None:
            ctx.put("partition-sortorder", failure.result.partition_sort_order)
        _fail(ctx, failure)
        return
    except RacerFailure as failure:
        _fail(ctx, failure)
        return
    if partition is not None:
        ctx.put("partition-sortorder", result.partition_sort_order)
    if result.warning:
        ctx.put("warning", result.warning)
    ctx.success()


@action("racer.delete", permission=Perm.EDIT_RACER)
def racer_delete(ctx: LegacyContext) -> None:
    posted = _optional(ctx, "racer")
    try:
        racers.delete_racer(ctx.event, ctx.resolve(Racer, posted), posted)
    except RacerFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()
    ctx.put("reload", True)


# Every caller reaches the handler, which refuses without EDIT_RACER.
@action("racer.bulk", permission=Access.ANONYMOUS)
def racer_bulk(ctx: LegacyContext) -> None:
    # PHP writes its refusal and then `reload` as for any other outcome.
    if not granted_for(ctx.session) & Perm.EDIT_RACER:
        _refuse(ctx)
        ctx.put("reload", True)
        return
    bulk = Bulk(
        who=_text(ctx, "who"),
        what=_text(ctx, "what"),
        value=_text(ctx, "value", "1"),
        start=_text(ctx, "start"),
        auto=php_truthy(_text(ctx, "auto")),
        may_revert=bool(granted_for(ctx.session) & Perm.REVERT_CHECK_IN),
    )
    try:
        racers.apply_bulk(ctx.event, bulk)
    except RacerFailure as failure:
        _fail(ctx, failure)
    else:
        ctx.success()
    ctx.put("reload", True)


@action(
    "racer.pass",
    permission=Perm.CHECK_IN_RACERS | Perm.PHOTO_UPLOAD | Perm.REVERT_CHECK_IN,
)
def racer_pass(ctx: LegacyContext) -> None:
    passed = php_truthy(_text(ctx, "value", "1"))
    needed = CHECK_IN if passed else Perm.REVERT_CHECK_IN
    if not granted_for(ctx.session) & needed:
        _refuse(ctx)
        return
    code = _optional(ctx, "barcode")
    if code is not None:
        racer = barcode.resolve(ctx.event, code).racer
    else:
        racer = ctx.resolve(Racer, _optional(ctx, "racer"))
    try:
        details = racers.pass_racer(racer, passed)
    except RacerFailure as failure:
        _fail(ctx, failure)
        ctx.put("reload", True)
        return
    ctx.success()
    if details is not None:
        # The photo stand reads these lines with sed, one key per line.
        ctx.put(
            "details",
            {
                "class": details.group_name,
                "firstname": details.first_name,
                "lastname": details.last_name,
            },
        )


# The fields racer.import requires, in the order PHP checks them.
REQUIRED_IMPORT_FIELDS = ("lastname", "firstname")


@action("racer.import", permission=Perm.SET_UP)
def racer_import(ctx: LegacyContext) -> None:
    names = {name: _optional(ctx, name) for name in REQUIRED_IMPORT_FIELDS}
    first_last = _optional(ctx, "first-last")
    if first_last is not None:
        names["firstname"], names["lastname"] = racers.split_first_last(first_last)
    for name in REQUIRED_IMPORT_FIELDS:
        if names[name] is None:
            ctx.failure("missingrequired", f"Missing required field {name}.")
            return
    exclude = _optional(ctx, "exclude")
    try:
        new = racers.insert_new_racer(
            ctx.event,
            first_name=names["firstname"],
            last_name=names["lastname"],
            car_number=_text(ctx, "carnumber"),
            car_name=_text(ctx, "carname"),
            note=_text(ctx, "note_from"),
            partition_name=_text(ctx, "partition").strip(" \t\n\r\0\x0b"),
            exclude=exclude is not None and racers.as_boolean(exclude),
        )
    except RacerFailure as failure:
        _fail(ctx, failure)
        return
    if new.warnings:
        ctx.put("warnings", new.warnings)
    ctx.put("racerid", new.racer.pk)
    ctx.success()


@action("racer.fake", permission=Perm.SET_UP)
def racer_fake(ctx: LegacyContext) -> None:
    racers.make_fake_racers(
        ctx.event,
        n_groups=php_intval(_text(ctx, "ngroups")),
        per_group=php_intval(_text(ctx, "racers_per_group")),
        check_in=php_truthy(_text(ctx, "check_in")),
    )
    ctx.success()


@query("racer.list", permission=READ_RACERS, get_writes=True)
def racer_list(ctx: LegacyContext) -> None:
    """With `barcode`, the one racer it names. An unknown `PWDwi` car makes
    a walk-in racer, so this GET can write, and the reply then carries the
    new check-in row and the next car numbers."""
    racer_id = None
    code = _optional(ctx, "barcode")
    if code:
        if not granted_for(ctx.session) & CHECK_IN:
            _refuse(ctx)
            return
        found = barcode.resolve(ctx.event, code)
        racer_id = found.racer.pk if found.racer else 0
        if found.created:
            ctx.put("new-row", racers.checkin_row(found.racer))
            ctx.put("car-numbers", car_numbers.next_car_numbers(ctx.event.id))
    elif php_truthy(_text(ctx, "racerid")):
        number = php_numeric(_text(ctx, "racerid"))
        racer_id = int(number) if number is not None else 0
    ctx.put(
        "racers",
        racers.list_racers(
            ctx.event,
            order=_text(ctx, "order"),
            racer_id=racer_id,
            render=_optional(ctx, "render"),
        ),
    )
