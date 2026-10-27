from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("roster", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="racer",
            name="not_eligible_for_speed_awards",
            field=models.BooleanField(default=False),
        ),
    ]
