from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType

from django.db.models import Exists, OuterRef

from awards.models import AwardPartition
from core.models import FullFieldRule, RaceSettings, RaceState
from racing.engine.standings import oracle
from racing.engine.standings.closed_lanes import HeatResult, counted_results
from racing.engine.standings.eligibility import AwardSettings, AwardUnit
from racing.engine.standings.scoring import Scoring
from racing.engine.standings.speed_awards import (
    CurrentAward,
    SpeedAwards,
    TrophySettings,
    current_speed_award,
    speed_awards,
)
from racing.models import HAS_RESULT, HeatLane, LaneClosure, RosterEntry, Round
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Racer,
    Subgroup,
)


@dataclass(frozen=True)
class _Inputs:
    event: oracle.Event
    trophies: TrophySettings


class Snapshot:
    def __init__(self, inputs: _Inputs):
        self.inputs = inputs
        self.standings = oracle.Standings(inputs.event)

    @cached_property
    def speed_awards(self) -> SpeedAwards:
        return speed_awards(self.standings, self.inputs.trophies)


def standings_snapshot(event) -> Snapshot:
    """The event's standings, computed from its rows as they are now.

    Nothing is cached. Reading the rows takes most of the time (#106: 8.8 ms
    of 11.6 ms for 200 racers and 1200 results on SQLite, from
    `tools/time_standings.py`), so a cache keyed on the rows would save only
    the 2.5 ms of computing, even for the 0.5 s `award.current` poll."""
    return Snapshot(_read(event))


def current_award(event) -> CurrentAward | None:
    """The speed award that `RaceState.current_award_key` names, or None
    when the key names a judged award (#33) or no award."""
    key = RaceState.objects.get(event=event).current_award_key
    snapshot = standings_snapshot(event)
    return current_speed_award(key, snapshot.standings, snapshot.speed_awards.awards)


def _read(event) -> _Inputs:
    settings = RaceSettings.objects.get(event=event)
    constituents: dict[int, list[int]] = {}
    for aggregate_id, constituent_id in (
        GroupConstituent.objects.filter(event=event)
        .order_by("id")
        .values_list("aggregate_id", "constituent_id")
    ):
        constituents.setdefault(aggregate_id, []).append(constituent_id)
    subgroup_filters: dict[int, list[int]] = {}
    for aggregate_id, subgroup_id in (
        GroupConstituentSubgroup.objects.filter(event=event)
        .order_by("id")
        .values_list("aggregate_id", "subgroup_id")
    ):
        subgroup_filters.setdefault(aggregate_id, []).append(subgroup_id)

    groups = tuple(
        oracle.Group(
            id=id,
            name=name,
            sort_order=sort_order,
            n_trophies=n_trophies,
            constituents=tuple(constituents.get(id, ())),
            subgroup_ids=tuple(subgroup_filters.get(id, ())),
        )
        for id, name, sort_order, n_trophies in Group.objects.filter(event=event)
        .order_by("id")
        .values_list("id", "name", "sort_order", "n_trophies")
    )
    subgroups = tuple(
        oracle.Subgroup(*row)
        for row in Subgroup.objects.filter(event=event)
        .order_by("id")
        .values_list("id", "group_id", "name", "sort_order", "n_trophies")
    )
    lanes = HeatLane.objects.filter(round=OuterRef("pk"))
    rounds = tuple(
        oracle.Round(*row[:4], None if row[4] is None else Scoring(row[4]))
        for row in Round.objects.filter(event=event)
        .annotate(
            finished=Exists(lanes) & ~Exists(lanes.exclude(HAS_RESULT)),
        )
        .order_by("id")
        .values_list("id", "group_id", "number", "finished", "scoring")
    )
    racers = tuple(
        oracle.Racer(
            id=id,
            first_name=first,
            last_name=last,
            car_number=car,
            car_name=car_name,
            group_id=group_id,
            subgroup_id=subgroup_id,
            awards=AwardSettings(
                not_eligible_for_any_award=exclude,
                not_eligible_for_speed_awards=no_speed,
            ),
            partition_id=partition_id,
        )
        for (
            id,
            first,
            last,
            car,
            car_name,
            group_id,
            subgroup_id,
            exclude,
            no_speed,
            partition_id,
        ) in Racer.objects.filter(event=event, passed_inspection=True)
        .order_by("id")
        .values_list(
            "id",
            "first_name",
            "last_name",
            "car_number",
            "car_name",
            "group_id",
            "subgroup_id",
            "exclude",
            "not_eligible_for_speed_awards",
            "partition_id",
        )
    )
    entered = RosterEntry.objects.filter(
        round=OuterRef("round"), racer=OuterRef("racer")
    )
    rows = (
        HeatLane.objects.filter(event=event)
        .filter(HAS_RESULT)
        .annotate(entered=Exists(entered))
        .order_by("id")
        .values_list(
            "round_id",
            "heat",
            "lane",
            "finish_time",
            "finish_place",
            "racer_id",
            "entered",
        )
    )
    # A closed lane's results still place the other cars of its heat
    # before they move up, so they are read whether entered or not.
    results = tuple(
        oracle.Result(r.round_id, r.key[0], r.finish_time, r.finish_place)
        for r in counted_results(
            (HeatResult(*row[:5], key=row[5:]) for row in rows),
            closed_lanes_by_round(event),
        )
        if r.key[1]
    )
    return _Inputs(
        event=oracle.Event(
            groups=groups,
            subgroups=subgroups,
            rounds=rounds,
            racers=racers,
            results=results,
            partition_lists=_partition_lists(event),
            settings=oracle.Settings(
                use_points=settings.use_points,
                scoring=Scoring(settings.scoring),
                lane_count=settings.lane_count,
                use_subgroups=settings.use_subgroups,
                full_field_calc=_full_field_calc(settings),
                supergroup_label=settings.supergroup_label,
            ),
        ),
        trophies=TrophySettings(
            n_pack=settings.n_pack_trophies,
            n_group=settings.n_den_trophies,
            n_subgroup=settings.n_rank_trophies,
            one_trophy_per=settings.one_trophy_per,
        ),
    )


def closed_lanes_by_round(event) -> dict[int, int]:
    """`{round id: mask}` of the lanes closed in each round, reopened or
    not, whose results the standings drop (#108)."""
    closed: dict[int, int] = {}
    for round_id, mask in LaneClosure.objects.filter(
        event=event, round__isnull=False
    ).values_list("round_id", "mask"):
        closed[round_id] = closed.get(round_id, 0) | mask
    return closed


def _partition_lists(event) -> MappingProxyType:
    """The narrowed lists of the speed-award units (D35)."""
    lists: dict[AwardUnit, set[int]] = {}
    for group_id, subgroup_id, partition_id in AwardPartition.objects.filter(
        event=event, award__isnull=True
    ).values_list("group_id", "subgroup_id", "partition_id"):
        lists.setdefault(AwardUnit(group_id, subgroup_id), set()).add(partition_id)
    return MappingProxyType({u: frozenset(p) for u, p in lists.items()})


def _full_field_calc(settings: RaceSettings) -> int:
    if settings.full_field_rule == FullFieldRule.GROUP:
        return settings.full_field_group_id
    if settings.full_field_rule == FullFieldRule.NONE:
        return -1
    return 0
