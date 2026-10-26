from dataclasses import dataclass
from functools import cached_property

from django.db.models import Exists, OuterRef

from core.models import FullFieldRule, RaceSettings, RaceState
from racing.engine.standings import oracle
from racing.engine.standings.eligibility import AwardSettings
from racing.engine.standings.scoring import Scoring
from racing.engine.standings.speed_awards import (
    CurrentAward,
    SpeedAwards,
    TrophySettings,
    current_speed_award,
    speed_awards,
)
from racing.models import HAS_RESULT, HeatLane, RosterEntry, Round
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


_cache: dict[int, Snapshot] = {}


def standings_snapshot(event) -> Snapshot:
    """The event's standings, computed again only when a result, a racer,
    a roster, a round, a group or a setting they read has changed.

    The cache compares the rows read with the rows it was computed from,
    so no writer has to bump a version, and the 0.5 s `award.current` poll
    reads the rows but does not compute the standings and awards again."""
    inputs = _read(event)
    cached = _cache.get(event.id)
    if cached is not None and cached.inputs == inputs:
        return cached
    snapshot = Snapshot(inputs)
    _cache[event.id] = snapshot
    return snapshot


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
        oracle.Round(*row)
        for row in Round.objects.filter(event=event)
        .annotate(
            finished=Exists(lanes) & ~Exists(lanes.exclude(HAS_RESULT)),
        )
        .order_by("id")
        .values_list("id", "group_id", "number", "finished")
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
                partition_counts_for_all_partition_awards=counts,
            ),
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
            counts,
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
            "partition__counts_for_all_partition_awards",
        )
    )
    entered = RosterEntry.objects.filter(
        round=OuterRef("round"), racer=OuterRef("racer")
    )
    results = tuple(
        oracle.Result(*row)
        for row in HeatLane.objects.filter(event=event)
        .filter(HAS_RESULT)
        .filter(Exists(entered))
        .order_by("id")
        .values_list("round_id", "racer_id", "finish_time", "finish_place")
    )
    return _Inputs(
        event=oracle.Event(
            groups=groups,
            subgroups=subgroups,
            rounds=rounds,
            racers=racers,
            results=results,
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


def _full_field_calc(settings: RaceSettings) -> int:
    if settings.full_field_rule == FullFieldRule.GROUP:
        return settings.full_field_group_id
    if settings.full_field_rule == FullFieldRule.NONE:
        return -1
    return 0
