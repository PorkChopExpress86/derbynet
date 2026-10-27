"""Award partition lists (D35): the partitions each award unit can go to.

A unit is the pack, a group, a subgroup, or a judged award, named by at
most one of `group`, `subgroup` and `award`. These defaults wait on the
owner's confirmation, and each is one line below:

- A unit with no stored list goes to every partition, those added later
  too. Setting a list back to every partition, or to none, stores nothing,
  so the unit goes to every partition again.
- A narrowed list keeps exactly its partitions, so a new partition stays
  out of it.
- Deleting a partition deletes it from every list (`on_delete=CASCADE`).
"""

from collections.abc import Iterable

from django.db import transaction

from awards.models import Award, AwardPartition
from core.services import lock_race_state
from roster.models import Group, Partition, Subgroup


def _unit(group, subgroup, award) -> dict:
    if sum(x is not None for x in (group, subgroup, award)) > 1:
        raise ValueError("an award unit is one of a group, a subgroup or an award")
    return {"group": group, "subgroup": subgroup, "award": award}


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
    partitions = Partition.objects.filter(event=event).order_by("sort_order", "id")
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
    """Narrow the unit's list to `partitions`, or, given every partition or
    none, go back to all partitions."""
    unit = _unit(group, subgroup, award)
    chosen = {p.pk for p in partitions}
    every = set(Partition.objects.filter(event=event).values_list("id", flat=True))
    if not chosen <= every:
        raise ValueError("a partition of another event")
    with transaction.atomic():
        lock_race_state(event.id)
        AwardPartition.objects.filter(event=event, **unit).delete()
        if chosen and chosen != every:
            AwardPartition.objects.bulk_create(
                AwardPartition(event=event, partition_id=p, **unit)
                for p in sorted(chosen)
            )
