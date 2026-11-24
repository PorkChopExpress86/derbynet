"""Store whether each role keeps its seeded password, for the D16 banner
(#441). The fill checks each stored hash once, here, so no page has to."""

from django.contrib.auth.hashers import check_password
from django.db import migrations, models

from accounts.defaults import DEFAULT_ROLES


def fill(apps, schema_editor):
    seeded = {name: password for name, password, _, _ in DEFAULT_ROLES}
    Role = apps.get_model("accounts", "Role")
    for role in Role.objects.all():
        # False for a role seeded with no password, which the banner never
        # lists, and for a name no install seeds (None).
        keeps = check_password(seeded.get(role.name), role.password_hash)
        Role.objects.filter(pk=role.pk).update(keeps_seeded_password=keeps)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_seed_default_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="keeps_seeded_password",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
