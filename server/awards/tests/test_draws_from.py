"""D38: a unit's list offers the partitions its racers can come from, the
same racers the standings rank in that unit's ladder."""

import pytest
from django.utils import timezone

from awards.services import partition_list, set_partition_list
from racing.engine.standings.eligibility import NotEligible
from racing.models import HeatLane, RosterEntry
from racing.services.standings import standings_snapshot
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.groups import GroupRef
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db


def partition(event, name):
    return Partition.objects.get(event=event, name=name)


def named(partitions):
    return {p.name for p in partitions}


@pytest.fixture
def event():
    """Under the custom rule, group Lions holds subgroup Lions (partitions
    Lions and Tigers) and subgroup Wolves, and Parents has a group of its
    own. Each partition has one racer."""
    replay = Replay()
    for den in ("Lions", "Tigers", "Wolves", "Parents"):
        replay.import_racer(f"{den}0", "Racer", den)
    event = replay.event
    groups.form_groups_by_rule(event, "custom")
    lions = partition(event, "Lions").subgroup
    groups.move_partition(partition(event, "Tigers"), GroupRef("rankid", lions.pk))
    groups.move_partition(
        partition(event, "Wolves"), GroupRef("rankid", -lions.group_id)
    )
    Racer.objects.filter(event=event).update(passed_inspection=True)
    return event


def den(event):
    return Group.objects.get(event=event, name="Lions")


def racer(event, name):
    return Racer.objects.get(event=event, first_name=f"{name}0")


def race(event):
    for i, entry in enumerate(
        RosterEntry.objects.filter(event=event, round__number=1).order_by("pk")
    ):
        HeatLane.objects.create(
            event=event,
            round=entry.round,
            heat=i + 1,
            lane=1,
            racer=entry.racer,
            finish_time=3 + i / 100,
            completed=timezone.now(),
        )


def ladder_partitions(ladder):
    ids = [r for rung in ladder for r in rung]
    return set(Racer.objects.filter(pk__in=ids).values_list("partition_id", flat=True))


def assert_lists_offer_every_ranked_partition(event):
    """Every partition the standings rank in a unit's award ladder is one
    the unit's list offers."""
    race(event)
    standings = standings_snapshot(event).standings
    for group in Group.objects.filter(event=event):
        offered = {p.pk for p in partition_list(event, group=group)}
        assert ladder_partitions(standings.award_ladder_class(group.pk)) <= offered
    for subgroup in Subgroup.objects.filter(event=event):
        offered = {p.pk for p in partition_list(event, subgroup=subgroup)}
        assert (
            ladder_partitions(standings.award_ladder_subgroup(subgroup.pk)) <= offered
        )


def test_a_subgroup_offers_only_its_own_partitions(event):
    lions = partition(event, "Lions").subgroup
    assert named(partition_list(event, subgroup=lions)) == {"Lions", "Tigers"}
    with pytest.raises(ValueError):
        set_partition_list(event, [partition(event, "Wolves")], subgroup=lions)


def test_an_aggregate_of_subgroups_offers_only_their_partitions(event):
    lions = partition(event, "Lions").subgroup
    aggregate = groups.create_aggregate_group(event.id, "B", subgroups=[lions])
    assert named(partition_list(event, group=aggregate)) == {"Lions", "Tigers"}
    with pytest.raises(ValueError):
        set_partition_list(event, [partition(event, "Wolves")], group=aggregate)


def test_an_aggregate_of_aggregates_reads_only_its_own_subgroup_filter(event):
    lions = partition(event, "Lions").subgroup
    inner = groups.create_aggregate_group(event.id, "B", subgroups=[lions])
    outer = groups.create_aggregate_group(event.id, "A", constituents=[inner])
    offered = named(partition_list(event, group=outer))
    assert {"Lions", "Tigers", "Wolves"} <= offered
    assert "Parents" not in offered
    assert_lists_offer_every_ranked_partition(event)


def test_racers_a_filtered_move_leaves_behind_keep_their_partition_listed(event):
    parents = partition(event, "Parents").subgroup.group
    set_partition_list(
        event, [partition(event, "Lions"), partition(event, "Tigers")], group=den(event)
    )
    groups.move_partition(
        partition(event, "Tigers"),
        GroupRef("classid", parents.pk),
        from_filter=GroupRef("classid", parents.pk),
    )
    assert racer(event, "Tigers").group_id == den(event).pk
    assert named(partition_list(event, group=den(event))) == {"Lions", "Tigers"}
    race(event)
    rows = standings_snapshot(event).standings.rows
    (tiger,) = [r for r in rows if r.racer.id == racer(event, "Tigers").pk]
    assert NotEligible.PARTITION_NOT_LISTED not in tiger.not_eligible.values()


def test_every_partition_ranked_in_a_unit_is_offered_after_a_filtered_move(event):
    parents = partition(event, "Parents").subgroup.group
    groups.move_partition(
        partition(event, "Tigers"),
        GroupRef("classid", parents.pk),
        from_filter=GroupRef("classid", parents.pk),
    )
    assert_lists_offer_every_ranked_partition(event)


def test_a_partition_moved_out_and_back_stays_out_of_the_standings_list(event):
    """The reconcile that drops Tigers from the group's list on the move
    out marks the Tigers racer not eligible on the group's ladder once
    Tigers is back."""
    group = den(event)
    lions = partition(event, "Lions").subgroup
    set_partition_list(
        event, [partition(event, "Lions"), partition(event, "Tigers")], group=group
    )
    parents = partition(event, "Parents").subgroup
    groups.move_partition(partition(event, "Tigers"), GroupRef("rankid", parents.pk))
    groups.move_partition(partition(event, "Tigers"), GroupRef("rankid", lions.pk))
    race(event)
    rows = standings_snapshot(event).standings.rows
    (tiger,) = [r for r in rows if r.racer.id == racer(event, "Tigers").pk]
    assert NotEligible.PARTITION_NOT_LISTED in tiger.not_eligible.values()
