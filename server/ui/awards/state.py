"""What the awards editor shows (#45): the awards in sort order, as cards
with name, type, group or subgroup and partition list, and the choices of
its sheet. Reads only: the page writes neither the session nor the
database, and the award types come from a data migration, not a page load
(`awards/migrations/0003_seed_award_types.py`)."""

from dataclasses import dataclass, field

from awards.eligibility import Eligibility
from awards.listing import AD_HOC
from awards.models import Award, AwardType
from core.models import RaceSettings
from core.setup_details import plural
from roster.models import Group, Partition, Subgroup
from roster.services.groups import Labels, labels


@dataclass(frozen=True)
class Card:
    id: int
    name: str
    award_type: str
    scope: str
    partitions: str


@dataclass
class Editor:
    awards: list[Card]
    labels: Labels
    partitions_label: str
    # (value, label, indented): "group,subgroup", 0 for none.
    scopes: list[tuple[str, str, bool]] = field(default_factory=list)
    types: list[tuple[int, str]] = field(default_factory=list)


def scope_value(group_id, subgroup_id) -> str:
    """An award's scope as the sheet's select names it, PHP's
    `class_and_rank`."""
    return f"{group_id or 0},{subgroup_id or 0}"


def build(event) -> Editor:
    settings = RaceSettings.objects.get(event=event)
    found_labels = labels(settings)
    groups = sorted(
        Group.objects.filter(event=event), key=lambda g: (g.sort_order, g.name, g.pk)
    )
    subgroups = sorted(
        Subgroup.objects.filter(event=event),
        key=lambda s: (s.sort_order, s.name, s.pk),
    )
    group_names = {g.pk: g.name for g in groups}
    subgroup_names = {s.pk: s.name for s in subgroups}
    partition_names = dict(
        Partition.objects.filter(event=event).values_list("pk", "name")
    )
    partitions_label = plural(found_labels.partition)
    eligibility = Eligibility(event.id)

    def scope(award) -> str:
        if award.subgroup_id:
            return (
                f"{group_names.get(award.group_id, '')} · "
                f"{subgroup_names.get(award.subgroup_id, '')}"
            )
        if award.group_id:
            return group_names.get(award.group_id, "")
        return found_labels.supergroup

    def partitions(award) -> str:
        if not eligibility.narrowed(award):
            return f"All {partitions_label.lower()}"
        names = sorted(partition_names[p] for p in eligibility.partitions(award))
        return ", ".join(names) or "None"

    awards = sorted(
        Award.objects.filter(event=event)
        .exclude(award_type_id=AD_HOC)
        .select_related("award_type"),
        key=lambda a: (a.sort, a.award_type.name, a.name, a.pk),
    )
    scopes = [(scope_value(None, None), found_labels.supergroup, False)]
    for g in groups:
        scopes.append((scope_value(g.pk, None), g.name, False))
        if settings.use_subgroups:
            scopes += [
                (scope_value(g.pk, s.pk), s.name, True)
                for s in subgroups
                if s.group_id == g.pk
            ]
    return Editor(
        awards=[
            Card(a.pk, a.name, a.award_type.name, scope(a), partitions(a))
            for a in awards
        ],
        labels=found_labels,
        partitions_label=partitions_label,
        scopes=scopes,
        types=list(
            AwardType.objects.exclude(pk=AD_HOC)
            .order_by("name")
            .values_list("pk", "name")
        ),
    )
