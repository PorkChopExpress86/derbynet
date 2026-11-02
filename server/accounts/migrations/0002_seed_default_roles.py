"""Seeds the default roles of `accounts.defaults` (#15, D16)."""

from django.contrib.auth.hashers import make_password
from django.db import migrations

from accounts.defaults import DEFAULT_ROLES, POST_SETUP_ROLE


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
