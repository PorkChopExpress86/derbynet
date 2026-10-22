from django.db import migrations, models


class Migration(migrations.Migration):
    """On SQLite this rebuilds the racer table."""

    dependencies = [("eventapp", "0002_racer_note")]
    operations = [migrations.AlterField(model_name="racer", name="name", field=models.CharField(max_length=200))]
