"""Partition car number ranges and the `range-full` answer (#123, D48, C49)."""

import json
from urllib.parse import urlencode

import pytest

from core.models import RaceSettings
from legacy_api.tests.conftest import log_in, post
from roster.models import CarRange, Group, Partition, Racer, Subgroup
from roster.services import car_numbers, groups
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def act(client, action_name, **fields):
    return json.loads(
        post(client, urlencode({"action": action_name, **fields})).content
    )


def code(reply):
    return reply["outcome"]["code"]


def den(event, name):
    return groups.find_or_create_partition(event.id, name)


def add_range(client, partition, first, last):
    reply = act(
        client, "partition.range-add", partitionid=partition.pk, first=first, last=last
    )
    assert code(reply) == "success", reply
    return reply


def check_in(client, partition, number=None, **fields):
    fields = {"firstname": "A", "lastname": "B", "partitionid": partition.pk, **fields}
    if number is not None:
        fields["carno"] = number
    return act(client, "racer.add", **fields)


def fill(event, partition, numbers):
    for number in numbers:
        RacerFactory(
            event=event,
            group=partition.subgroup.group,
            subgroup=partition.subgroup,
            partition=partition,
            car_number=number,
        )


def suggestion(client, partition):
    rows = car_numbers.next_car_numbers(partition.event_id)
    return {r["partitionid"]: r["next_carnumber"] for r in rows}[partition.pk]


def test_the_suggestion_moves_to_the_next_range_once_the_first_is_full(
    coordinator, event
):
    lions = den(event, "Lions")
    add_range(coordinator, lions, 100, 102)
    add_range(coordinator, lions, 400, 499)
    assert suggestion(coordinator, lions) == 100

    fill(event, lions, [100, 101])
    assert suggestion(coordinator, lions) == 102

    fill(event, lions, [102])
    assert suggestion(coordinator, lions) == 400
    check_in(coordinator, lions)
    assert Racer.objects.filter(event=event).latest("pk").car_number == 400


def test_a_full_partition_answers_range_full_until_a_range_is_added(coordinator, event):
    lions = den(event, "Lions")
    add_range(coordinator, lions, 100, 101)
    fill(event, lions, [100, 101])
    assert suggestion(coordinator, lions) is None

    reply = check_in(coordinator, lions)

    assert code(reply) == "range-full"
    assert reply["partitionid"] == lions.pk
    assert Racer.objects.filter(event=event).count() == 2
    add_range(coordinator, lions, 400, 499)
    assert code(check_in(coordinator, lions)) == "success"


def test_a_range_that_overlaps_another_partition_is_refused(coordinator, event):
    lions, tigers = den(event, "Lions"), den(event, "Tigers")
    add_range(coordinator, lions, 100, 199)

    reply = act(
        coordinator,
        "partition.range-add",
        partitionid=tigers.pk,
        first=199,
        last=250,
    )

    assert code(reply) == "range-overlap"
    assert list(CarRange.objects.values_list("partition__name", "first", "last")) == [
        ("Lions", 100, 199)
    ]


def test_a_range_ending_below_its_start_is_refused(coordinator, event):
    lions = den(event, "Lions")

    reply = act(
        coordinator, "partition.range-add", partitionid=lions.pk, first=50, last=40
    )

    assert code(reply) == "bad-range"
    assert not CarRange.objects.exists()


def test_a_number_outside_the_ranges_is_accepted_with_a_warning(coordinator, event):
    lions = den(event, "Lions")
    add_range(coordinator, lions, 100, 199)

    inside = check_in(coordinator, lions, 150)
    outside = check_in(coordinator, lions, 7)

    assert "warnings" not in inside
    assert code(outside) == "success"
    assert any("outside" in w for w in outside["warnings"])
    assert Racer.objects.filter(event=event, car_number=7).exists()


def test_a_partition_with_no_range_accepts_any_number_without_a_warning(
    coordinator, event
):
    lions = den(event, "Lions")

    reply = check_in(coordinator, lions, 7)

    assert "warnings" not in reply


def test_reordering_the_partitions_moves_no_range(coordinator, event):
    lions, tigers = den(event, "Lions"), den(event, "Tigers")
    add_range(coordinator, lions, 100, 199)
    add_range(coordinator, tigers, 200, 299)

    reply = act(
        coordinator,
        "partition.order",
        partitionid_1=tigers.pk,
        partitionid_2=lions.pk,
    )

    assert code(reply) == "success"
    assert suggestion(coordinator, lions) == 100
    assert suggestion(coordinator, tigers) == 200


def test_the_poll_partitions_carry_ranges_only_when_there_are_some(coordinator, event):
    lions = den(event, "Lions")
    den(event, "Tigers")
    add_range(coordinator, lions, 100, 199)

    poll = json.loads(
        coordinator.get(
            "/action.php", {"query": "poll", "values": "partitions"}
        ).content
    )

    by_name = {p["name"]: p for p in poll["partitions"]}
    assert by_name["Lions"]["carranges"] == [
        {"rangeid": lions.car_ranges.get().pk, "first": 100, "last": 199}
    ]
    assert "carranges" not in by_name["Tigers"]


def test_a_range_can_be_listed_and_removed(coordinator, event):
    lions = den(event, "Lions")
    add_range(coordinator, lions, 100, 199)
    listing = json.loads(
        coordinator.get("/action.php", {"query": "partition.range-list"}).content
    )
    (rows,) = listing["partition-ranges"]
    assert rows["partitionid"] == lions.pk

    reply = act(
        coordinator, "partition.range-remove", rangeid=rows["ranges"][0]["rangeid"]
    )

    assert code(reply) == "success"
    assert reply["ranges"] == []
    assert not CarRange.objects.exists()


def test_a_caller_without_set_up_is_refused(client, event):
    lions = den(event, "Lions")
    log_in(client, 0, role="RaceCrew")

    added = act(client, "partition.range-add", partitionid=lions.pk, first=1, last=9)
    removed = act(client, "partition.range-remove", rangeid=1)
    listing = json.loads(
        client.get("/action.php", {"query": "partition.range-list"}).content
    )

    assert (code(added), code(removed), code(listing)) == ("notauthorized",) * 3
    assert "partition-ranges" not in listing
    assert not CarRange.objects.exists()


@pytest.mark.parametrize(
    "rule, group_name, partition_name",
    [("by-partition", "Lions 1", "Lions 1"), ("one-group", "All Racers", "Lions 1")],
)
def test_rank_edit_renames_the_group_only_under_by_partition(
    coordinator, event, rule, group_name, partition_name
):
    """C49: PHP renames the subgroup and the partition, never the group."""
    RaceSettings.objects.filter(event=event).update(group_formation_rule=rule)
    lions = den(event, "Lions")

    reply = act(coordinator, "rank.edit", rankid=lions.subgroup_id, name="Lions 1")

    assert code(reply) == "success"
    assert Subgroup.objects.get(pk=lions.subgroup_id).name == "Lions 1"
    assert Group.objects.get(pk=lions.subgroup.group_id).name == group_name
    assert Partition.objects.get(pk=lions.pk).name == partition_name


def test_editing_a_car_number_outside_the_ranges_warns(coordinator, event):
    lions = den(event, "Lions")
    add_range(coordinator, lions, 100, 199)
    check_in(coordinator, lions, 150)
    racer = Racer.objects.get(event=event, car_number=150)

    reply = act(coordinator, "racer.edit", racer=racer.pk, carno=7)

    assert code(reply) == "success"
    assert any("outside" in w for w in reply["warning"])


def test_rank_edit_leaves_the_group_when_subgroups_are_on(coordinator, event):
    RaceSettings.objects.filter(event=event).update(use_subgroups=True)
    lions = den(event, "Lions")
    group_name = lions.subgroup.group.name

    act(coordinator, "rank.edit", rankid=lions.subgroup_id, name="Lions 1")

    assert Group.objects.get(pk=lions.subgroup.group_id).name == group_name
