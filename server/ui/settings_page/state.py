"""What the settings page shows (#48): each section's fields with their
values, the lane lock, the folder badges, and each partition's weigh-in."""

from dataclasses import dataclass, field

from django.conf import settings as django_settings
from django.db.models import Count, Q

from core import diagnostics, settings_page
from core.models import RaceSettings
from core.settings_form import lanes_locked
from core.settings_page import FIELDS, SECTION_TITLES, Field, Kind, Section
from roster.models import Partition
from roster.services import weigh_in
from ui.playlist import state as playlist_state


@dataclass
class FieldView:
    field: Field
    value: object
    choices: list = field(default_factory=list)
    disabled: bool = False
    # A folder's badge (`diagnostics.Status`).
    status: str = ""
    # The folder settings are locked (`DERBYNET_LOCK_SETTINGS`): shown, not edited.
    read_only: bool = False
    saved: bool = False
    error: str = ""

    @property
    def key(self) -> str:
        return self.field.key

    @property
    def kind(self) -> str:
        return self.field.kind.value


@dataclass
class SectionView:
    key: str
    title: str
    fields: list[FieldView]


def field_view(
    event, setting: Field, *, row=None, values=None, locked=None
) -> FieldView:
    row = row or RaceSettings.for_event(event)
    values = values if values is not None else settings_page.read_values(event)
    locked = lanes_locked(event) if locked is None else locked
    value = settings_page.value_of(row, values, setting)
    view = FieldView(setting, value, list(settings_page.options(setting)))
    view.disabled = setting.lane_lock and locked
    if setting.kind is Kind.DIRECTORY:
        view.status = diagnostics.folder_status(str(value))
        view.read_only = django_settings.LOCK_SETTINGS
    return view


@dataclass
class WeighInRow:
    partition: Partition
    mode: str
    unit: str
    # The maximum in the unit, as a bare number, or "".
    maximum: str
    # Racers of the partition with a weigh-in recorded, which a mode change
    # clears (`set_partition_weigh_in`).
    weighed: int
    saved: bool = False
    error: str = ""


def weigh_in_row(partition: Partition, weighed: int | None = None) -> WeighInRow:
    if weighed is None:
        weighed = _weighed(Partition.objects.filter(pk=partition.pk))[partition.pk]
    maximum = (
        weigh_in.in_unit(partition.max_weight_grams, partition.weight_unit)
        if partition.max_weight_grams is not None and partition.weight_unit
        else ""
    )
    return WeighInRow(
        partition, partition.weigh_in_mode, partition.weight_unit, maximum, weighed
    )


def _weighed(partitions) -> dict[int, int]:
    counted = partitions.annotate(
        n=Count(
            "racers",
            filter=Q(racers__weigh_in_passed__isnull=False)
            | Q(racers__weight_grams__isnull=False),
        )
    )
    return {p.pk: p.n for p in counted}


def weigh_in_rows(event) -> list[WeighInRow]:
    partitions = list(Partition.objects.filter(event=event).order_by("sort_order"))
    counts = _weighed(Partition.objects.filter(event=event))
    return [weigh_in_row(p, counts.get(p.pk, 0)) for p in partitions]


@dataclass
class Page:
    sections: list[SectionView]
    lane_count: int
    lane_colors: list[str]
    lanes_locked: bool
    settings_locked: bool
    weigh_in: list[WeighInRow]
    time_zone: diagnostics.TimeZone
    partition_label: str
    scheduling: object


def lane_colors(event) -> list[str]:
    """Each lane's color, for the lane colors partial."""
    return settings_page.lane_colors(RaceSettings.for_event(event))


def build(event) -> Page:
    row = RaceSettings.for_event(event)
    values = settings_page.read_values(event)
    locked = lanes_locked(event)
    sections = [
        SectionView(
            s.value,
            SECTION_TITLES[s],
            [
                field_view(event, setting, row=row, values=values, locked=locked)
                for setting in FIELDS
                if setting.section is s
            ],
        )
        for s in Section
    ]
    return Page(
        sections=sections,
        lane_count=row.lane_count,
        lane_colors=settings_page.lane_colors(row),
        lanes_locked=locked,
        settings_locked=django_settings.LOCK_SETTINGS,
        weigh_in=weigh_in_rows(event),
        time_zone=diagnostics.time_zone(),
        partition_label=row.partition_label,
        scheduling=playlist_state.scheduling(event),
    )


# The choices every settings template reads.
CHOICES = {
    "colors": settings_page.LANE_COLORS,
    "modes": Partition.WeighInMode.choices,
    "units": Partition.WeightUnit.choices,
}
