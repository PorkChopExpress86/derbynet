"""The migration that stores whether each role keeps its seeded password
(#441) checks the hashes an existing install already holds."""

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.services import roles_with_default_password

BEFORE = [("accounts", "0002_seed_default_roles")]
AFTER = [("accounts", "0003_role_keeps_seeded_password")]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_migration_names_only_the_roles_that_keep_their_seeded_password():
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    Role = executor.loader.project_state(BEFORE).apps.get_model("accounts", "Role")
    Role.objects.filter(name="RaceCrew").update(password_hash=make_password("new"))
    Role.objects.filter(name="Timer").update(password_hash=make_password("tick"))
    Role.objects.create(
        name="Judge",
        password_hash=make_password("murphy"),
        permissions=258,
        position=9,
    )

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(AFTER)

    assert roles_with_default_password() == ["Photo", "RaceCoordinator"]
