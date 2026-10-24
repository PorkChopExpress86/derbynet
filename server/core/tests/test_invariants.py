"""Each of the 40 `query.database.check.inc` tags, with a violating dataset.

A constraint tag must make the database refuse the write. A service tag must
show up in `check_invariants()`. A tag that went away with its column has
nothing left to violate.
"""

import pytest
from django.utils import timezone

from awards.models import Award, AwardType
from awards.tests.factories import AwardFactory
from core.invariants import CHECKS, check_invariants
from core.models import RaceSettings
from core.tests.db import missing_id, refused
from core.tests.factories import EventFactory
from kiosks.models import Scene, SceneKiosk
from kiosks.tests.factories import SceneKioskFactory
from racing.models import HeatLane, PlaylistEntry, RosterEntry, Round
from racing.tests.factories import (
    HeatLaneFactory,
    PlaylistEntryFactory,
    RosterEntryFactory,
    RoundFactory,
)
from roster.models import Group, GroupConstituent, Partition, Racer, Subgroup
from roster.tests.factories import (
    GroupConstituentFactory,
    GroupFactory,
    PartitionFactory,
    RacerFactory,
    SubgroupFactory,
)

pytestmark = pytest.mark.django_db

# Every check_empty() tag in website/ajax/query.database.check.inc.
PHP_TAGS = {
    "awardid-awardtypeid",
    "awardid-classid",
    "awardid-rankid",
    "award-racerid",
    "classes-ranks",
    "classes-round1",
    "aggregate-members",
    "aggregate-ranks",
    "classes-constituent",
    "classes-sortorder",
    "classes-ambiguous-sort",
    "constituentclasses-antecedent",
    "constituentclasses-dependent",
    "racechart-classid",
    "racechart-roundid",
    "racechart-masterheat",
    "racechart-masterheat2",
    "racechart-straight-lanes",
    "racechart-one-car-per-lane",
    "racechart-lane",
    "ranks-classid",
    "ranks-sortorder",
    "ranks-ambiguous-sort",
    "racer-partitionid",
    "racer-classid",
    "racer-rankid",
    "roster-roundid",
    "roster-classid",
    "roster-racerid",
    "roster-duplicates",
    "roster-round-classid",
    "rounds-sequential",
    "rounds-classid",
    "scenekiosk-scene",
    "playlist-seq",
    "playlist-ambiguous-seq",
    "playlist-classid",
    "playlist-round",
    "partition-rankid",
    "partition-sortorder",
}


def _point_at_missing(model, row, field, target):
    model.objects.filter(pk=row.pk).update(**{f"{field}_id": missing_id(target)})


def _set_null(model, row, field):
    model.objects.filter(pk=row.pk).update(**{field: None})


def _twice(factory, **kwargs):
    factory(**kwargs)
    factory(**kwargs)


CONSTRAINT_VIOLATIONS = {
    "awardid-awardtypeid": lambda: _point_at_missing(
        Award, AwardFactory(), "award_type", AwardType
    ),
    "awardid-classid": lambda: _point_at_missing(Award, AwardFactory(), "group", Group),
    "awardid-rankid": lambda: _point_at_missing(
        Award, AwardFactory(), "subgroup", Subgroup
    ),
    "award-racerid": lambda: _point_at_missing(Award, AwardFactory(), "racer", Racer),
    "classes-sortorder": lambda: _set_null(Group, GroupFactory(), "sort_order"),
    "constituentclasses-antecedent": lambda: _point_at_missing(
        GroupConstituent, GroupConstituentFactory(), "constituent", Group
    ),
    "constituentclasses-dependent": lambda: _point_at_missing(
        GroupConstituent, GroupConstituentFactory(), "aggregate", Group
    ),
    "racechart-roundid": lambda: _point_at_missing(
        HeatLane, HeatLaneFactory(), "round", Round
    ),
    "racechart-one-car-per-lane": lambda: _twice(
        HeatLaneFactory, round=RoundFactory(), heat=1, lane=1
    ),
    "racechart-lane": lambda: _set_null(HeatLane, HeatLaneFactory(), "lane"),
    "ranks-classid": lambda: _point_at_missing(
        Subgroup, SubgroupFactory(), "group", Group
    ),
    "ranks-sortorder": lambda: _set_null(Subgroup, SubgroupFactory(), "sort_order"),
    "racer-partitionid": lambda: _point_at_missing(
        Racer, RacerFactory(), "partition", Partition
    ),
    "racer-classid": lambda: _point_at_missing(Racer, RacerFactory(), "group", Group),
    "racer-rankid": lambda: _point_at_missing(
        Racer, RacerFactory(), "subgroup", Subgroup
    ),
    "roster-roundid": lambda: _point_at_missing(
        RosterEntry, RosterEntryFactory(), "round", Round
    ),
    "roster-racerid": lambda: _point_at_missing(
        RosterEntry, RosterEntryFactory(), "racer", Racer
    ),
    "roster-duplicates": lambda: _twice(
        RosterEntryFactory,
        round=(entry := RosterEntryFactory()).round,
        racer=entry.racer,
    ),
    "rounds-classid": lambda: _point_at_missing(Round, RoundFactory(), "group", Group),
    "scenekiosk-scene": lambda: _point_at_missing(
        SceneKiosk, SceneKioskFactory(), "scene", Scene
    ),
    "playlist-seq": lambda: _set_null(PlaylistEntry, PlaylistEntryFactory(), "seq"),
    "playlist-ambiguous-seq": lambda: _twice(
        PlaylistEntryFactory, group=GroupFactory(), seq=7
    ),
    "playlist-classid": lambda: _point_at_missing(
        PlaylistEntry, PlaylistEntryFactory(), "group", Group
    ),
    "playlist-round": lambda: _set_null(
        PlaylistEntry, PlaylistEntryFactory(), "round_number"
    ),
    "partition-rankid": lambda: _point_at_missing(
        Partition, PartitionFactory(), "subgroup", Subgroup
    ),
    "partition-sortorder": lambda: _set_null(
        Partition, PartitionFactory(), "sort_order"
    ),
}

# Each PHP check compared a redundant column that the models do not have.
GONE_WITH_COLUMN = {
    "racechart-classid": HeatLane,
    "roster-classid": RosterEntry,
    "classes-constituent": Group,
}


def test_every_php_tag_is_classified_once():
    classes = [set(CONSTRAINT_VIOLATIONS), set(CHECKS), set(GONE_WITH_COLUMN)]
    assert set().union(*classes) == PHP_TAGS
    assert sum(len(c) for c in classes) == len(PHP_TAGS) == 40
    assert [len(c) for c in classes] == [26, 11, 3]


@pytest.mark.parametrize("tag", sorted(CONSTRAINT_VIOLATIONS))
def test_constraint_tag_is_refused_by_the_database(tag):
    with refused():
        CONSTRAINT_VIOLATIONS[tag]()


@pytest.mark.parametrize("tag", ["racechart-classid", "roster-classid"])
def test_heats_and_rosters_take_their_group_from_the_round(tag):
    fields = {f.name for f in GONE_WITH_COLUMN[tag]._meta.get_fields()}
    assert "round" in fields
    assert not fields & {"group", "classid"}


def test_constituent_rows_are_the_only_store_of_an_aggregates_members():
    constituents = Group._meta.get_field("constituents")
    assert constituents.remote_field.through is GroupConstituent
    link = GroupConstituentFactory()
    assert list(link.aggregate.constituents.all()) == [link.constituent]


def _organic_group(event, **kwargs):
    group = GroupFactory(event=event, **kwargs)
    SubgroupFactory(group=group)
    RoundFactory(group=group, number=1)
    return group


def _aggregate_group(event, **kwargs):
    aggregate = GroupFactory(event=event, **kwargs)
    GroupConstituentFactory(
        aggregate=aggregate, constituent=_organic_group(event), event=event
    )
    return aggregate


def _ids(rows, key="id"):
    return {row[key] for row in rows}


def _build_valid_event():
    event = EventFactory()
    settings = RaceSettings.for_event(event)
    settings.lane_count = 4
    settings.save()
    group = _organic_group(event)
    round1 = group.rounds.get(number=1)
    for lane in (1, 2, 3):
        entry = RosterEntryFactory(round=round1)
        HeatLaneFactory(round=round1, heat=1, lane=lane, racer=entry.racer)
    RoundFactory(group=group, number=2)
    _aggregate_group(event)
    return event


def test_a_valid_event_reports_no_violations():
    assert check_invariants(_build_valid_event()) == {}


def _classes_ranks(event):
    PartitionFactory(subgroup__group=_organic_group(event))
    bare = GroupFactory(event=event)
    RoundFactory(group=bare, number=1)
    return bare.id, "id"


def _classes_round1(event):
    return GroupFactory(event=event).id, "id"


def _aggregate_members(event):
    aggregate = _aggregate_group(event)
    RacerFactory(group=aggregate)
    return aggregate.id, "id"


def _aggregate_ranks(event):
    aggregate = _aggregate_group(event)
    SubgroupFactory(group=aggregate)
    return aggregate.id, "id"


def _classes_ambiguous_sort(event):
    _organic_group(event, sort_order=50)
    _organic_group(event, sort_order=50)
    return 50, "sort_order"


def _ranks_ambiguous_sort(event):
    group = _organic_group(event)
    SubgroupFactory(group=group, sort_order=4)
    SubgroupFactory(group=group, sort_order=4)
    return group.id, "group_id"


def _racechart_masterheat(event):
    round2 = RoundFactory(group=_organic_group(event), number=2)
    HeatLaneFactory(round=round2, heat=1, lane=1, master_heat=1)
    stray = HeatLaneFactory(round=round2, heat=1, lane=2, master_heat=2)
    return stray.id, "id"


def _racechart_masterheat2(event):
    round2 = RoundFactory(group=_organic_group(event), number=2)
    HeatLaneFactory(round=round2, heat=1, lane=1, master_heat=5)
    other = HeatLaneFactory(round=round2, heat=2, lane=1, master_heat=5)
    return other.id, "id"


def _racechart_straight_lanes(event):
    RaceSettings.objects.filter(event=event).update(lane_count=2)
    round1 = _organic_group(event).rounds.get()
    for lane in (1, 2, 3):
        HeatLaneFactory(round=round1, heat=1, lane=lane)
    return round1.id, "round_id"


def _roster_round_classid(event):
    round1 = _organic_group(event).rounds.get()
    stranger = RacerFactory(group=_organic_group(event))
    entry = RosterEntryFactory(round=round1, racer=stranger)
    return entry.id, "id"


def _rounds_sequential(event):
    gap = RoundFactory(group=_organic_group(event), number=3)
    return gap.id, "id"


# Each builder makes a violation in the event and returns the value that
# identifies it in the reported rows, with the row key that holds it.
SERVICE_VIOLATIONS = {
    "classes-ranks": _classes_ranks,
    "classes-round1": _classes_round1,
    "aggregate-members": _aggregate_members,
    "aggregate-ranks": _aggregate_ranks,
    "classes-ambiguous-sort": _classes_ambiguous_sort,
    "ranks-ambiguous-sort": _ranks_ambiguous_sort,
    "racechart-masterheat": _racechart_masterheat,
    "racechart-masterheat2": _racechart_masterheat2,
    "racechart-straight-lanes": _racechart_straight_lanes,
    "roster-round-classid": _roster_round_classid,
    "rounds-sequential": _rounds_sequential,
}


def test_every_service_tag_has_a_violating_dataset():
    assert set(SERVICE_VIOLATIONS) == set(CHECKS)


@pytest.mark.parametrize("tag", sorted(SERVICE_VIOLATIONS))
def test_service_tag_is_reported_by_check_invariants(tag):
    event = EventFactory()
    expected, key = SERVICE_VIOLATIONS[tag](event)

    violations = check_invariants(event)

    assert tag in violations
    assert expected in _ids(violations[tag], key)


@pytest.mark.parametrize("tag", sorted(SERVICE_VIOLATIONS))
def test_service_tag_ignores_another_events_violation(tag):
    clean = _build_valid_event()
    SERVICE_VIOLATIONS[tag](EventFactory())

    assert check_invariants(clean) == {}


def test_a_heat_with_exactly_lane_count_lanes_is_not_reported():
    event = EventFactory()
    RaceSettings.objects.filter(event=event).update(lane_count=3)
    round1 = _organic_group(event).rounds.get()
    for lane in (1, 2, 3):
        HeatLaneFactory(round=round1, heat=1, lane=lane)

    assert "racechart-straight-lanes" not in check_invariants(event)


def test_a_completed_heat_may_share_its_master_heat():
    event = EventFactory()
    group = _organic_group(event)
    round2 = RoundFactory(group=group, number=2)
    HeatLaneFactory(
        round=group.rounds.get(number=1),
        heat=1,
        lane=1,
        master_heat=5,
        completed=timezone.now(),
    )
    HeatLaneFactory(round=round2, heat=1, lane=1, master_heat=5)

    assert "racechart-masterheat2" not in check_invariants(event)


def test_classes_ranks_is_skipped_when_the_event_has_no_partitions():
    event = EventFactory()
    RoundFactory(group__event=event, number=1)

    assert check_invariants(event) == {}
