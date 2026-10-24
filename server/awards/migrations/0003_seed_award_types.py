from django.core.management.color import no_style
from django.db import migrations

# website/sql/sqlite/award-type-values.inc
AWARD_TYPES = [
    (1, "Design General"),
    (2, "Speed General"),
    (3, "Other"),
    (4, "Design Trophy"),
    (5, "Speed Trophy"),
    (6, "Ad Hoc"),
]


def seed_award_types(apps, schema_editor):
    AwardType = apps.get_model("awards", "AwardType")
    db = schema_editor.connection.alias
    AwardType.objects.using(db).bulk_create(
        AwardType(id=id, name=name) for id, name in AWARD_TYPES
    )
    # Explicit ids leave a Postgres sequence at 1, so the next insert would
    # collide with id 1.
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        for sql in connection.ops.sequence_reset_sql(no_style(), [AwardType]):
            cursor.execute(sql)


def unseed_award_types(apps, schema_editor):
    AwardType = apps.get_model("awards", "AwardType")
    db = schema_editor.connection.alias
    AwardType.objects.using(db).filter(id__in=[id for id, _ in AWARD_TYPES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("awards", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(seed_award_types, unseed_award_types),
    ]
