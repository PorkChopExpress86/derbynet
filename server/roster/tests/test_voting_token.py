"""Each racer's voting token (#206, D86): the migration gives every
existing racer its own, and every way a racer is added makes one."""

import json
import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.permissions import Perm
from core.services import new_event
from legacy_api.tests.conftest import log_in
from roster.models import Racer
from roster.services.racers import insert_new_racer
from roster.tests.factories import GroupFactory, RacerFactory

BEFORE = [("roster", "0004_weigh_in")]
AFTER = [("roster", "0005_voting_token")]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_migration_gives_each_existing_racer_its_own_token():
    """`AddField` with a callable default would give every row one value;
    the migration fills each row with a GUID of its own."""
    event = new_event("Migrated")
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    group = old.get_model("roster", "Group").objects.create(
        event_id=event.pk, name="Lions", sort_order=1
    )
    subgroup = old.get_model("roster", "Subgroup").objects.create(
        event_id=event.pk, group=group, name="Den", sort_order=1
    )
    partition = old.get_model("roster", "Partition").objects.create(
        event_id=event.pk, subgroup=subgroup, name="Den", sort_order=1
    )
    for car in (101, 102, 103):
        old.get_model("roster", "Racer").objects.create(
            event_id=event.pk,
            group=group,
            subgroup=subgroup,
            partition=partition,
            car_number=car,
            first_name=f"F{car}",
            last_name="L",
        )

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(AFTER)

    tokens = list(Racer.objects.values_list("voting_token", flat=True))
    assert len(tokens) == 3
    assert all(isinstance(t, uuid.UUID) for t in tokens)
    assert len(set(tokens)) == 3


pytestmark_db = pytest.mark.django_db


@pytestmark_db
def test_every_new_racer_has_a_token_of_its_own():
    group = GroupFactory()
    made = [RacerFactory(group=group) for _ in range(3)]
    added = insert_new_racer(
        group.event,
        first_name="New",
        last_name="Racer",
        car_number=500,
        partition_name="Late",
    ).racer
    tokens = [r.voting_token for r in [*made, added]]
    assert len(set(tokens)) == 4


def _post(client, **fields):
    reply = client.post("/action.php", fields)
    assert json.loads(reply.content)["outcome"]["summary"] == "success", reply.content


@pytestmark_db
def test_racer_add_import_and_fake_make_tokens(client):
    """`racer.add`, `racer.import` (the roster import) and `racer.fake` give
    each new racer a token of its own."""
    event = new_event("Paths")
    log_in(client, Perm.SET_UP | Perm.REGISTER_NEW_RACER)

    _post(client, action="racer.add", firstname="Ann", lastname="Able", carno="101")
    _post(
        client,
        action="racer.import",
        firstname="Ben",
        lastname="Baker",
        carnumber="102",
        partition="Lions",
    )
    added = Racer.objects.filter(event=event).count()
    _post(client, action="racer.fake", ngroups="2", racers_per_group="4")

    tokens = list(
        Racer.objects.filter(event=event).values_list("voting_token", flat=True)
    )
    assert added == 2
    # racer.fake adds a few racers per group, at random (C57).
    assert len(tokens) > added
    assert len(set(tokens)) == len(tokens)
