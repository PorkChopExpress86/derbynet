import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("eventapp", "0003_racer_name_length")]
    operations = [
        migrations.CreateModel(name="Award", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=100)),
            ("racer", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="eventapp.racer")),
        ]),
        migrations.AddIndex(model_name="result", index=models.Index(fields=["heat", "lane"], name="result_heat_lane")),
    ]
