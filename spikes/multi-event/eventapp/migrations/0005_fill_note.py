from django.db import migrations


def fill_note(apps, schema_editor):
    Racer = apps.get_model("eventapp", "Racer")
    db = schema_editor.connection.alias
    racers = list(Racer.objects.using(db).all())
    for r in racers:
        r.note = f"car {r.car_number}"
    Racer.objects.using(db).bulk_update(racers, ["note"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("eventapp", "0004_award_result_index")]
    operations = [migrations.RunPython(fill_note, migrations.RunPython.noop)]
