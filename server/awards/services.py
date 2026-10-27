"""Award partition lists (D35, D38): the partitions each award unit can go to.

A unit is the pack, a group, a subgroup, or a judged award, named by at
most one of `group`, `subgroup` and `award`. A unit draws from the
partitions its racers can come from: the pack from every partition, a group
from the partitions in it (an aggregate from its constituents' or its
subgroups'), a subgroup from its own, and a judged award from those of the
group or subgroup it is for.

- A unit with no stored list is "all partitions", which always means every
  partition the unit draws from, so it picks up a partition added to the
  event or moved into the group later. Setting a list to every partition
  the unit draws from, or to none, stores nothing.
- A narrowed list keeps exactly its partitions, so a new partition stays
  out of it.
- A list never holds a partition its unit does not draw from. Deleting a
  partition deletes it from every list (`on_delete=CASCADE`), and moving it
  out of a group takes it out of that group's lists. After either, a list
  left holding every partition its unit draws from, or none, becomes "all
  partitions" (`reconcile_partition_lists`).
"""

from collections.abc import Iterable

from django.db import transaction

from awards.models import Award, AwardPartition
from core.services import lock_race_state
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Subgroup,
)


def _unit(group, subgroup, award) -> dict:
    if sum(x is not None for x in (group, subgroup, award)) > 1:
        raise ValueError("an award unit is one of a group, a subgroup or an award")
    return {"group": group, "subgroup": subgroup, "award": award}


def _group_draws_from(group_id, seen: frozenset = frozenset()) -> set[int]:
    filters = GroupConstituentSubgroup.objects.filter(aggregate_id=group_id)
    if filters.exists():
        return set(
            Partition.objects.filter(
                subgroup_id__in=filters.values("subgroup_id")
            ).values_list("id", flat=True)
        )
    ids = set(
        Partition.objects.filter(subgroup__group_id=group_id).values_list(
            "id", flat=True
        )
    )
    seen = seen | {group_id}
    for constituent in GroupConstituent.objects.filter(
        aggregate_id=group_id
    ).values_list("constituent_id", flat=True):
        if constituent not in seen:
            ids |= _group_draws_from(constituent, seen)
    return ids


def _draws_from(event_id, group_id, subgroup_id, award_id) -> set[int]:
    """The ids of the partitions the unit draws from."""
    if award_id is not None:
        group_id, subgroup_id = Award.objects.values_list(
            "group_id", "subgroup_id"
        ).get(pk=award_id)
    if subgroup_id is not None:
        partitions = Partition.objects.filter(subgroup_id=subgroup_id)
    elif group_id is not None:
        return _group_draws_from(group_id)
    else:
        partitions = Partition.objects.filter(event_id=event_id)
    return set(partitions.values_list("id", flat=True))


def _ids(group, subgroup, award) -> tuple:
    return tuple(None if x is None else x.pk for x in (group, subgroup, award))


def partition_list(
    event,
    *,
    group: Group | None = None,
    subgroup: Subgroup | None = None,
    award: Award | None = None,
) -> list[Partition]:
    """The partitions the unit's awards can go to, in partition order."""
    stored = AwardPartition.objects.filter(
        event=event, **_unit(group, subgroup, award)
    ).values("partition_id")
    partitions = Partition.objects.filter(
        id__in=_draws_from(event.id, *_ids(group, subgroup, award))
    ).order_by("sort_order", "id")
    if stored.exists():
        partitions = partitions.filter(id__in=stored)
    return list(partitions)


def set_partition_list(
    event,
    partitions: Iterable[Partition],
    *,
    group: Group | None = None,
    subgroup: Subgroup | None = None,
    award: Award | None = None,
) -> None:
    """Narrow the unit's list to `partitions`, or, given every partition the
    unit draws from or none, go back to all partitions. A partition the
    unit does not draw from is a `ValueError`."""
    unit = _unit(group, subgroup, award)
    chosen = {p.pk for p in partitions}
    with transaction.atomic():
        lock_race_state(event.id)
        every = _draws_from(event.id, *_ids(group, subgroup, award))
        if not chosen <= every:
            raise ValueError("a partition the award does not draw from")
        AwardPartition.objects.filter(event=event, **unit).delete()
        if chosen and chosen != every:
            AwardPartition.objects.bulk_create(
                AwardPartition(event=event, partition_id=p, **unit)
                for p in sorted(chosen)
            )


def reconcile_partition_lists(event_id) -> None:
    """After partitions move between groups or are deleted, take each
    partition out of the lists of units that no longer draw from it, and
    make a list holding every partition its unit draws from "all
    partitions". The caller holds the `RaceState` lock."""
    lists: dict[tuple, dict[int, int]] = {}
    for row_id, *unit, partition_id in AwardPartition.objects.filter(
        event_id=event_id
    ).values_list("id", "group_id", "subgroup_id", "award_id", "partition_id"):
        lists.setdefault(tuple(unit), {})[partition_id] = row_id
    doomed: list[int] = []
    for unit, rows in lists.items():
        every = _draws_from(event_id, *unit)
        kept = rows.keys() & every
        doomed.extend(
            row_id for p, row_id in rows.items() if p not in kept or kept == every
        )
    AwardPartition.objects.filter(id__in=doomed).delete()
