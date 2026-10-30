"""Kiosk and scene parameters become the text the client sent (#34).

PHP stores whatever follows `#` in the page and never parses it, so a JSON
field cannot hold `{confetti:false}`. Existing values are written as compact
JSON, the form the kiosk dashboard sends, and an empty object as none.
"""

import json

from django.db import migrations, models


def _to_text(apps, schema_editor):
    for name in ("Kiosk", "SceneKiosk"):
        model = apps.get_model("kiosks", name)
        for row in model.objects.exclude(params={}):
            row.params_text = json.dumps(row.params, separators=(",", ":"))
            row.save(update_fields=["params_text"])


def _to_json(apps, schema_editor):
    for name in ("Kiosk", "SceneKiosk"):
        model = apps.get_model("kiosks", name)
        for row in model.objects.exclude(params_text=""):
            try:
                row.params = json.loads(row.params_text)
            except json.JSONDecodeError:
                row.params = {}
            row.save(update_fields=["params"])


class Migration(migrations.Migration):
    dependencies = [("kiosks", "0001_initial")]

    operations = [
        *(
            migrations.AddField(
                model_name=name,
                name="params_text",
                field=models.TextField(blank=True, default=""),
            )
            for name in ("kiosk", "scenekiosk")
        ),
        migrations.RunPython(_to_text, _to_json),
        *(
            migrations.RemoveField(model_name=name, name="params")
            for name in ("kiosk", "scenekiosk")
        ),
        *(
            migrations.RenameField(
                model_name=name, old_name="params_text", new_name="params"
            )
            for name in ("kiosk", "scenekiosk")
        ),
    ]
