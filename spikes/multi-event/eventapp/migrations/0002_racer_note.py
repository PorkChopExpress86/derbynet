from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("eventapp", "0001_initial")]
    operations = [migrations.AddField(model_name="racer", name="note", field=models.TextField(default=""))]
