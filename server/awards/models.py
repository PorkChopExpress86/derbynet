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


class AwardPartition(EventScoped):
    """One partition in an award unit's narrowed partition list (D35, D38).

    A unit is the pack (no group, subgroup or award), a group, an aggregate
    included, a subgroup, or a judged award. A unit with no row is "all
    partitions", every partition it draws from, those added or moved in
    later too, so neither an empty list nor a full one is stored. A row
    names only a partition its unit draws from. Deleting a partition, or
    the unit, deletes its rows, and `awards.services` keeps the rest of
    that rule. Speed awards read the lists now;
    `award.winner` reads a judged award's in #33. The legacy API neither
    reads nor writes them.
    """

    group = models.ForeignKey(
        "roster.Group",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    subgroup = models.ForeignKey(
        "roster.Subgroup",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    award = models.ForeignKey(
        Award, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    partition = models.ForeignKey(
        "roster.Partition", on_delete=models.CASCADE, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(group__isnull=True, subgroup__isnull=True)
                    | models.Q(group__isnull=True, award__isnull=True)
                    | models.Q(subgroup__isnull=True, award__isnull=True)
                ),
                name="award_partition_one_unit",
            ),
            models.UniqueConstraint(
                fields=["event", "partition"],
                condition=models.Q(
                    group__isnull=True, subgroup__isnull=True, award__isnull=True
                ),
                name="award_partition_unique_pack",
            ),
            models.UniqueConstraint(
                fields=["group", "partition"],
                condition=models.Q(group__isnull=False),
                name="award_partition_unique_group",
            ),
            models.UniqueConstraint(
                fields=["subgroup", "partition"],
                condition=models.Q(subgroup__isnull=False),
                name="award_partition_unique_subgroup",
            ),
            models.UniqueConstraint(
                fields=["award", "partition"],
                condition=models.Q(award__isnull=False),
                name="award_partition_unique_award",
            ),
        ]
