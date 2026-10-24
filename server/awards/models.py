from django.db import models

from core.models import EventScoped


class AwardType(models.Model):
    """Site-level lookup, legacy `AwardTypes`. A data migration seeds ids 1 to 6."""

    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Award(EventScoped):
    """Legacy `Awards`.

    A null `group` means pack-wide, and a null `racer` means no winner yet.
    """

    name = models.CharField(max_length=100)
    award_type = models.ForeignKey(
        AwardType, on_delete=models.PROTECT, related_name="awards"
    )
    group = models.ForeignKey(
        "roster.Group",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="awards",
    )
    subgroup = models.ForeignKey(
        "roster.Subgroup",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="awards",
    )
    racer = models.ForeignKey(
        "roster.Racer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="awards",
    )
    sort = models.IntegerField(default=0)

    def __str__(self):
        return self.name


class BallotAward(EventScoped):
    """Legacy `BallotAwards`: an award that voters can vote on."""

    award = models.OneToOneField(Award, on_delete=models.PROTECT, related_name="ballot")
    depth = models.PositiveSmallIntegerField()


class Voter(EventScoped):
    identifier = models.CharField(max_length=200)
    ip_address = models.CharField(max_length=200, blank=True, default="")
    cast_at = models.DateTimeField(null=True, blank=True)


class Vote(EventScoped):
    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, related_name="votes")
    award = models.ForeignKey(Award, on_delete=models.CASCADE, related_name="votes")
    racer = models.ForeignKey(
        "roster.Racer", on_delete=models.CASCADE, related_name="votes"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "voter", "award", "racer"], name="vote_unique"
            ),
        ]
