"""D16: every manager page warns while a role keeps its seeded password."""

from unittest import mock

import pytest

from accounts import services
from accounts.models import Role
from accounts.services import roles_with_default_password

pytestmark = pytest.mark.django_db

BANNER = "data-default-password-banner"


def change_password(name, password):
    role = Role.objects.get(name=name)
    role.set_password(password)
    role.save()


def test_a_new_install_keeps_three_default_passwords(event):
    assert roles_with_default_password() == ["Photo", "RaceCrew", "RaceCoordinator"]


def test_the_banner_shows_to_a_logged_in_role(login):
    page = login("RaceCrew").get("/").content.decode()

    assert BANNER in page
    assert "Photo, RaceCrew, RaceCoordinator still use their default passwords." in page
    assert "Ask the race coordinator to change them." in page


def test_the_coordinator_gets_a_link_to_change_them(login):
    page = login("RaceCoordinator").get("/").content.decode()

    assert 'href="/settings/roles/"' in page


def test_a_visitor_sees_no_banner(login):
    assert BANNER not in login("anonymous").get("/").content.decode()


def test_the_banner_names_only_the_roles_left(login):
    client = login("RaceCoordinator")
    change_password("Photo", "new-photo")
    change_password("RaceCrew", "new-crew")

    page = client.get("/").content.decode()

    assert "RaceCoordinator still uses its default password." in page


def test_the_banner_goes_once_every_password_changed(login):
    client = login("RaceCoordinator")
    for name in ("Photo", "RaceCrew", "RaceCoordinator"):
        change_password(name, f"new-{name}")

    assert BANNER not in client.get("/").content.decode()


def test_the_seed_migration_reads_the_same_list():
    from importlib import import_module

    from accounts import defaults

    seed = import_module("accounts.migrations.0002_seed_default_roles")

    assert seed.DEFAULT_ROLES is defaults.DEFAULT_ROLES


def test_each_hash_is_checked_once(event):
    services._checked.clear()
    real = services.check_password
    with mock.patch.object(services, "check_password", side_effect=real) as check:
        roles_with_default_password()
        roles_with_default_password()
        assert check.call_count == 3

        change_password("RaceCrew", "new-crew")
        assert roles_with_default_password() == ["Photo", "RaceCoordinator"]
        assert check.call_count == 4
