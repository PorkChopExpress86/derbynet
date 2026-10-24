from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from core.models import EventScoped

# "", "<basename>", or "0-cropped/<basename>". A basename of only dots would
# leave the repository folder.
PHOTO_PATH_REGEX = r"^((0-cropped/)?[^/]*[^/.][^/]*)?$"


class Group(EventScoped):
    """A racing group, legacy `Classes`.

    Names are unique per event ignoring case. SQLite's `lower()` folds ASCII
    only, as PHP's `COLLATE NOCASE` did, while Postgres folds Unicode too. So
    `École` and `école` conflict on Postgres but not on SQLite.

    A group with constituents or constituent subgroups is an aggregate.
    Every other group is organic.
    """

    class WeighIn(models.TextChoices):
        NONE = "none"
        PASS_FAIL = "pass-fail"
        RECORDED = "recorded"

    class WeightUnit(models.TextChoices):
        GRAMS = "g"
        OUNCES = "oz"
        POUNDS = "lb"

    name = models.CharField(max_length=75)
    sort_order = models.IntegerField()
    durable = models.BooleanField(default=False)
    n_trophies = models.PositiveSmallIntegerField(null=True, blank=True)
    constituents = models.ManyToManyField(
        "self",
        through="GroupConstituent",
        through_fields=("aggregate", "constituent"),
        symmetrical=False,
        related_name="aggregates",
        blank=True,
    )
    constituent_subgroups = models.ManyToManyField(
        "Subgroup",
        through="GroupConstituentSubgroup",
        through_fields=("aggregate", "subgroup"),
        related_name="aggregates",
        blank=True,
    )
    weigh_in = models.CharField(
        max_length=10, choices=WeighIn.choices, default=WeighIn.NONE
    )
    weight_unit = models.CharField(
        max_length=2, choices=WeightUnit.choices, blank=True, default=""
    )
    max_weight_grams = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "event", Lower("name"), name="group_unique_name_per_event"
            ),
        ]

    def __str__(self):
        return self.name


class GroupConstituent(EventScoped):
    """Legacy `ConstituentClasses`: `constituent` is one member of `aggregate`."""

    aggregate = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="constituent_links"
    )
    constituent = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="aggregate_links"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "aggregate", "constituent"],
                name="groupconstituent_unique_pair",
            ),
        ]


class GroupConstituentSubgroup(EventScoped):
    """Legacy `Classes.rankids`: `subgroup` is one member of `aggregate`."""

    aggregate = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="constituent_subgroup_links"
    )
    subgroup = models.ForeignKey(
        "Subgroup", on_delete=models.CASCADE, related_name="aggregate_links"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "aggregate", "subgroup"],
                name="groupconstituentsubgroup_unique_pair",
            ),
        ]


class Subgroup(EventScoped):
    """Legacy `Ranks`."""

    group = models.ForeignKey(Group, on_delete=models.PROTECT, related_name="subgroups")
    name = models.CharField(max_length=75)
    sort_order = models.IntegerField()
    n_trophies = models.PositiveSmallIntegerField(null=True, blank=True)

    def __str__(self):
        return self.name


class Partition(EventScoped):
    """Legacy `Partitions`. Several partitions can share one subgroup."""

    name = models.CharField(max_length=200)
    subgroup = models.ForeignKey(
        Subgroup, on_delete=models.PROTECT, related_name="partitions"
    )
    sort_order = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "name"], name="partition_unique_name_per_event"
            ),
        ]

    def __str__(self):
        return self.name


class Racer(EventScoped):
    """Legacy `RegistrationInfo`.

    `headshot` and `car_photo` are paths relative to the event's photo
    repositories (`PHOTO_PATH_REGEX`).
    """

    car_number = models.IntegerField()
    car_name = models.CharField(max_length=100, blank=True, default="")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    group = models.ForeignKey(Group, on_delete=models.PROTECT, related_name="racers")
    subgroup = models.ForeignKey(
        Subgroup, on_delete=models.PROTECT, related_name="racers"
    )
    partition = models.ForeignKey(
        Partition, on_delete=models.PROTECT, related_name="racers"
    )
    passed_inspection = models.BooleanField(default=False)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    headshot = models.CharField(max_length=255, blank=True, default="")
    car_photo = models.CharField(max_length=255, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    exclude = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(headshot__regex=PHOTO_PATH_REGEX),
                name="racer_headshot_relative_path",
            ),
            models.CheckConstraint(
                condition=Q(car_photo__regex=PHOTO_PATH_REGEX),
                name="racer_car_photo_relative_path",
            ),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
