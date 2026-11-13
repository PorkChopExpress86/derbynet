"""Give every racer a voting token (#206, D86).

Three steps, because `AddField` with a callable default would give every
existing row the same value: add the field nullable, fill each row with its
own GUID, then make it unique and required."""

import uuid

from django.db import migrations, models


def fill(apps, schema_editor):
    Racer = apps.get_model("roster", "Racer")
    for racer in Racer.objects.filter(voting_token=None).only("pk"):
        racer.voting_token = uuid.uuid4()
        racer.save(update_fields=["voting_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("roster", "0004_weigh_in"),
    ]

    operations = [
        migrations.AddField(
            model_name="racer",
            name="voting_token",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="racer",
            name="voting_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
