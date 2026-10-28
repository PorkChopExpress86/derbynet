"""`partition.move` (#30, S9). PHP appends the posted `from_group_field` to
its SQL, so any `CONTROL_RACE` caller could rewrite the statement. Django
accepts only `classid` or `rankid` and refuses anything else unchanged."""

import json
from urllib.parse import urlencode

import pytest

from accounts.permissions import Perm
from core.models import RaceSettings
from legacy_api.tests.conftest import log_in, post
from racing.models import RosterEntry
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.structure import classes
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def crew(client, event):
    log_in(client, Perm.CONTROL_RACE)
    RaceSettings.objects.filter(event=event).update(group_formation_rule="custom")
    for name in ("Lions", "Tigers"):
        partition = groups.find_or_create_partition(event.id, name)
        for _ in range(2):
            RacerFactory(
                event=event,
                group=partition.subgroup.group,
                subgroup=partition.subgroup,
                partition=partition,
            )
    groups.fill_in_missing_roster_entries(event.id)
    return client


def move(client, **fields):
    body = urlencode({"action": "partition.move", **fields})
    return json.loads(post(client, body).content)


def snapshot(event):
    return (
        classes(event),
        sorted(
            Racer.objects.filter(event=event).values_list(
                "pk", "group_id", "subgroup_id", "partition_id"
            )
        ),
        sorted(RosterEntry.objects.filter(event=event).values_list("round", "racer")),
        sorted(Partition.objects.filter(event=event).values_list("pk", "subgroup")),
    )


def ids(event):
    lions = Partition.objects.get(event=event, name="Lions")
    tigers = Group.objects.get(event=event, name="Tigers")
    return lions, tigers


@pytest.mark.parametrize(
    "field",
    [
        "classid OR 1=1",
        "classid = classid OR 1",
        "partitionid",
        "racerid",
        "classid; DELETE FROM roster_racer",
    ],
)
def test_an_injected_from_group_field_fails_and_changes_nothing(crew, event, field):
    lions, tigers = ids(event)
    before = snapshot(event)

    reply = move(
        crew,
        div_id=lions.pk,
        group_field="classid",
        group_id=tigers.pk,
        from_group_field=field,
        from_group_id=lions.subgroup.group_id,
    )

    assert reply["outcome"]["summary"] == "failure"
    assert reply["outcome"]["code"] == "bad-from-group"
    assert snapshot(event) == before


@pytest.mark.parametrize("field", ["classid OR 1=1", "", "name"])
def test_a_group_field_other_than_classid_or_rankid_fails(crew, event, field):
    lions, tigers = ids(event)
    before = snapshot(event)

    reply = move(crew, div_id=lions.pk, group_field=field, group_id=tigers.pk)

    assert reply["outcome"]["code"] == "bad-group"
    assert snapshot(event) == before


def test_a_target_in_another_event_fails(crew, event):
    lions, _ = ids(event)
    stranger = GroupFactory()
    before = snapshot(event)

    reply = move(crew, div_id=lions.pk, group_field="classid", group_id=stranger.pk)

    assert reply["outcome"]["code"] == "bad-group"
    assert snapshot(event) == before


def test_an_unknown_partition_fails(crew, event):
    assert move(crew, div_id=999999, group_field="classid", group_id=-1)["outcome"][
        "code"
    ] == ("no-such-partition")


def test_move_into_a_group_puts_the_partition_and_its_racers_there(crew, event):
    lions, tigers = ids(event)

    reply = move(crew, div_id=lions.pk, group_field="classid", group_id=tigers.pk)

    assert reply["outcome"]["code"] == "success"
    lions.refresh_from_db()
    assert lions.subgroup.group == tigers
    assert lions.subgroup.name == "Lions"
    assert Racer.objects.filter(group=tigers).count() == 4
    # The emptied Lions group goes, and every racer is in round 1 of Tigers.
    assert not Group.objects.filter(event=event, name="Lions").exists()
    assert set(RosterEntry.objects.values_list("round__group", flat=True)) == {
        tigers.pk
    }


def test_move_with_a_from_group_moves_only_the_racers_in_it(crew, event):
    lions, tigers = ids(event)
    lions_group = lions.subgroup.group
    elsewhere = Racer.objects.filter(partition=lions).first()
    other = groups.add_group(event, "Other")
    Racer.objects.filter(pk=elsewhere.pk).update(
        group=other, subgroup=Subgroup.objects.get(group=other)
    )

    reply = move(
        crew,
        div_id=lions.pk,
        group_field="rankid",
        group_id=Subgroup.objects.get(group=tigers).pk,
        from_group_field="classid",
        from_group_id=lions_group.pk,
    )

    assert reply["outcome"]["code"] == "success"
    assert Racer.objects.get(pk=elsewhere.pk).group == other
    assert Racer.objects.filter(group=tigers).count() == 3


def test_move_to_a_negative_classid_makes_a_group_named_for_the_partition(crew, event):
    lions, tigers = ids(event)
    move(crew, div_id=lions.pk, group_field="classid", group_id=tigers.pk)

    reply = move(crew, div_id=lions.pk, group_field="classid", group_id=-1)

    assert reply["outcome"]["code"] == "success"
    assert Racer.objects.filter(group__name="Lions").count() == 2
