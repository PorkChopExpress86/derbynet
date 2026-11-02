"""What the racing groups editor shows (#43): the formation rule, the labels
and trophy counts, the group tree with its subgroups and partitions, the
aggregates, and each award unit's partition list.

Reads only. The editor loads the structure once and every mutation answers
with it again, so nothing here polls.
"""

from dataclasses import dataclass, field

from awards.services import PartitionChoice, partition_choice
from core.models import FullFieldRule, RaceSettings
from core.setup_details import plural
from roster.models import Partition
from roster.services import car_numbers, structure
from roster.services.groups import Labels, Rule, labels

RULES = (
    (Rule.BY_PARTITION.value, "By {partition}"),
    (Rule.ONE_GROUP.value, "One group"),
    (Rule.CUSTOM.value, "Custom"),
)
# PHP's trophy count select: Default, then 0 to 10.
TROPHY_CHOICES = tuple(range(11))


def group_labels(event) -> Labels:
    """The one place the editor reads the labels from, so a rename shows in
    every part of the page (#43)."""
    return labels(RaceSettings.objects.get(event=event))


def rule_choices(found: Labels):
    return [(value, label.format(partition=found.partition)) for value, label in RULES]


@dataclass
class PartitionRow:
    id: int
    name: str
    count: int
    ranges: list = field(default_factory=list)


@dataclass
class SubgroupRow:
    id: int
    name: str
    count: int
    ntrophies: int
    partitions: list[PartitionRow]
    constituent_of: str = ""


@dataclass
class GroupRow:
    id: int
    name: str
    count: int
    nrounds: int
    ntrophies: int
    subgroups: list[SubgroupRow]
    constituent_of: str = ""


@dataclass
class AggregateRow:
    id: int
    name: str
    ntrophies: int
    by_subgroup: bool
    constituents: list[str]
    constituent_of: str = ""


@dataclass
class Tree:
    rule: str
    labels: Labels
    settings: RaceSettings
    groups: list[GroupRow]
    aggregates: list[AggregateRow]
    partitions: list[PartitionRow]
    pack: PartitionChoice

    @property
    def rule_choices(self) -> list[tuple[str, str]]:
        return rule_choices(self.labels)

    @property
    def partitions_label(self) -> str:
        return plural(self.labels.partition)

    @property
    def by_partition(self) -> bool:
        return self.rule == Rule.BY_PARTITION

    @property
    def custom(self) -> bool:
        return self.rule == Rule.CUSTOM

    @property
    def show_partitions(self) -> bool:
        """D50: the partition layer is hidden under by partition, where a
        group, its subgroup and its partition are one thing."""
        return not self.by_partition

    @property
    def calc(self) -> str:
        """The pack aggregate choice as `full-field-calc` text."""
        rule = self.settings.full_field_rule
        if rule == FullFieldRule.GROUP:
            return str(self.settings.full_field_group_id)
        return "-1" if rule == FullFieldRule.NONE else "0"

    @property
    def calc_choices(self) -> list[tuple[str, str]]:
        choices = [("0", "Calculate normally"), ("-1", "Don't calculate")]
        choices += [
            (str(a.id), f"Use standings from {a.name}") for a in self.aggregates
        ]
        return choices

    @property
    def pack_trophies(self) -> bool:
        return (self.settings.n_pack_trophies or 0) > 0


def build(event) -> Tree:
    settings = RaceSettings.objects.get(event=event)
    classes = structure.classes(event)
    ranges = car_numbers.ranges_by_partition(event.pk)
    partitions = [
        PartitionRow(
            p["partitionid"],
            p["name"],
            p["count"],
            car_numbers.range_rows(p["partitionid"], ranges),
        )
        for p in structure.partitions(event)
    ]
    by_subgroup: dict[int, list[PartitionRow]] = {}
    subgroup_of = dict(
        Partition.objects.filter(event=event).values_list("id", "subgroup_id")
    )
    for row in partitions:
        by_subgroup.setdefault(subgroup_of[row.id], []).append(row)

    owner_of_group: dict[int, str] = {}
    owner_of_subgroup: dict[int, str] = {}
    for entry in classes:
        for c in entry.get("constituents", []):
            owner_of_group[c["classid"]] = entry["name"]
        for r in entry.get("constituent-ranks", []):
            owner_of_subgroup[r["rankid"]] = entry["name"]

    groups, aggregates = [], []
    for entry in classes:
        if entry.get("constituents"):
            ranks = entry.get("constituent-ranks")
            aggregates.append(
                AggregateRow(
                    entry["classid"],
                    entry["name"],
                    entry["ntrophies"],
                    bool(ranks),
                    [r["name"] for r in ranks]
                    if ranks
                    else [c["name"] for c in entry["constituents"]],
                    owner_of_group.get(entry["classid"], ""),
                )
            )
            continue
        groups.append(
            GroupRow(
                entry["classid"],
                entry["name"],
                entry["count"],
                entry["nrounds"],
                entry["ntrophies"],
                [
                    SubgroupRow(
                        s["rankid"],
                        s["name"],
                        s["count"],
                        s["ntrophies"],
                        by_subgroup.get(s["rankid"], []),
                        owner_of_subgroup.get(s["rankid"], ""),
                    )
                    for s in entry["subgroups"]
                ],
                owner_of_group.get(entry["classid"], ""),
            )
        )
    return Tree(
        rule=settings.group_formation_rule,
        labels=labels(settings),
        settings=settings,
        groups=groups,
        aggregates=aggregates,
        partitions=partitions,
        pack=partition_choice(event),
    )


def group_row(tree: Tree, group_id: int) -> GroupRow | AggregateRow | None:
    return next((g for g in [*tree.groups, *tree.aggregates] if g.id == group_id), None)


def subgroup_row(tree: Tree, subgroup_id: int) -> tuple[GroupRow, SubgroupRow] | None:
    for group in tree.groups:
        for subgroup in group.subgroups:
            if subgroup.id == subgroup_id:
                return group, subgroup
    return None


def partition_row(tree: Tree, partition_id: int) -> PartitionRow | None:
    return next((p for p in tree.partitions if p.id == partition_id), None)


def group_deletable(tree: Tree, row) -> bool:
    """PHP's rule for the Delete button (`racing-groups-edit.js`): no racers,
    no round with a result, no aggregate draws from it, and under one group
    only an aggregate."""
    if isinstance(row, AggregateRow):
        return not row.constituent_of
    return (
        row.count == 0
        and row.nrounds == 0
        and not row.constituent_of
        and tree.rule != Rule.ONE_GROUP
    )


def subgroup_deletable(group: GroupRow, subgroup: SubgroupRow) -> bool:
    return (
        len(group.subgroups) > 1 and subgroup.count == 0 and not subgroup.constituent_of
    )
