"""The award writes behind the legacy `award.*` actions (#33), ported from
`website/ajax/action.award.*.inc`.

Every write takes the `RaceState` lock first, so a winner's group, subgroup
and partition cannot change between the check and the write. A failure is
an `AwardFailure` with PHP's code, and changes nothing.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max

from awards.eligibility import Eligibility, Refusal
from awards.listing import AD_HOC
from awards.models import Award, AwardType, BallotAward, Vote
from awards.services import set_partition_list
from core.models import ActionLog, RaceSettings, RaceState
from core.race_settings import INTEGER_RANGES
from core.services import lock_race_state
from racing.engine.phpcompat import php_intval, php_numeric, php_truthy
from roster.models import Group, Partition, Racer, Subgroup
from roster.services.groups import _named, labels

# `CLASS_COLUMN_WIDTH` and `RANK_COLUMN_WIDTH`.
NAME_WIDTH = 75
# `Awards.awardname`.
NAME_LIMIT = Award._meta.get_field("name").max_length
# The award type `award.xbs` gives, "Other".
XBS_AWARD_TYPE = 3
AD_HOC_SORT = 999
# The largest values the integer columns hold on every database.
MAX_SORT = INTEGER_RANGES["IntegerField"][1]
MAX_BALLOT_DEPTH = INTEGER_RANGES["PositiveSmallIntegerField"][1]


class AwardFailure(Exception):
    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


class _Keep:
    def __repr__(self):
        return "KEEP"


KEEP = _Keep()


@dataclass(frozen=True)
class Scope:
    """A posted `class_and_rank`: the group, or None for pack-wide, and the
    subgroup, None for none, or KEEP when only a class was posted."""

    group: Group | None
    subgroup: Subgroup | None | _Keep = KEEP


def _check_scope(scope: Scope) -> None:
    subgroup = scope.subgroup
    if isinstance(subgroup, Subgroup) and (
        scope.group is None or subgroup.group_id != scope.group.pk
    ):
        raise AwardFailure("wrongclass", "Subgroup not in this group")


def _highest_sort(event) -> int | None:
    """The highest sort of an award that is not ad hoc, or None."""
    return (
        Award.objects.filter(event=event)
        .exclude(award_type_id=AD_HOC)
        .aggregate(n=Max("sort"))["n"]
    )


def _next_sort(event) -> int:
    """`award.edit`'s sort for a new award: one past the highest sort of an
    award that is not ad hoc, or 2 when there is none."""
    return min(1 + (_highest_sort(event) or 1), MAX_SORT)


def _award_type(award_type_id) -> AwardType:
    found = AwardType.objects.filter(pk=award_type_id).first()
    if found is None:
        raise AwardFailure("sql", "Unrecognized award type")
    return found


def _set_ballot_depth(award: Award, depth: int) -> None:
    """`set_ballot_depth()`: 0 takes the award off the ballot and deletes its
    votes."""
    BallotAward.objects.filter(award=award).delete()
    if depth > 0:
        BallotAward.objects.create(
            event_id=award.event_id,
            award=award,
            depth=min(depth, MAX_BALLOT_DEPTH),
        )
    else:
        Vote.objects.filter(award=award).delete()


def _delete(awards) -> None:
    """Delete awards with their ballot rows, which PHP leaves behind."""
    BallotAward.objects.filter(award__in=awards).delete()
    awards.delete()


def create_award(
    event,
    *,
    name: str | None,
    award_type_id,
    scope: Scope | None = None,
    sort: int | None = None,
    ballot_depth: int | None = None,
) -> Award:
    """`award.edit` with `awardid=new`. A missing name or type fails with
    `sql`, as PHP's insert does."""
    if name is None:
        raise AwardFailure("sql", "An award needs a name")
    with transaction.atomic():
        lock_race_state(event.id)
        award_type = _award_type(award_type_id)
        group = subgroup = None
        if scope is not None:
            _check_scope(scope)
            group = scope.group
            subgroup = None if scope.subgroup is KEEP else scope.subgroup
        award = Award.objects.create(
            event=event,
            name=name,
            award_type=award_type,
            group=group,
            subgroup=subgroup,
            sort=_next_sort(event) if sort is None else sort,
        )
        if ballot_depth is not None:
            _set_ballot_depth(award, ballot_depth)
    return award


def edit_award(
    award: Award,
    *,
    name: str | None = None,
    award_type_id=None,
    scope: Scope | None = None,
    sort: int | None = None,
    ballot_depth: int | None = None,
) -> None:
    """`award.edit` of an existing award: each field given changes."""
    with transaction.atomic():
        lock_race_state(award.event_id)
        award = Award.objects.get(pk=award.pk)
        if name is not None:
            award.name = name
        if award_type_id is not None:
            award.award_type = _award_type(award_type_id)
        if scope is not None:
            subgroup = award.subgroup if scope.subgroup is KEEP else scope.subgroup
            _check_scope(Scope(scope.group, subgroup))
            award.group, award.subgroup = scope.group, subgroup
        if sort is not None:
            award.sort = sort
        award.save()
        if ballot_depth is not None:
            _set_ballot_depth(award, ballot_depth)


def delete_award(event, award: Award | None, posted_id) -> None:
    """`award.delete`, with the `award.*delete` entry `history.php` reads.
    PHP records it whether or not the id names an award."""
    with transaction.atomic():
        lock_race_state(event.id)
        if award is not None:
            _delete(Award.objects.filter(pk=award.pk))
        ActionLog.objects.create(
            event=event, request={"action": "award.*delete", "awardid": posted_id}
        )


def order_awards(event, placed: Iterable[tuple[int, Award]]) -> None:
    """`award.order`: each award takes its posted sort."""
    with transaction.atomic():
        lock_race_state(event.id)
        for sort, award in placed:
            Award.objects.filter(pk=award.pk).update(sort=sort)


def set_ad_hoc_award(event, racer: Racer | None, name: str) -> None:
    """`award.adhoc`: replace the racer's ad hoc award, or remove it when
    the name is empty (PHP's falsy, so "0" too). The award carries the
    racer's group and subgroup, so it shows wherever the racer does."""
    if racer is None:
        return
    with transaction.atomic():
        lock_race_state(event.id)
        racer = Racer.objects.get(pk=racer.pk)
        _delete(Award.objects.filter(event=event, award_type_id=AD_HOC, racer=racer))
        if php_truthy(name):
            Award.objects.create(
                event=event,
                name=name,
                award_type_id=AD_HOC,
                racer=racer,
                group_id=racer.group_id,
                subgroup_id=racer.subgroup_id,
                sort=AD_HOC_SORT,
            )


def set_winner(event, award: Award | None, racer: Racer | None) -> None:
    """`award.winner`: give a judged award to a racer, or clear its winner
    when `racer` is None.

    PHP's checks stay for every judged award: a racer not eligible for any
    award (`exclude`) is `ineligible`, and the racer must be in the award's
    group, through an aggregate's groups, and subgroup. A racer who is not
    eligible for speed awards can still win (D33). A racer whose partition
    the award's partition list leaves out is `ineligible` too (D35)."""
    with transaction.atomic():
        lock_race_state(event.id)
        if racer is None:
            if award is not None:
                Award.objects.filter(pk=award.pk).update(racer=None)
            return
        if award is None:
            raise AwardFailure("no-such-award", "Award not found")
        award = Award.objects.get(pk=award.pk)
        racer = Racer.objects.get(pk=racer.pk)
        refusal = Eligibility(event.id).refusal(award, racer)
        if refusal is not None:
            raise AwardFailure(refusal.legacy_code, refusal.description)
        Award.objects.filter(pk=award.pk).update(racer=racer)


def _type_named(name: str) -> AwardType:
    found = AwardType.objects.filter(name=name).first()
    if found is None:
        types = ", ".join(sorted(AwardType.objects.values_list("name", flat=True)))
        raise AwardFailure(
            "unknownawardtype",
            f"Unrecognized award type.  Award type must be one of: {types}",
        )
    return found


def import_award(event, posted: dict) -> None:
    """`award.import`: one award from an imported row, with the group and
    subgroup by name and the winner by racer id or car number."""
    for required in ("awardname", "awardtype"):
        if not isinstance(posted.get(required), str):
            raise AwardFailure("missingrequired", f"Missing required field {required}")
    name = posted["awardname"].strip()
    if not name:
        raise AwardFailure("emptyaward", "Award name can\\'t be empty")
    with transaction.atomic():
        lock_race_state(event.id)
        award_type = _type_named(posted["awardtype"])
        group = subgroup = None
        group_name = _text(posted, "classname")[:NAME_WIDTH]
        if group_name:
            group = _named(Group.objects.filter(event=event), group_name).first()
            if group is None:
                raise AwardFailure("unknownclass", "Unrecognized class")
        subgroup_name = _text(posted, "subgroup")[:NAME_WIDTH]
        if subgroup_name:
            subgroup = (
                _named(Subgroup.objects.filter(event=event), subgroup_name)
                .order_by("pk")
                .first()
            )
            if subgroup is None:
                raise AwardFailure("unknownrank", "Unrecognized rank")
            if group is None:
                group = subgroup.group
            elif group.pk != subgroup.group_id:
                label = labels(RaceSettings.objects.get(event=event)).group
                raise AwardFailure(
                    "wrongclass", f"Subgroup not in this {label.lower()}"
                )
        if "sort" in posted:
            sort = _php_int(posted["sort"])
        elif Award.objects.filter(event=event).exists():
            sort = _highest_sort(event) or 0
        else:
            sort = 0
        racer = None
        if "racerid" in posted:
            racer = Racer.objects.filter(
                event=event, pk=_php_int(posted["racerid"])
            ).first()
        elif "carnumber" in posted:
            car = _car_number(posted["carnumber"])
            found = (
                list(Racer.objects.filter(event=event, car_number=car)[:2])
                if car is not None
                else []
            )
            if not found:
                raise AwardFailure("unknowncarnumber", "Unknown car number")
            if len(found) > 1:
                raise AwardFailure("ambiguouscarnumber", "Ambiguous car number")
            racer = found[0]
        Award.objects.create(
            event=event,
            name=name,
            award_type=award_type,
            group=group,
            subgroup=subgroup,
            racer=racer,
            sort=sort,
        )


def _text(posted: dict, key: str) -> str:
    value = posted.get(key)
    return value.strip() if isinstance(value, str) else ""


def _php_int(value) -> int:
    """`0 + $value` for the integers these columns hold; anything else,
    or a number past an integer column, 0."""
    try:
        number = php_intval(value)
    except TypeError, ValueError:
        return 0
    return number if -(2**31) <= number < 2**31 else 0


def _car_number(value) -> int | None:
    """The car number `carnumber = :carnumber` can match: a whole number
    the column holds, or None for text that matches no car."""
    number = php_numeric(value) if isinstance(value, str) else None
    if number is None or not -MAX_SORT - 1 <= number <= MAX_SORT:
        return None
    return int(number) if number == int(number) else None


def present(event, *, key: str | None = None, reveal: bool | None = None) -> None:
    """`award.present`: the award the kiosks show, and whether it is
    revealed."""
    fields = {}
    if key is not None:
        fields["current_award_key"] = key
    if reveal is not None:
        fields["reveal_current_award"] = reveal
    if fields:
        with transaction.atomic():
            lock_race_state(event.id)
            RaceState.objects.filter(event=event).update(**fields)


def toggle_xbs_award(event, racer: Racer | None, on: bool) -> None:
    """`award.xbs`: give the racer the "Exclusively By Scout" award, named
    by `xbs-award`, or take it away."""
    if racer is None:
        return
    with transaction.atomic():
        lock_race_state(event.id)
        name = RaceSettings.objects.get(event=event).xbs_award
        if on:
            Award.objects.create(
                event=event, name=name, award_type_id=XBS_AWARD_TYPE, racer=racer
            )
        else:
            _delete(Award.objects.filter(event=event, racer=racer, name=name))


# ----- the awards editor and judging pages (#45) -----
#
# Each takes the ids the page posted and resolves them under the lock, so a
# row that changed or went away since the page loaded is refused here, and
# the same post twice changes nothing more.


def _event_award(event, award_id) -> Award:
    found = Award.objects.filter(event=event, pk=award_id).first()
    if found is None:
        raise AwardFailure("no-such-award", "That award is gone. Reload the page.")
    return found


def _event_racer(event, racer_id) -> Racer:
    found = Racer.objects.filter(event=event, pk=racer_id).first()
    if found is None:
        raise AwardFailure("no-such-racer", "That racer is gone. Reload the page.")
    return found


def _editor_scope(event, group_id, subgroup_id) -> tuple[Group | None, Subgroup | None]:
    group = subgroup = None
    if group_id is not None:
        group = Group.objects.filter(event=event, pk=group_id).first()
        if group is None:
            raise AwardFailure("unknownclass", "That group is gone. Pick another.")
    if subgroup_id is not None:
        subgroup = Subgroup.objects.filter(event=event, pk=subgroup_id).first()
        if subgroup is None:
            raise AwardFailure("unknownrank", "That subgroup is gone. Pick another.")
        if group is None or subgroup.group_id != group.pk:
            raise AwardFailure("wrongclass", "That subgroup is in another group.")
    return group, subgroup


def save_award(
    event,
    award_id: int | None,
    *,
    name: str,
    award_type_id: int | None,
    group_id: int | None,
    subgroup_id: int | None,
    partition_ids: list[int] | None,
) -> Award:
    """The awards editor's sheet: a new award (`award_id` None) or an
    award's name, type, group or subgroup, and its partition list, in one
    transaction. `partition_ids` None, or every partition the award draws
    from, is "all partitions" (D35, D38); an empty list is refused. An ad
    hoc award belongs to the judging page, so the editor neither picks the
    type nor edits one."""
    name = name.strip()
    if not name:
        raise AwardFailure("emptyaward", "Give the award a name.")
    if len(name) > NAME_LIMIT:
        raise AwardFailure("toolong", f"Keep the name to {NAME_LIMIT} characters.")
    if partition_ids is not None and not partition_ids:
        raise AwardFailure("nopartitions", "Check at least one, or pick all of them.")
    with transaction.atomic():
        lock_race_state(event.id)
        if award_type_id == AD_HOC:
            raise AwardFailure("adhoc", "Pick a type other than Ad Hoc.")
        award_type = AwardType.objects.filter(pk=award_type_id).first()
        if award_type is None:
            raise AwardFailure("unknownawardtype", "Pick the award's type.")
        group, subgroup = _editor_scope(event, group_id, subgroup_id)
        if award_id is None:
            award = Award(event=event, sort=_next_sort(event))
        else:
            award = _event_award(event, award_id)
            if award.award_type_id == AD_HOC:
                raise AwardFailure("adhoc", "Change an ad hoc award on Judging.")
        award.name, award.award_type = name, award_type
        award.group, award.subgroup = group, subgroup
        award.save()
        chosen = []
        if partition_ids is not None:
            chosen = list(Partition.objects.filter(event=event, pk__in=partition_ids))
            if len(chosen) != len(set(partition_ids)):
                raise AwardFailure("nopartitions", "That partition is gone.")
        try:
            set_partition_list(event, chosen, award=award)
        except ValueError:
            raise AwardFailure(
                "nopartitions", "Pick only partitions this award draws from."
            ) from None
    return award


def reorder_awards(event, award_ids: list[int]) -> None:
    """The editor's list in a new order, from a drag or a row's menu: the
    awards take sorts 1, 2, 3... as `award.order` gets them. The ids must be
    every award the editor lists, each once."""
    with transaction.atomic():
        lock_race_state(event.id)
        listed = set(
            Award.objects.filter(event=event)
            .exclude(award_type_id=AD_HOC)
            .values_list("pk", flat=True)
        )
        if len(award_ids) != len(set(award_ids)) or set(award_ids) != listed:
            raise AwardFailure("order", "The awards changed. Reload the page.")
        for sort, award_id in enumerate(award_ids, 1):
            Award.objects.filter(pk=award_id).update(sort=sort)


def delete_award_by_id(event, award_id: int) -> str:
    """The editor's Delete, after its confirm sheet: `delete_award`, with the
    entry `history.php` reads. Returns the award's name."""
    with transaction.atomic():
        lock_race_state(event.id)
        award = _event_award(event, award_id)
        delete_award(event, award, str(award_id))
    return award.name


def assign_award(event, award_id: int, racer_id: int) -> Award:
    """Judging's tap to assign, and the Undo of a removal: give a judged
    award to a racer who can win it (`Eligibility`). An award given to
    another racer is refused, so a judge removes that racer first; given to
    this racer already, nothing changes."""
    with transaction.atomic():
        lock_race_state(event.id)
        award = _event_award(event, award_id)
        racer = _event_racer(event, racer_id)
        if award.award_type_id == AD_HOC:
            raise AwardFailure("adhoc", "An ad hoc award stays with its racer.")
        if award.racer_id == racer.pk:
            return award
        if award.racer_id is not None:
            raise AwardFailure("filled", f"{award.name} already has a winner.")
        refusal = Eligibility(event.id).refusal(award, racer)
        if refusal is not None:
            raise AwardFailure(refusal.code, refusal_text(refusal, award, racer))
        award.racer = racer
        award.save(update_fields=["racer"])
    return award


def remove_winner(event, award_id: int) -> tuple[Award, Racer | None]:
    """Judging's remove: the award loses its winner, returned for the Undo.
    An award with no winner changes nothing, and returns None."""
    with transaction.atomic():
        lock_race_state(event.id)
        award = (
            Award.objects.select_related("racer")
            .filter(event=event, pk=award_id)
            .first()
        )
        if award is None:
            raise AwardFailure("no-such-award", "That award is gone. Reload the page.")
        if award.award_type_id == AD_HOC:
            raise AwardFailure("adhoc", "Clear an ad hoc award from its racer.")
        racer = award.racer
        if racer is not None:
            Award.objects.filter(pk=award.pk).update(racer=None)
    return award, racer


def set_ad_hoc_award_by_id(event, racer_id: int, name: str) -> Racer:
    """The racer sheet's ad hoc award field: `set_ad_hoc_award`, with the
    name trimmed and kept to the column."""
    name = name.strip()
    if len(name) > NAME_LIMIT:
        raise AwardFailure("toolong", f"Keep the name to {NAME_LIMIT} characters.")
    with transaction.atomic():
        lock_race_state(event.id)
        racer = _event_racer(event, racer_id)
        set_ad_hoc_award(event, racer, name)
    return racer


def refusal_text(refusal: Refusal, award: Award, racer: Racer) -> str:
    """A refusal as the judging page says it, naming the group, subgroup or
    partition. The award and racer carry their group, subgroup and
    partition, which the caller reads with them."""
    match refusal.code:
        case "wrong-class":
            return f"For {award.group.name} only"
        case "wrong-subgroup":
            if award.subgroup_id is not None:
                return f"For {award.subgroup.name} only"
            return f"Not open to {racer.subgroup.name}"
        case "not-in-list":
            return f"Not open to {racer.partition.name}"
    return "Not eligible for any award"
