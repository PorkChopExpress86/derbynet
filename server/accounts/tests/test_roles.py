from pathlib import Path

import pytest
from django.conf import settings
from django.db import IntegrityError

from accounts.models import Role

pytestmark = pytest.mark.django_db

DEFAULT_PASSWORDS = (
    Path(settings.BASE_DIR).parent / "testing" / "default.passwords"
).read_text()


def default_passwords() -> dict[str, str]:
    return dict(
        line.split(":", 1)
        for line in DEFAULT_PASSWORDS.splitlines()
        if ":" in line and not line.startswith("#")
    )


def test_the_seed_is_phps_default_roles_in_file_order():
    assert [
        (r.name, r.permissions, r.interactive, r.post_setup) for r in Role.objects.all()
    ] == [
        ("", 1, True, False),
        ("Timer", 4096, False, False),
        ("Photo", 8192, False, False),
        ("RaceCrew", 8687, True, False),
        ("RaceCoordinator", -1, True, True),
    ]


def test_the_seeded_passwords_are_the_documented_defaults():
    passwords = default_passwords()

    assert passwords == {
        "RaceCoordinator": "doyourbest",
        "RaceCrew": "murphy",
        "Timer": "",
        "Photo": "flashbulb",
    }
    for name, password in passwords.items():
        assert Role.objects.get(name=name).check_password(password), name


def test_no_password_is_stored_in_plain_text():
    plaintexts = set(default_passwords().values()) - {""}

    for role in Role.objects.all():
        assert role.password_hash not in plaintexts
        assert all(p not in role.password_hash for p in plaintexts)


def test_a_role_without_a_password_takes_only_the_empty_password():
    timer = Role.objects.get(name="Timer")

    assert timer.password_hash == ""
    assert timer.check_password("")
    assert not timer.check_password("anything")


def test_a_numeric_password_is_compared_as_text():
    """PHP's loose `==` takes `1e1` for `10` (S31)."""
    role = Role(name="Numeric", permissions=0, position=9)
    role.set_password("10")

    assert role.check_password("10")
    assert not role.check_password("1e1")
    assert not role.check_password("10.0")


def test_only_one_role_is_the_post_setup_role():
    with pytest.raises(IntegrityError):
        Role.objects.filter(name="RaceCrew").update(post_setup=True)
