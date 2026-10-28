"""The class, rank and partition endpoints (#30): PHP's wire contract over the
group services of #25, the action log entries `history.php` reads, and dense
ordering (C16)."""

import json
from unittest import mock
from urllib.parse import urlencode

import pytest

from accounts.permissions import Perm
from core.models import ActionLog, RaceSettings
from core.tests.factories import EventFactory
from legacy_api.tests.conftest import log_in, post
from racing.models import Round
from racing.tests.factories import HeatLaneFactory
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.tests.factories import GroupFactory, RacerFactory, SubgroupFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def act(client, action_name, **fields):
    body = urlencode({"action": action_name, **fields})
    return json.loads(post(client, body).content)


def act_raw(client, body):
    return json.loads(post(client, body).content)


def outcome(reply):
    return reply["outcome"]["code"]


def class_list(client):
    return json.loads(client.get("/action.php", {"query": "class.list"}).content)


def logged(event, prefix=""):
    return [
        row.request
        for row in ActionLog.objects.filter(event=event).order_by("id")
        if row.request["action"].startswith(prefix) and "*" in row.request["action"]
    ]


def dens(event, *names, racers=1):
    """Partitions with racers, made as a racer import makes them."""
    for name in names:
        partition = groups.find_or_create_partition(event.id, name)
        for _ in range(racers):
            RacerFactory(
                event=event,
                group=partition.subgroup.group,
                subgroup=partition.subgroup,
                partition=partition,
            )
    groups.fill_in_missing_roster_entries(event.id)


def named(model, event, name):
    return model.objects.get(event=event, name=name)


def set_rule(event, rule):
    RaceSettings.objects.filter(event=event).update(group_formation_rule=rule)


# ----- Permissions --------------------------------------------------------


@pytest.mark.parametrize(
    "name, code",
    [
        ("class.add", "permission"),
        ("class.edit", "permission"),
        ("class.order", "permission"),
        ("rank.add", "permission"),
        ("rank.move", "permission"),
        ("class.delete", "notauthorized"),
        ("rank.edit", "notauthorized"),
        ("rank.delete", "notauthorized"),
        ("rank.order", "notauthorized"),
        ("partition.edit", "notauthorized"),
        ("partition.delete", "notauthorized"),
        ("partition.order", "notauthorized"),
    ],
)
def test_set_up_actions_refuse_the_race_crew_with_phps_code(client, event, name, code):
    log_in(client, Perm.CONTROL_RACE)

    reply = act(client, name, name="X", classid=1, rankid=1, partitionid=1)

    assert outcome(reply) == code


@pytest.mark.parametrize("name", ["partition.add", "partition.apply-rule"])
def test_control_race_actions_refuse_set_up_alone(client, event, name):
    log_in(client, Perm.SET_UP)

    assert outcome(act(client, name, name="X", rule="custom")) == "notauthorized"


def test_the_race_crew_adds_a_walk_in_partition(client, event):
    log_in(client, Perm.CONTROL_RACE)

    reply = act(client, "partition.add", name="Walk-ins")

    assert outcome(reply) == "success"
    assert reply["partitionid"] == named(Partition, event, "Walk-ins").pk


def test_class_list_needs_a_login(client, event):
    reply = class_list(client)

    assert outcome(reply) == "notauthorized"
    assert "classes" not in reply


# ----- class.list -----------------------------------------------------------


def test_class_list_lists_groups_in_sort_order_then_name_ignoring_case(
    coordinator, event
):
    b = GroupFactory(event=event, name="bears", sort_order=2)
    a = GroupFactory(event=event, name="Arrows", sort_order=2, n_trophies=3)
    c = GroupFactory(event=event, name="Cubs", sort_order=1)
    b2 = SubgroupFactory(group=b, name="two", sort_order=1)
    b1 = SubgroupFactory(group=b, name="One", sort_order=1, n_trophies=2)
    RacerFactory(event=event, group=b, subgroup=b1, partition__subgroup=b1)

    classes = class_list(coordinator)["classes"]

    assert [g["classid"] for g in classes] == [c.pk, a.pk, b.pk]
    assert classes[1] == {
        "classid": a.pk,
        "count": 0,
        "nrounds": 0,
        "ntrophies": 3,
        "name": "Arrows",
        "subgroups": [],
    }
    assert classes[2]["count"] == 1
    assert classes[2]["subgroups"] == [
        {"rankid": b1.pk, "count": 1, "ntrophies": 2, "name": "One"},
        {"rankid": b2.pk, "count": 0, "ntrophies": -1, "name": "two"},
    ]


def test_class_list_counts_rounds_with_a_result(coordinator, event):
    dens(event, "Lions")
    round_ = Round.objects.get(event=event)
    HeatLaneFactory(round=round_, racer=Racer.objects.get(), finish_time=2.5)

    assert class_list(coordinator)["classes"][0]["nrounds"] == 1


def test_class_list_shows_an_aggregate_by_subgroup(coordinator, event):
    dens(event, "Lions", "Tigers")
    tigers = named(Subgroup, event, "Tigers")
    lions = named(Subgroup, event, "Lions")
    act_raw(
        coordinator,
        f"action=class.add&name=Cats&rankid_{tigers.pk}=1&rankid_{lions.pk}=1",
    )

    cats = class_list(coordinator)["classes"][-1]

    assert list(cats) == [
        "classid",
        "count",
        "nrounds",
        "ntrophies",
        "name",
        "aggregate-by-subgroup",
        "subgroups",
        "constituents",
        "constituent-ranks",
    ]
    assert cats["aggregate-by-subgroup"] is True
    assert cats["constituents"] == [
        {"classid": tigers.group_id, "name": "Tigers"},
        {"classid": lions.group_id, "name": "Lions"},
    ]
    assert cats["constituent-ranks"] == [
        {
            "rankid": tigers.pk,
            "name": "Tigers",
            "classid": tigers.group_id,
            "class": "Tigers",
        },
        {
            "rankid": lions.pk,
            "name": "Lions",
            "classid": lions.group_id,
            "class": "Lions",
        },
    ]


# ----- class.add, class.edit, class.delete --------------------------------


def test_class_add_makes_a_group_with_round_one_subgroup_and_partition(
    coordinator, event
):
    set_rule(event, "custom")

    reply = act(coordinator, "class.add", name="  Webelos  ", ntrophies="2")

    assert outcome(reply) == "success"
    assert "classes" not in reply
    group = named(Group, event, "Webelos")
    assert group.n_trophies == 2
    assert Round.objects.filter(group=group, number=1).exists()
    subgroup = Subgroup.objects.get(group=group)
    partition = Partition.objects.get(subgroup=subgroup)
    round_ = Round.objects.get(group=group)
    assert logged(event) == [
        {"action": "class.*create", "class": "Webelos", "classid": group.pk},
        {
            "action": "round.*create",
            "roundid": round_.pk,
            "classid": group.pk,
            "round": 1,
        },
        {
            "action": "partition.*create",
            "partitionid": partition.pk,
            "name": "Webelos",
            "rankid": subgroup.pk,
        },
        {
            "action": "rank.*create",
            "rank": "Webelos",
            "rankid": subgroup.pk,
            "classid": group.pk,
        },
    ]


def test_class_add_makes_an_aggregate_of_constituent_groups(coordinator, event):
    dens(event, "Lions", "Tigers")
    lions, tigers = (named(Group, event, n) for n in ("Lions", "Tigers"))

    reply = act_raw(
        coordinator,
        f"action=class.add&name=Younger&constituent_{lions.pk}=1"
        f"&constituent_{tigers.pk}=1",
    )

    assert outcome(reply) == "success"
    younger = named(Group, event, "Younger")
    assert younger.durable
    assert list(younger.constituents.order_by("name")) == [lions, tigers]
    assert not Round.objects.filter(group=younger).exists()


@pytest.mark.parametrize(
    "fields, code",
    [
        ({}, "missing-args"),
        ({"name": "   "}, "noname"),
        ({"name": "lions"}, "already-exists"),
    ],
)
def test_class_add_failures(coordinator, event, fields, code):
    dens(event, "Lions")

    assert outcome(act(coordinator, "class.add", **fields)) == code
    assert Group.objects.filter(event=event).count() == 1


def test_class_edit_under_by_partition_renames_its_subgroup_and_partition(
    coordinator, event
):
    dens(event, "Lions")
    lions = named(Group, event, "Lions")

    reply = act(
        coordinator, "class.edit", classid=lions.pk, name="Lions 1", ntrophies=-1
    )

    assert outcome(reply) == "success"
    assert named(Subgroup, event, "Lions 1").group == lions
    assert named(Partition, event, "Lions 1").subgroup.group == lions
    lions.refresh_from_db()
    assert (lions.name, lions.n_trophies) == ("Lions 1", None)


def test_class_edit_under_custom_renames_only_the_group(coordinator, event):
    dens(event, "Lions")
    set_rule(event, "custom")
    lions = named(Group, event, "Lions")

    act(coordinator, "class.edit", classid=lions.pk, name="Big Cats")

    assert named(Subgroup, event, "Lions").group.name == "Big Cats"
    assert Partition.objects.filter(event=event, name="Lions").exists()


def test_class_edit_refuses_a_name_another_group_holds(coordinator, event):
    dens(event, "Lions", "Tigers")

    reply = act(
        coordinator,
        "class.edit",
        classid=named(Group, event, "Lions").pk,
        name="TIGERS",
    )

    assert outcome(reply) == "name-taken"


def test_class_delete_returns_the_classes(coordinator, event):
    dens(event, "Lions")
    empty = groups.add_group(event, "Empty")

    reply = act(coordinator, "class.delete", classid=empty.pk)

    assert outcome(reply) == "success"
    assert [c["name"] for c in reply["classes"]] == ["Lions"]
    assert {"action": "class.*delete", "classid": empty.pk} in logged(event)


def test_class_delete_refuses_a_group_with_racers_or_an_aggregate_member(
    coordinator, event
):
    dens(event, "Lions")
    groups.add_group(event, "Cubs")
    cubs = named(Group, event, "Cubs")
    groups.add_group(event, "Pack", constituents=[cubs])

    assert (
        outcome(
            act(coordinator, "class.delete", classid=named(Group, event, "Lions").pk)
        )
        == "existing-racers"
    )
    assert outcome(act(coordinator, "class.delete", classid=cubs.pk)) == "constituent"


def test_an_id_from_another_event_changes_nothing(coordinator, event):
    other = GroupFactory(name="Theirs")

    for name in ("class.edit", "class.delete"):
        assert outcome(act(coordinator, name, classid=other.pk, name="Mine")) == (
            "success"
        )

    other.refresh_from_db()
    assert other.name == "Theirs"


# ----- Ordering (C16) -------------------------------------------------------


def sort_orders(model, event):
    return list(
        model.objects.filter(event=event)
        .order_by("sort_order", "id")
        .values_list("name", "sort_order")
    )


def test_class_order_with_duplicate_sort_orders_renumbers_densely(coordinator, event):
    set_rule(event, "custom")
    for name in "ABCD":
        groups.add_group(event, name)
    a, b, c, d = (named(Group, event, n) for n in "ABCD")
    Group.objects.filter(pk=d.pk).update(sort_order=9)

    # Two rows posted at 1, one at 3, and A and D not posted at all.
    reply = act_raw(
        coordinator,
        f"action=class.order&classid_1={c.pk}&classid_01={b.pk}&classid_3={c.pk}",
    )

    assert outcome(reply) == "success"
    assert sort_orders(Group, event) == [("C", 1), ("B", 2), ("A", 3), ("D", 4)]
    assert [p.name for p in Partition.objects.order_by("sort_order")] == [
        "C",
        "B",
        "A",
        "D",
    ]
    assert logged(event, "partition.*structure")


def test_rank_order_with_duplicate_sort_orders_renumbers_each_group_densely(
    coordinator, event
):
    set_rule(event, "custom")
    cubs = groups.add_group(event, "Cubs")
    for name in ("Lions", "Tigers", "Bears"):
        groups.add_subgroup(event, cubs.pk, name)
    one, lions, tigers, bears = Subgroup.objects.filter(group=cubs).order_by("id")
    Subgroup.objects.filter(pk=bears.pk).update(sort_order=7)

    reply = act_raw(
        coordinator,
        f"action=rank.order&rankid_2={tigers.pk}&rankid_2={lions.pk}"
        f"&rankid_02={bears.pk}",
    )

    assert outcome(reply) == "success"
    assert sort_orders(Subgroup, event) == [
        ("Cubs", 1),
        ("Lions", 2),
        ("Bears", 3),
        ("Tigers", 4),
    ]


def test_partition_order_with_duplicate_sort_orders_renumbers_densely(
    coordinator, event
):
    """PHP's cleanup calls `$db->execute_once`, which does not exist (C16)."""
    dens(event, "A", "B", "C")
    a, b, c = (named(Partition, event, n) for n in "ABC")

    reply = act_raw(
        coordinator,
        f"action=partition.order&partitionid_1={c.pk}&partitionid_1x={a.pk}"
        f"&partitionid_01={b.pk}",
    )

    assert outcome(reply) == "success"
    assert sort_orders(Partition, event) == [("C", 1), ("B", 2), ("A", 3)]
    # Under by-partition the groups follow their partitions.
    assert sort_orders(Group, event) == [("C", 1), ("B", 2), ("A", 3)]


# ----- Ranks ----------------------------------------------------------------


def test_rank_add_appends_a_subgroup_and_returns_the_classes(coordinator, event):
    dens(event, "Bears")
    bears = named(Group, event, "Bears")

    reply = act(coordinator, "rank.add", classid=bears.pk, name="Second")

    assert outcome(reply) == "success"
    assert [s["name"] for s in reply["classes"][0]["subgroups"]] == [
        "Bears",
        "Second",
    ]
    second = named(Subgroup, event, "Second")
    assert second.sort_order == 2
    assert not Partition.objects.filter(subgroup=second).exists()
    assert logged(event, "rank.*create")[-1] == {
        "action": "rank.*create",
        "rank": "Second",
        "rankid": second.pk,
        "classid": bears.pk,
    }


def test_rank_add_failures(coordinator, event):
    other = GroupFactory()

    assert outcome(act(coordinator, "rank.add", name="X")) == "missing-args"
    assert outcome(act(coordinator, "rank.add", classid=other.pk, name=" ")) == (
        "noname"
    )
    reply = act(coordinator, "rank.add", classid=other.pk, name="X")
    assert reply["outcome"] == {
        "summary": "failure",
        "code": "noclass",
        "description": f"No class has classid {other.pk}",
    }


def test_rank_edit_renames_the_subgroup_and_its_partition(coordinator, event):
    dens(event, "Lions")
    set_rule(event, "one-group")
    lions = named(Subgroup, event, "Lions")

    reply = act(coordinator, "rank.edit", rankid=lions.pk, name="Lions 1", ntrophies=4)

    assert outcome(reply) == "success"
    assert reply["classes"][0]["subgroups"][0]["name"] == "Lions 1"
    assert named(Partition, event, "Lions 1").subgroup_id == lions.pk
    lions.refresh_from_db()
    assert lions.n_trophies == 4


def test_rank_delete_refuses_the_last_subgroup_and_one_with_racers(coordinator, event):
    dens(event, "Bears")
    bears = named(Group, event, "Bears")
    first = named(Subgroup, event, "Bears")

    assert outcome(act(coordinator, "rank.delete", rankid=first.pk)) == "last-rank"
    act(coordinator, "rank.add", classid=bears.pk, name="Second")
    assert outcome(act(coordinator, "rank.delete", rankid=first.pk)) == (
        "existing-racers"
    )
    second = named(Subgroup, event, "Second")
    reply = act(coordinator, "rank.delete", rankid=second.pk)
    assert outcome(reply) == "success"
    assert len(reply["classes"][0]["subgroups"]) == 1
    assert {"action": "rank.*delete", "rankid": second.pk} in logged(event)


def test_rank_delete_of_an_unknown_subgroup_is_last_rank_as_in_php(coordinator, event):
    assert outcome(act(coordinator, "rank.delete", rankid=999999)) == "last-rank"


def test_rank_move_to_minus_one_makes_a_new_group(coordinator, event):
    set_rule(event, "custom")
    dens(event, "Bears")
    bears = named(Group, event, "Bears")
    act(coordinator, "rank.add", classid=bears.pk, name="Grizzly")
    moved = named(Subgroup, event, "Bears")

    reply = act(coordinator, "rank.move", rankid=moved.pk, classid=-1)

    assert outcome(reply) == "success"
    moved.refresh_from_db()
    assert moved.group.name == "Bears-1"
    assert logged(event)[-1]["action"] == "partition.*structure"


def test_rank_move_puts_the_racers_in_the_target_group(coordinator, event):
    set_rule(event, "custom")
    dens(event, "Lions", "Tigers")
    lions, tigers = (named(Subgroup, event, n) for n in ("Lions", "Tigers"))

    act(coordinator, "rank.move", rankid=tigers.pk, classid=lions.group_id)

    assert Racer.objects.filter(group=lions.group).count() == 2
    assert not Group.objects.filter(name="Tigers").exists()


# ----- Partitions -----------------------------------------------------------


def test_partition_add_failures(coordinator, event):
    dens(event, "Lions")

    assert outcome(act(coordinator, "partition.add", name=" ")) == "no-name"
    assert outcome(act(coordinator, "partition.add", name="Lions")) == "already-exists"
    assert outcome(act(coordinator, "partition.add", name="LIONS")) == "name-taken"


def test_partition_add_logs_the_create_entries(coordinator, event):
    reply = act(coordinator, "partition.add", name="Lions")

    partition = Partition.objects.get(pk=reply["partitionid"])
    assert [r["action"] for r in logged(event)] == [
        "class.*create",
        "round.*create",
        "rank.*create",
        "partition.*create",
    ]
    assert logged(event)[-1] == {
        "action": "partition.*create",
        "partitionid": partition.pk,
        "name": "Lions",
        "rankid": partition.subgroup_id,
    }


def test_partition_edit_renames_the_group_and_subgroup_under_by_partition(
    coordinator, event
):
    dens(event, "Lions", "Tigers")
    lions = named(Partition, event, "Lions")

    assert outcome(
        act(coordinator, "partition.edit", partitionid=lions.pk, name=" ")
    ) == ("empty-name")
    assert (
        outcome(act(coordinator, "partition.edit", partitionid=lions.pk, name="tigers"))
        == "name-taken"
    )
    reply = act(coordinator, "partition.edit", partitionid=lions.pk, name="Lions 2")
    assert outcome(reply) == "success"
    lions.refresh_from_db()
    assert (lions.name, lions.subgroup.name, lions.subgroup.group.name) == (
        "Lions 2",
        "Lions 2",
        "Lions 2",
    )


def test_partition_delete_refuses_a_partition_with_racers(coordinator, event):
    dens(event, "Lions")

    reply = act(
        coordinator, "partition.delete", partitionid=named(Partition, event, "Lions").pk
    )

    assert outcome(reply) == "in-use"


def test_partition_delete_under_by_partition_deletes_the_group(coordinator, event):
    dens(event, "Lions")
    dens(event, "Empty", racers=0)
    empty = named(Partition, event, "Empty")
    group = empty.subgroup.group

    reply = act(coordinator, "partition.delete", partitionid=empty.pk)

    assert outcome(reply) == "success"
    assert not Group.objects.filter(pk=group.pk).exists()
    assert not Round.objects.filter(group_id=group.pk).exists()
    assert logged(event)[-1]["action"] == "partition.*structure"


def test_partition_delete_runs_in_one_transaction(coordinator, event):
    dens(event, "Lions")
    dens(event, "Empty", racers=0)
    empty = named(Partition, event, "Empty")
    before = (
        Group.objects.count(),
        Partition.objects.count(),
        ActionLog.objects.count(),
    )

    with mock.patch.object(
        groups, "reconcile_partition_lists", side_effect=RuntimeError("late failure")
    ):
        reply = act(coordinator, "partition.delete", partitionid=empty.pk)

    assert outcome(reply) == "server-error"
    after = (
        Group.objects.count(),
        Partition.objects.count(),
        ActionLog.objects.count(),
    )
    # Only the request itself is logged, as PHP logs every action it receives.
    assert after == (before[0], before[1], before[2] + 1)


# ----- partition.apply-rule -----------------------------------------------


def test_apply_rule_regroups_and_logs_the_structure(coordinator, event):
    dens(event, "Lions", "Tigers")

    reply = act(coordinator, "partition.apply-rule", rule="one-group")

    assert outcome(reply) == "success"
    settings = RaceSettings.objects.get(event=event)
    assert (settings.group_formation_rule, settings.use_subgroups) == (
        "one-group",
        True,
    )
    assert [c["name"] for c in class_list(coordinator)["classes"]] == ["All Racers"]
    structure = logged(event)[-1]
    group = Group.objects.get(event=event)
    round_ = Round.objects.get(group=group)
    assert structure == {
        "action": "partition.*structure",
        "class": "All Racers",
        "partitions": [
            {
                "partitionid": p.pk,
                "classid": group.pk,
                "rankid": p.subgroup_id,
                "roundid": round_.pk,
            }
            for p in Partition.objects.order_by("id")
        ],
        "ranks": [],
    }


def test_apply_rule_failures(coordinator, event):
    dens(event, "Lions")

    assert (
        outcome(act(coordinator, "partition.apply-rule", rule="by-den")) == "bad-rule"
    )
    round_ = Round.objects.get(event=event)
    HeatLaneFactory(round=round_, racer=Racer.objects.get(), finish_place=1)
    assert outcome(act(coordinator, "partition.apply-rule", rule="custom")) == (
        "results-exist"
    )


def test_apply_rule_runs_in_one_transaction(coordinator, event):
    dens(event, "Lions", "Tigers")
    before = class_list(coordinator)

    with mock.patch.object(
        groups, "fill_in_missing_roster_entries", side_effect=RuntimeError("late")
    ):
        reply = act(coordinator, "partition.apply-rule", rule="one-group")

    assert outcome(reply) == "server-error"
    assert class_list(coordinator) == before
    assert RaceSettings.objects.get(event=event).group_formation_rule == "by-partition"


def test_ids_from_another_event_change_nothing_there(coordinator, event):
    other = EventFactory()
    dens(other, "Lions", "Tigers")
    dens(other, "Empty", racers=0)
    lions_group, tigers = named(Group, other, "Lions"), named(Group, other, "Tigers")
    lions, empty = named(Subgroup, other, "Lions"), named(Partition, other, "Empty")

    def state():
        return (
            sort_orders(Group, other),
            sort_orders(Subgroup, other),
            sort_orders(Partition, other),
            sorted(Racer.objects.filter(event=other).values_list("pk", "group_id")),
        )

    before = state()
    for body in (
        f"action=class.edit&classid={lions_group.pk}&name=Mine",
        f"action=class.delete&classid={tigers.pk}",
        f"action=class.order&classid_1={tigers.pk}&classid_2={lions_group.pk}",
        f"action=rank.add&classid={lions_group.pk}&name=Mine",
        f"action=rank.edit&rankid={lions.pk}&name=Mine",
        f"action=rank.delete&rankid={lions.pk}",
        f"action=rank.move&rankid={lions.pk}&classid=-1",
        f"action=rank.order&rankid_1={lions.pk}",
        f"action=partition.edit&partitionid={empty.pk}&name=Mine",
        f"action=partition.delete&partitionid={empty.pk}",
        f"action=partition.order&partitionid_1={empty.pk}",
        f"action=partition.move&div_id={empty.pk}&group_field=classid&group_id=-1",
    ):
        act_raw(coordinator, body)

    assert state() == before
