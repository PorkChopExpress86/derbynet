import django.db.models.deletion
from django.db import migrations, models

ID = ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"))


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name="EventInfo", fields=[ID, ("slug", models.CharField(max_length=64))]),
        migrations.CreateModel(name="Racer", fields=[ID, ("name", models.CharField(max_length=100)), ("car_number", models.IntegerField())]),
        migrations.CreateModel(name="Heat", fields=[ID, ("number", models.IntegerField()), ("status", models.CharField(default="scheduled", max_length=16))]),
        migrations.CreateModel(name="Result", fields=[
            ID,
            ("lane", models.IntegerField()),
            ("time", models.FloatField(null=True)),
            ("heat", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="eventapp.heat")),
            ("racer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="eventapp.racer")),
        ]),
        migrations.CreateModel(name="TimerState", fields=[ID, ("last_contact", models.FloatField(default=0))]),
    ]
