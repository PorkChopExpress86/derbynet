"""D16: every manager page warns while a role keeps its seeded password."""

from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.test import Client

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


def test_a_change_on_the_roles_page_takes_its_role_off(login):
    """`accounts.services.change_password`, which the roles page calls."""
    client = login("RaceCoordinator")

    client.post("/settings/roles/Photo/password/", {"password": "zoom"})

    page = client.get("/").content.decode()
    # The span starts the list, so the three-role sentence does not match.
    assert "<span>RaceCrew, RaceCoordinator still use their default passwords." in page


def test_a_password_changed_back_to_the_seeded_one_is_named_again(login):
    client = login("RaceCoordinator")
    client.post("/settings/roles/Photo/password/", {"password": "zoom"})

    client.post("/settings/roles/Photo/password/", {"password": "flashbulb"})

    page = client.get("/").content.decode()
    assert "Photo, RaceCrew, RaceCoordinator still use their default passwords." in (
        page
    )


def test_a_flush_and_re_seed_names_every_seeded_role_again(login):
    """As the browser conftest re-seeds after each test's flush."""
    before = login("RaceCrew")
    for name in ("Photo", "RaceCrew", "RaceCoordinator"):
        change_password(name, f"new-{name}")
    assert BANNER not in before.get("/").content.decode()
    Role.objects.all().delete()

    seed = import_module("accounts.migrations.0002_seed_default_roles").seed
    seed(apps, None)

    page = login("RaceCrew").get("/").content.decode()
    assert "Photo, RaceCrew, RaceCoordinator still use their default passwords." in (
        page
    )


ROLE_FILES = Path(__file__).parents[2] / "accounts" / "tests" / "data"


def import_roles(name):
    call_command("import_roles", str(ROLE_FILES / name), stdout=StringIO())


def test_an_import_with_new_passwords_takes_the_banner_away(event):
    """`config-roles-hand-edited.json` gives each seeded role it keeps a new
    password, the coordinator "n3w-pass", and has no Photo."""
    import_roles("config-roles-hand-edited.inc")
    client = Client()
    response = client.post(
        "/login/", {"name": "RaceCoordinator", "password": "n3w-pass"}
    )
    assert response.status_code == 302, response.content

    assert BANNER not in client.get("/").content.decode()


def test_an_import_of_the_seeded_passwords_keeps_the_banner(login):
    """`config-roles-default.json` holds the passwords a new install seeds."""
    before = login("RaceCrew")
    for name in ("Photo", "RaceCrew", "RaceCoordinator"):
        change_password(name, f"new-{name}")
    assert BANNER not in before.get("/").content.decode()

    import_roles("config-roles-default.inc")

    page = login("RaceCrew").get("/").content.decode()
    assert "Photo, RaceCrew, RaceCoordinator still use their default passwords." in (
        page
    )


def test_a_role_no_install_seeds_is_never_named(login):
    """A role whose hash is written without `set_password` keeps whatever
    flag it had, so the banner names only the roles seeded with a password,
    as before #441: not a new role, and not Timer, seeded with none."""
    Role.objects.create(
        name="Judge", password_hash=make_password("x"), permissions=258, position=9
    )
    Role.objects.filter(name="Timer").update(
        password_hash=make_password("tick"), keeps_seeded_password=True
    )

    page = login("RaceCrew").get("/").content.decode()

    assert "<span>Photo, RaceCrew, RaceCoordinator still use their default" in page
    assert "Judge" not in page
    assert "Timer" not in page
