"""The group structure as the legacy API lists it (#30).

`classes()` is `json_all_classes()` from `website/inc/json-classes.inc`,
`partitions()` is `json_partitions()` from `website/inc/json-partitions.inc`,
and `partition_structure()` is the request `record_action_partition_structure()`
in `website/inc/partitions.inc` writes to the action log, which `history.php`
replays. Names sort with case ignored, as PHP's `COLLATE NOCASE` columns do.
"""

from django.db.models import Count, Q

from core.models import RaceSettings
from racing.models import HAS_RESULT, HeatLane, Round
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Subgroup,
)

# The class name `record_action_partition_structure()` writes under
# `one-group`, whatever the group is called now.
ONE_GROUP_CLASS = "All Racers"


def _fold(name: str) -> str:
    """`COLLATE NOCASE`: ASCII letters only."""
    return name.translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _trophies(n) -> int:
    return -1 if n is None else n


def _rounds_with_results(event) -> dict[int, int]:
    """Group id to the number of its rounds with at least one result."""
    counts: dict[int, int] = {}
    for group_id, _round_id in (
        HeatLane.objects.filter(event=event)
        .filter(HAS_RESULT)
        .values_list("round__group_id", "round_id")
        .distinct()
    ):
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def classes(event) -> list[dict]:
    """`json_all_classes()`: every group in sort order, then name, with its
    subgroups in the same order, and an aggregate's constituents in the order
    it was given them."""
    groups = sorted(
        Group.objects.filter(event=event).annotate(n=Count("racers")),
        key=lambda g: (g.sort_order, _fold(g.name), g.pk),
    )
    subgroups: dict[int, list[Subgroup]] = {}
    for subgroup in Subgroup.objects.filter(event=event).annotate(n=Count("racers")):
        subgroups.setdefault(subgroup.group_id, []).append(subgroup)
    names = {g.pk: g.name for g in groups}
    constituents: dict[int, list[int]] = {}
    for aggregate_id, constituent_id in (
        GroupConstituent.objects.filter(event=event)
        .order_by("id")
        .values_list("aggregate_id", "constituent_id")
    ):
        constituents.setdefault(aggregate_id, []).append(constituent_id)
    constituent_ranks: dict[int, list[Subgroup]] = {}
    for link in (
        GroupConstituentSubgroup.objects.filter(event=event)
        .select_related("subgroup")
        .order_by("id")
    ):
        constituent_ranks.setdefault(link.aggregate_id, []).append(link.subgroup)
    nrounds = _rounds_with_results(event)

    result = []
    for group in groups:
        entry = {
            "classid": group.pk,
            "count": group.n,
            "nrounds": nrounds.get(group.pk, 0),
            "ntrophies": _trophies(group.n_trophies),
            "name": group.name,
        }
        if group.pk in constituent_ranks:
            entry["aggregate-by-subgroup"] = True
        entry["subgroups"] = [
            {
                "rankid": s.pk,
                "count": s.n,
                "ntrophies": _trophies(s.n_trophies),
                "name": s.name,
            }
            for s in sorted(
                subgroups.get(group.pk, []),
                key=lambda s: (s.sort_order, _fold(s.name), s.pk),
            )
        ]
        if group.pk in constituents:
            entry["constituents"] = [
                {"classid": c, "name": names[c]} for c in constituents[group.pk]
            ]
        if group.pk in constituent_ranks:
            entry["constituent-ranks"] = [
                {
                    "rankid": s.pk,
                    "name": s.name,
                    "classid": s.group_id,
                    "class": names[s.group_id],
                }
                for s in constituent_ranks[group.pk]
            ]
        result.append(entry)
    return result


def partitions(event) -> list[dict]:
    """`json_partitions()`: every partition in sort order, with its racer
    count and its one group and subgroup."""
    rows = (
        Partition.objects.filter(event=event)
        .select_related("subgroup")
        .annotate(n=Count("racers"))
        .order_by("sort_order", "id")
    )
    return [
        {
            "partitionid": p.pk,
            "name": p.name,
            "sortorder": p.sort_order,
            "count": p.n,
            "classids": [p.subgroup.group_id],
            "rankids": [p.subgroup_id],
        }
        for p in rows
    ]


def partition_structure(event_id) -> dict:
    """The `partition.*structure` request: each partition, and each subgroup
    with no partition, joined to round 1 of its group, by id. The keys are
    the ones `history.php` reads, so imported and new history read alike."""
    first_rounds: dict[int, list[int]] = {}
    for group_id, round_id in (
        Round.objects.filter(event_id=event_id, number=1)
        .order_by("id")
        .values_list("group_id", "id")
    ):
        first_rounds.setdefault(group_id, []).append(round_id)
    parts = [
        {
            "partitionid": p.pk,
            "classid": p.subgroup.group_id,
            "rankid": p.subgroup_id,
            "roundid": round_id,
        }
        for p in Partition.objects.filter(event_id=event_id)
        .select_related("subgroup")
        .order_by("id")
        for round_id in first_rounds.get(p.subgroup.group_id, [])
    ]
    ranks = [
        {"rankid": s.pk, "classid": s.group_id, "roundid": round_id}
        for s in Subgroup.objects.filter(event_id=event_id)
        .filter(~Q(pk__in=Partition.objects.values("subgroup_id")))
        .order_by("id")
        for round_id in first_rounds.get(s.group_id, [])
    ]
    request: dict = {"action": "partition.*structure"}
    rule = RaceSettings.objects.get(event_id=event_id).group_formation_rule
    if rule == "one-group":
        request["class"] = ONE_GROUP_CLASS
    request["partitions"] = parts
    request["ranks"] = ranks
    return request
