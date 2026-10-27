"""D35: award partition lists. No stored list is every partition, those
added later too; a narrowed list keeps exactly its partitions; deleting a
partition takes it out of every list, and a list left empty is all
partitions again."""

import pytest

from awards.models import AwardPartition
from awards.services import partition_list, set_partition_list
from roster.models import Partition
from roster.services import groups
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
    assert "Wolves" in named(partition_list(event, group=lions_group))

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
