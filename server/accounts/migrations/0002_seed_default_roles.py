"""The roles and passwords `write_config_roles_file()` writes for a new PHP
install, so `testing/default.passwords` stays true (#15, D16)."""

from django.contrib.auth.hashers import make_password
from django.db import migrations

# (name, password, permissions, interactive). RaceCrew's 8687 is
# VIEW_RACE_RESULTS | VIEW_AWARDS | CHECK_IN_RACERS | REVERT_CHECK_IN
# | REGISTER_NEW_RACER | EDIT_RACER | ASSIGN_RACER_IMAGE | JUDGING | PHOTO_UPLOAD.
DEFAULT_ROLES = [
    ("", "", 1, True),
    ("Timer", "", 4096, False),
    ("Photo", "flashbulb", 8192, False),
    ("RaceCrew", "murphy", 8687, True),
    ("RaceCoordinator", "doyourbest", -1, True),
]
POST_SETUP_ROLE = "RaceCoordinator"


def seed(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.bulk_create(
        Role(
            name=name,
            password_hash=make_password(password) if password else "",
            permissions=permissions,
            interactive=interactive,
            position=position,
            post_setup=name == POST_SETUP_ROLE,
        )
        for position, (name, password, permissions, interactive) in enumerate(
            DEFAULT_ROLES
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
