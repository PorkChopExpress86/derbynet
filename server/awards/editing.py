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

from awards.listing import AD_HOC
from awards.models import Award, AwardType, BallotAward, Vote
from awards.services import partition_list
from core.models import ActionLog, RaceSettings, RaceState
from core.services import lock_race_state
from racing.engine.phpcompat import php_intval, php_truthy
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Racer,
    Subgroup,
)
from roster.services.groups import _named, labels

# `CLASS_COLUMN_WIDTH` and `RANK_COLUMN_WIDTH`.
NAME_WIDTH = 75
# The award type `award.xbs` gives, "Other".
XBS_AWARD_TYPE = 3
AD_HOC_SORT = 999


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
    return 1 + (_highest_sort(event) or 1)


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
        BallotAward.objects.create(event_id=award.event_id, award=award, depth=depth)
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


def _aggregate_allows(group_id: int, racer: Racer) -> tuple[bool, bool]:
    """Whether a racer is in an aggregate group's groups, through aggregates
    of aggregates, and in its subgroups, when it names any."""
    members, todo = set(), [group_id]
    while todo:
        for constituent in GroupConstituent.objects.filter(
            aggregate_id=todo.pop()
        ).values_list("constituent_id", flat=True):
            if constituent not in members:
                members.add(constituent)
                todo.append(constituent)
    only = set(
        GroupConstituentSubgroup.objects.filter(aggregate_id=group_id).values_list(
            "subgroup_id", flat=True
        )
    )
    return racer.group_id in members, not only or racer.subgroup_id in only


def _is_aggregate(group_id: int) -> bool:
    return (
        GroupConstituent.objects.filter(aggregate_id=group_id).exists()
        or GroupConstituentSubgroup.objects.filter(aggregate_id=group_id).exists()
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
        group_ok = award.group_id is None or award.group_id == racer.group_id
        subgroup_ok = (
            award.subgroup_id is None or award.subgroup_id == racer.subgroup_id
        )
        if award.group_id is not None and _is_aggregate(award.group_id):
            in_groups, subgroup_ok = _aggregate_allows(award.group_id, racer)
            group_ok = group_ok or in_groups
        if racer.exclude:
            raise AwardFailure("ineligible", "Racer not eligible for awards")
        if not group_ok:
            raise AwardFailure("wrong-class", "Award is for a different class")
        if not subgroup_ok:
            raise AwardFailure("wrong-subgroup", "Award is for a different subgroup")
        if racer.partition_id not in {p.pk for p in partition_list(event, award=award)}:
            raise AwardFailure("ineligible", "Racer not eligible for this award")
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
            found = list(
                Racer.objects.filter(
                    event=event, car_number=_php_int(posted["carnumber"])
                )[:2]
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
