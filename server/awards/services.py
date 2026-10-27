"""Award partition lists (D35, D38): the partitions each award unit can go to.

A unit is the pack, a group, a subgroup, or a judged award, named by at
most one of `group`, `subgroup` and `award`. A unit draws from the
partitions its racers can come from, the ones the standings rank in its
ladder. The pack draws from every partition. A group or subgroup draws from
the partitions in it and the partitions of the racers in it. An aggregate
draws from every group that rolls up to it, filtered by its own subgroups
when it has them. A judged award draws from its subgroup, else its group,
else the pack.

- A unit with no stored list is "all partitions", which always means every
  partition the unit draws from, so it picks up a partition added to the
  event or moved into the group later. Setting a list to every partition
  the unit draws from, or to none, stores nothing.
- A narrowed list keeps exactly its partitions, so a new partition stays
  out of it.
- A list never holds a partition its unit does not draw from. Deleting a
  partition deletes it from every list (`on_delete=CASCADE`), and moving it,
  or its subgroup, out of a group takes it out of that group's lists.
  After either, a list left holding every partition its unit draws from,
  or none, becomes "all partitions" (`reconcile_partition_lists`).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.db import transaction

from awards.models import Award, AwardPartition
from core.services import lock_race_state
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Racer,
    Subgroup,
)


def _unit(group, subgroup, award) -> dict:
    if sum(x is not None for x in (group, subgroup, award)) > 1:
        raise ValueError("an award unit is one of a group, a subgroup or an award")
    return {"group": group, "subgroup": subgroup, "award": award}


@dataclass(frozen=True)
class _Places:
    """Where an event's partitions and racers sit, read once.

    `places` holds a (partition, group, subgroup) triple for each
    partition's own subgroup and for each racer's group and subgroup. The
    two differ after a `partition.move` with a filter leaves some of the
    partition's racers behind, and the standings rank racers where the
    racers are.
    """

    partitions: frozenset[int]
    places: frozenset[tuple[int, int, int]]
    constituents: Mapping[int, tuple[int, ...]]
    subgroup_filters: Mapping[int, frozenset[int]]

    @classmethod
    def read(cls, event_id) -> _Places:
        partitions = Partition.objects.filter(event_id=event_id)
        places = set(partitions.values_list("id", "subgroup__group_id", "subgroup_id"))
        places |= set(
            Racer.objects.filter(
                event_id=event_id, partition__isnull=False
            ).values_list("partition_id", "group_id", "subgroup_id")
        )
        constituents: dict[int, list[int]] = {}
        for aggregate, constituent in GroupConstituent.objects.filter(
            event_id=event_id
        ).values_list("aggregate_id", "constituent_id"):
            constituents.setdefault(aggregate, []).append(constituent)
        filters: dict[int, set[int]] = {}
        for aggregate, subgroup in GroupConstituentSubgroup.objects.filter(
            event_id=event_id
        ).values_list("aggregate_id", "subgroup_id"):
            filters.setdefault(aggregate, set()).add(subgroup)
        return cls(
            partitions=frozenset(partitions.values_list("id", flat=True)),
            places=frozenset(places),
            constituents={a: tuple(c) for a, c in constituents.items()},
            subgroup_filters={a: frozenset(s) for a, s in filters.items()},
        )

    def draws_from(self, group_id, subgroup_id) -> set[int]:
        """The partitions a unit draws from, as the standings rank it. An
        aggregate draws from every group that rolls up to it, through
        aggregates of aggregates, filtered by its own subgroups only, as
        `Standings._keys` reads `add_rolls_up_to_relation()`."""
        if subgroup_id is not None:
            return {p for p, _, s in self.places if s == subgroup_id}
        if group_id is None:
            return set(self.partitions)
        members, todo = {group_id}, [group_id]
        while todo:
            for constituent in self.constituents.get(todo.pop(), ()):
                if constituent not in members:
                    members.add(constituent)
                    todo.append(constituent)
        only = self.subgroup_filters.get(group_id)
        return {
            p for p, g, s in self.places if g in members and (only is None or s in only)
        }


def _scope(group_id, subgroup_id, award_id) -> tuple:
    """The group and subgroup whose racers a unit's awards go to."""
    if award_id is None:
        return group_id, subgroup_id
    return tuple(Award.objects.values_list("group_id", "subgroup_id").get(pk=award_id))


def _draws_from(event_id, group_id, subgroup_id, award_id) -> set[int]:
    """The ids of the partitions the unit draws from."""
    return _Places.read(event_id).draws_from(*_scope(group_id, subgroup_id, award_id))


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
    if not lists:
        return
    places = _Places.read(event_id)
    doomed: list[int] = []
    for unit, rows in lists.items():
        every = places.draws_from(*_scope(*unit))
        kept = rows.keys() & every
        doomed.extend(
            row_id for p, row_id in rows.items() if p not in kept or kept == every
        )
    AwardPartition.objects.filter(id__in=doomed).delete()
