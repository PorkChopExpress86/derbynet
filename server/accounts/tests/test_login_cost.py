"""What a login costs in password hashes after the roles are seeded again,
as each browser test seeds them (#430).

The re-seed gives every role a new salt, so the hashes the process already
checked are no help: each browser test paid them again, not only each
process's first page, and under load the login's Enter timed out (#383).
The test settings hash with MD5 (`conftest.py`); the production case keeps
the real login on PBKDF2.
"""

import time

import pytest
from django.conf import global_settings
from django.contrib.auth import hashers
from django.test import Client

from accounts.models import Role
from core.tests.factories import EventFactory
from ui.tests.roles import PASSWORDS

pytestmark = pytest.mark.django_db

STEPS = ("seed", "visitor's /", "login", "first /", "second /")


@pytest.fixture
def event():
    return EventFactory()


def seed_again():
    """As the browser conftest does after each flush: new salts."""
    from importlib import import_module

    from django.apps import apps

    Role.objects.all().delete()
    import_module("accounts.migrations.0002_seed_default_roles").seed(apps, None)


def hash_counts(monkeypatch) -> dict[str, int]:
    calls = []
    real = hashers.pbkdf2

    def counted(*args, **kwargs):
        calls.append(None)
        return real(*args, **kwargs)

    monkeypatch.setattr(hashers, "pbkdf2", counted)
    client = Client()
    steps = {
        "seed": seed_again,
        "visitor's /": lambda: client.get("/"),
        "login": lambda: client.post(
            "/login/",
            {"name": "RaceCoordinator", "password": PASSWORDS["RaceCoordinator"]},
        ),
        "first /": lambda: client.get("/"),
        "second /": lambda: client.get("/"),
    }
    counts, replies = {}, {}
    for name in STEPS:
        before, start = len(calls), time.perf_counter()
        replies[name] = steps[name]()
        counts[name] = len(calls) - before
        print(f"{name}: {counts[name]} PBKDF2, {time.perf_counter() - start:.2f} s")
    assert replies["login"].status_code == 302, replies["login"].content
    banner = "Photo, RaceCrew, RaceCoordinator still use their default passwords."
    assert banner in replies["first /"].content.decode()
    return counts


def test_the_test_settings_run_no_pbkdf2(monkeypatch, event):
    assert hash_counts(monkeypatch) == dict.fromkeys(STEPS, 0)


def test_the_production_hasher_pays_one_hash_per_login_and_three_per_first_page(
    monkeypatch, settings, event
):
    settings.PASSWORD_HASHERS = global_settings.PASSWORD_HASHERS

    assert hash_counts(monkeypatch) == {
        "seed": 3,
        "visitor's /": 0,
        "login": 1,
        "first /": 3,
        "second /": 0,
    }
