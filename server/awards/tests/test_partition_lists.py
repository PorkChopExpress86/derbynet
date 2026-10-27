"""D35 and D38: award partition lists. No stored list is every partition the
award draws from, those added or moved in later too; a narrowed list keeps
exactly its partitions; deleting a partition, or moving it out of a group,
takes it out of the lists that no longer draw from it, and a list left
empty or holding every partition it draws from is all partitions again."""

import pytest

from awards.models import AwardPartition
from awards.services import partition_list, set_partition_list
from awards.tests.factories import AwardFactory
from roster.models import Partition, Subgroup
from roster.services import groups
from roster.services.groups import GroupRef
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    replay = Replay()
    for name in ("Lions", "Tigers", "Parents"):
        groups.add_partition(replay.event, name)
    return replay.event


def named(partitions):
    return [p.name for p in partitions]


def partition(event, name):
    return Partition.objects.get(event=event, name=name)


def test_a_narrowed_list_keeps_its_partitions_as_partitions_come_and_go(event):
    lions, tigers = partition(event, "Lions"), partition(event, "Tigers")
    lions_group = lions.subgroup.group
    set_partition_list(event, [lions, tigers])
    assert named(partition_list(event)) == ["Lions", "Tigers"]

    groups.add_partition(event, "Wolves")
    assert named(partition_list(event)) == ["Lions", "Tigers"]
    assert named(partition_list(event, group=lions_group)) == ["Lions"]

    groups.delete_partition(tigers)
    assert named(partition_list(event)) == ["Lions"]

    groups.delete_partition(Partition.objects.get(pk=lions.pk))
    assert not AwardPartition.objects.filter(event=event).exists()
    assert named(partition_list(event)) == ["Parents", "Wolves"]


@pytest.mark.parametrize("chosen", ["every", "none"])
def test_every_partition_or_none_goes_back_to_all(event, chosen):
    set_partition_list(event, [partition(event, "Lions")])
    everyone = list(Partition.objects.filter(event=event))
    set_partition_list(event, everyone if chosen == "every" else [])
    assert not AwardPartition.objects.filter(event=event).exists()
    groups.add_partition(event, "Wolves")
    assert named(partition_list(event)) == ["Lions", "Tigers", "Parents", "Wolves"]


def test_a_unit_is_one_of_a_group_a_subgroup_or_an_award(event):
    lions = partition(event, "Lions")
    with pytest.raises(ValueError):
        set_partition_list(
            event, [lions], group=lions.subgroup.group, subgroup=lions.subgroup
        )


# ----- D38: a list offers only the partitions the award can draw from -----


@pytest.fixture
def den(event):
    """Under the custom rule, one group "Lions" holding Lions, Tigers and
    Wolves, with Parents in a group of its own."""
    groups.add_partition(event, "Wolves")
    groups.form_groups_by_rule(event, "custom")
    for name in ("Tigers", "Wolves"):
        move_to(event, name, "Lions")
    return partition(event, "Lions").subgroup.group


def move_to(event, name, beside):
    """Move partition `name` into the subgroup of partition `beside`."""
    target = partition(event, beside).subgroup_id
    groups.move_partition(partition(event, name), GroupRef("rankid", target))


def move_out(event, name):
    move_to(event, name, "Parents")


def test_a_group_award_offers_only_its_groups_partitions(event, den):
    assert set(named(partition_list(event, group=den))) == {"Lions", "Tigers", "Wolves"}
    judged = AwardFactory(event=event, group=den)
    assert set(named(partition_list(event, award=judged))) == {
        "Lions",
        "Tigers",
        "Wolves",
    }
    pack_award = AwardFactory(event=event)
    assert "Parents" in named(partition_list(event, award=pack_award))
    for unit in ({"group": den}, {"award": judged}):
        with pytest.raises(ValueError):
            set_partition_list(event, [partition(event, "Parents")], **unit)
        assert not AwardPartition.objects.filter(event=event).exists()


def test_every_partition_of_the_group_is_all_partitions(event, den):
    set_partition_list(event, list(partition_list(event, group=den)), group=den)
    assert not AwardPartition.objects.filter(event=event).exists()


def test_all_partitions_follows_a_partition_moved_into_the_group(event, den):
    assert "Parents" not in named(partition_list(event, group=den))
    move_to(event, "Parents", "Lions")
    assert set(named(partition_list(event, group=den))) == {
        "Lions",
        "Tigers",
        "Wolves",
        "Parents",
    }


def test_all_partitions_of_the_pack_follows_a_partition_added_later(event, den):
    groups.add_partition(event, "Bears")
    assert set(named(partition_list(event))) == {
        "Lions",
        "Tigers",
        "Wolves",
        "Parents",
        "Bears",
    }


def test_a_narrowed_group_list_holding_the_rest_after_a_delete_is_all(event, den):
    set_partition_list(
        event, [partition(event, "Lions"), partition(event, "Tigers")], group=den
    )
    groups.delete_partition(partition(event, "Wolves"))
    assert not AwardPartition.objects.filter(event=event).exists()
    move_to(event, "Parents", "Lions")
    assert set(named(partition_list(event, group=den))) == {
        "Lions",
        "Tigers",
        "Parents",
    }


def test_a_narrowed_pack_list_holding_the_rest_after_a_delete_is_all(event, den):
    set_partition_list(
        event, [partition(event, n) for n in ("Lions", "Tigers", "Wolves")]
    )
    groups.delete_partition(partition(event, "Parents"))
    assert not AwardPartition.objects.filter(event=event).exists()


@pytest.mark.parametrize("unit", ["group", "award"])
def test_a_narrowed_list_holding_the_rest_after_a_move_out_is_all(event, den, unit):
    units = {
        "group": {"group": den},
        "award": {"award": AwardFactory(event=event, group=den)},
    }
    set_partition_list(
        event, [partition(event, "Lions"), partition(event, "Tigers")], **units[unit]
    )
    move_out(event, "Wolves")
    assert not AwardPartition.objects.filter(event=event).exists()
    move_to(event, "Parents", "Lions")
    assert set(named(partition_list(event, **units[unit]))) == {
        "Lions",
        "Tigers",
        "Parents",
    }


def test_a_partition_moved_out_leaves_the_groups_narrowed_list(event, den):
    set_partition_list(
        event, [partition(event, "Lions"), partition(event, "Tigers")], group=den
    )
    move_out(event, "Tigers")
    assert named(partition_list(event, group=den)) == ["Lions"]
    move_to(event, "Tigers", "Lions")
    assert named(partition_list(event, group=den)) == ["Lions"]


def test_a_partition_moved_out_stays_in_the_packs_narrowed_list(event, den):
    set_partition_list(event, [partition(event, "Lions"), partition(event, "Tigers")])
    move_out(event, "Tigers")
    assert named(partition_list(event)) == ["Lions", "Tigers"]


def test_a_subgroup_moved_out_takes_its_partitions_out_of_the_groups_list(event, den):
    groups.move_partition(partition(event, "Wolves"), GroupRef("rankid", -den.pk))
    wolves = partition(event, "Wolves").subgroup
    set_partition_list(
        event, [partition(event, "Lions"), partition(event, "Wolves")], group=den
    )
    groups.move_subgroup(wolves, None)
    assert named(partition_list(event, group=den)) == ["Lions"]
    groups.move_subgroup(Subgroup.objects.get(pk=wolves.pk), den)
    assert named(partition_list(event, group=den)) == ["Lions"]


def test_deleting_a_subgroup_leaving_a_full_group_list_is_all(event, den):
    groups.move_partition(partition(event, "Wolves"), GroupRef("rankid", -den.pk))
    wolves = partition(event, "Wolves").subgroup
    set_partition_list(
        event, [partition(event, "Lions"), partition(event, "Tigers")], group=den
    )
    groups.delete_subgroup(wolves)
    assert not AwardPartition.objects.filter(event=event).exists()


def test_deleting_a_group_leaving_a_full_pack_list_is_all(event, den):
    set_partition_list(event, [partition(event, n) for n in ("Lions", "Tigers")])
    groups.delete_partition(partition(event, "Wolves"))
    groups.delete_group(partition(event, "Parents").subgroup.group)
    assert not AwardPartition.objects.filter(event=event).exists()


def test_a_rule_change_keeps_the_packs_list_and_drops_the_groups(event, den):
    narrowed = [partition(event, "Lions"), partition(event, "Tigers")]
    set_partition_list(event, narrowed)
    set_partition_list(event, narrowed, group=den)
    groups.form_groups_by_rule(event, "one-group")
    assert named(partition_list(event)) == ["Lions", "Tigers"]
    assert not AwardPartition.objects.filter(event=event, group__isnull=False).exists()
