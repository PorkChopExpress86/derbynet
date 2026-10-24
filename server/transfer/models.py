from django.db import models

from core.models import EventScoped


class LegacyId(EventScoped):
    """The new id of one row that `import_derbynet` read from a legacy database.

    Imported events would collide on legacy ids in one database, so ids are
    never copied (#12). `model` is the new row's model label, such as
    `roster.racer`. Imported `ActionLog` requests keep their legacy ids, and
    the Retrospective page resolves them through this map.
    """

    model = models.CharField(max_length=100)
    legacy_id = models.BigIntegerField()
    new_id = models.BigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "model", "legacy_id"],
                name="legacyid_unique_legacy_id",
            ),
            models.UniqueConstraint(
                fields=["event", "model", "new_id"],
                name="legacyid_unique_new_id",
            ),
        ]

    def __str__(self):
        return f"{self.model} {self.legacy_id} -> {self.new_id}"
