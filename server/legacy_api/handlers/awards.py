"""The award, judging and presentation endpoints (#33): the `award.*`
actions, `query=award.current` and `query=award.list`, over
`awards.editing` and `awards.listing`.

The award kiosk polls `award.current` anonymously (D5). `award.list` needs
a login: every page that reads it has one. The ballot endpoints are
deferred to #54 (D7) and recorded in `legacy_api.registry.DEFERRED`.
"""

import time

from django.db import DatabaseError

from accounts.permissions import Access, Perm
from awards import editing, listing
from awards.editing import AwardFailure, Scope
from awards.models import Award
from core import race_settings
from core.models import RaceState
from legacy_api.registry import LegacyContext, action, query
from racing.engine.phpcompat import php_equals, php_intval, php_truthy
from roster.models import Group, Racer, Subgroup

PRESENT_DIVERGENCES = (
    "A3: with neither key nor reveal it answers success; PHP sets no outcome"
    " and answers in-progress.",
    "A5: a key past 100 characters answers not-written; PHP stores it.",
)
EDIT_DIVERGENCES = (
    "A1: a pack-wide award lists classid 0 and rankid 0 where PHP lists the"
    " null it stored for an award made with no class_and_rank.",
    "A2: a class_and_rank naming no group or subgroup of the event answers"
    " unknownclass or unknownrank, and a subgroup of another group"
    " wrongclass; PHP stores the ids as given.",
    "A5: a sort or ballot_depth past its column is kept at the column's"
    " limit; PHP stores the number posted.",
)
LIST_DIVERGENCES = (
    "D5: it needs a login; PHP answers anyone. Every page that reads it has one.",
    "A1: an award stored with no class lists classid 0 and rankid 0; PHP lists null.",
)
# The actions that answer with the award list, as award.list does.
REPLY_DIVERGENCES = (
    "A1: an award stored with no class lists classid 0 and rankid 0 in the"
    " reply's award list; PHP lists null.",
)
CALC_RULE_DIVERGENCES = (
    "A4: a full-field-calc that names no group of the event answers"
    " not-written; PHP stores it.",
)
KEY_LENGTH = RaceState._meta.get_field("current_award_key").max_length
# The largest sort a posted key may give.
MAX_SORT = 2**31 - 1


def _text(ctx: LegacyContext, key: str) -> str | None:
    value = ctx.args.get(key)
    return value if isinstance(value, str) else None


def _int(value) -> int | None:
    """A posted integer as PHP's integer column keeps it, or None for text
    that is not one or a number past the column."""
    if not isinstance(value, str):
        return None
    number = php_intval(value)
    return number if -MAX_SORT - 1 <= number <= MAX_SORT else None


def _is_zero(value) -> bool:
    """PHP's `$value == 0`, which a missing value also passes."""
    return value is None or (isinstance(value, str) and php_equals(0, value))


def _fail(ctx: LegacyContext, failure: AwardFailure) -> None:
    ctx.failure(failure.code, failure.description)


def _put_awards(ctx: LegacyContext, include_ad_hoc: bool) -> None:
    for key, value in listing.award_lists(ctx.event, include_ad_hoc).items():
        ctx.put(key, value)


def _scope(ctx: LegacyContext) -> Scope | None:
    """A posted `classid[,rankid]`, where 0 is none. Raises `AwardFailure`
    for an id that names no row of the event."""
    posted = _text(ctx, "class_and_rank")
    if posted is None:
        return None
    parts = posted.split(",")
    group = None
    if not _is_zero(parts[0]):
        group = ctx.resolve(Group, parts[0])
        if group is None:
            raise AwardFailure("unknownclass", "Unrecognized class")
    if len(parts) < 2:
        return Scope(group)
    subgroup = None
    if not _is_zero(parts[1]):
        subgroup = ctx.resolve(Subgroup, parts[1])
        if subgroup is None:
            raise AwardFailure("unknownrank", "Unrecognized rank")
    return Scope(group, subgroup)


@action("award.edit", permission=Perm.EDIT_AWARDS, divergences=EDIT_DIVERGENCES)
def award_edit(ctx: LegacyContext) -> None:
    fields = {
        "award_type_id": _int(ctx.args.get("awardtypeid")),
        "sort": _int(ctx.args.get("sort")),
        "ballot_depth": _int(ctx.args.get("ballot_depth")),
    }
    try:
        fields["scope"] = _scope(ctx)
        if ctx.args.get("awardid") == "new":
            editing.create_award(ctx.event, name=_text(ctx, "name"), **fields)
        else:
            award = ctx.resolve(Award, ctx.args.get("awardid"))
            if award is not None:
                if "awardtypeid" in ctx.args and fields["award_type_id"] is None:
                    # Posted but not a number names no type: `sql`, as in PHP.
                    fields["award_type_id"] = 0
                editing.edit_award(award, name=_text(ctx, "name"), **fields)
    except AwardFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()
    _put_awards(ctx, include_ad_hoc=False)


@action("award.delete", permission=Perm.EDIT_AWARDS, divergences=REPLY_DIVERGENCES)
def award_delete(ctx: LegacyContext) -> None:
    award = ctx.resolve(Award, ctx.args.get("awardid"))
    try:
        editing.delete_award(ctx.event, award, ctx.args.get("awardid"))
    except DatabaseError as error:
        ctx.failure("sql", str(error))
        return
    ctx.success()
    _put_awards(ctx, include_ad_hoc=False)


@action("award.order", permission=Perm.EDIT_AWARDS, divergences=REPLY_DIVERGENCES)
def award_order(ctx: LegacyContext) -> None:
    placed = []
    for key, raw_id in ctx.args.items():
        if not key.startswith("awardid_"):
            continue
        sort = _int(key.removeprefix("awardid_"))
        award = ctx.resolve(Award, raw_id)
        if sort is not None and award is not None:
            placed.append((sort, award))
    editing.order_awards(ctx.event, placed)
    ctx.success()
    _put_awards(ctx, include_ad_hoc=False)


@action("award.adhoc", permission=Perm.JUDGING, divergences=REPLY_DIVERGENCES)
def award_adhoc(ctx: LegacyContext) -> None:
    racer = ctx.resolve(Racer, ctx.args.get("racerid"))
    editing.set_ad_hoc_award(ctx.event, racer, _text(ctx, "awardname") or "")
    ctx.success()
    _put_awards(ctx, include_ad_hoc=True)


@action("award.winner", permission=Perm.JUDGING, divergences=REPLY_DIVERGENCES)
def award_winner(ctx: LegacyContext) -> None:
    award = ctx.resolve(Award, ctx.args.get("awardid"))
    racer = None
    try:
        if not _is_zero(ctx.args.get("racerid")):
            if award is None:
                raise AwardFailure("no-such-award", "Award not found")
            racer = ctx.resolve(Racer, ctx.args.get("racerid"))
            if racer is None:
                raise AwardFailure("no-such-racer", "Racer not found")
        editing.set_winner(ctx.event, award, racer)
    except AwardFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()
    _put_awards(ctx, include_ad_hoc=True)


@action("award.import", permission=Perm.SET_UP)
def award_import(ctx: LegacyContext) -> None:
    try:
        editing.import_award(ctx.event, ctx.args)
    except AwardFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()


@action(
    "award.present", permission=Perm.PRESENT_AWARDS, divergences=PRESENT_DIVERGENCES
)
def award_present(ctx: LegacyContext) -> None:
    key = _text(ctx, "key")
    if key is not None and len(key) > KEY_LENGTH:
        ctx.failure("not-written", "Value for current_award_key could not be written.")
        return
    reveal = php_truthy(ctx.args["reveal"]) if "reveal" in ctx.args else None
    editing.present(ctx.event, key=key, reveal=reveal)
    ctx.success()


@action(
    "award.calc-rule",
    permission=Perm.PRESENT_AWARDS,
    divergences=CALC_RULE_DIVERGENCES,
)
def award_calc_rule(ctx: LegacyContext) -> None:
    try:
        race_settings.write(
            ctx.event, "full-field-calc", _text(ctx, "full-field-calc") or ""
        )
    except race_settings.NotWritten:
        ctx.failure("not-written", "Value for full-field-calc could not be written.")
        return
    ctx.success()


@action("award.xbs", permission=Perm.CHECK_IN_RACERS)
def award_xbs(ctx: LegacyContext) -> None:
    racer = ctx.resolve(Racer, ctx.args.get("racer"))
    editing.toggle_xbs_award(ctx.event, racer, php_truthy(ctx.args.get("value")))
    ctx.success()


@query("award.current", permission=Access.ANONYMOUS)
def award_current(ctx: LegacyContext) -> None:
    started = time.monotonic()
    try:
        current = listing.current(ctx.event, started)
    except listing.UnrecognizedKey:
        ctx.failure("unrecognized-award-code", "Unrecognized award code")
        return
    if current is not None:
        ctx.put("current", current)


@query("award.list", permission=Access.LOGGED_IN, divergences=LIST_DIVERGENCES)
def award_list(ctx: LegacyContext) -> None:
    _put_awards(ctx, include_ad_hoc=php_truthy(ctx.args.get("adhoc", "1")))
