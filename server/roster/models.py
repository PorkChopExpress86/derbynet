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
    """Legacy `Partitions`. Several partitions can share one subgroup.

    The services keep names unique per event with case ignored (D31), and
    the constraint below checks the exact name. They compare `name_key`, not
    the database's `lower()`, which folds only ASCII on SQLite, so both
    databases refuse the same names.

    The weigh-in fields are Django only (D8, D54): how check-in weighs the
    partition's cars, the unit weights are entered and shown in, and an
    optional maximum, stored in grams. `roster.services.weigh_in` sets and
    reads them.
    """

    class WeighInMode(models.TextChoices):
        NONE = "none", "No weigh-in"
        PASS_FAIL = "pass_fail", "Pass or fail"
        RECORDED = "recorded", "Recorded weight"

    class WeightUnit(models.TextChoices):
        GRAMS = "g", "grams"
        OUNCES = "oz", "ounces"
        POUNDS = "lb", "pounds"

    name = models.CharField(max_length=200)
    subgroup = models.ForeignKey(
        Subgroup, on_delete=models.PROTECT, related_name="partitions"
    )
    sort_order = models.IntegerField()
    weigh_in_mode = models.CharField(
        max_length=10, choices=WeighInMode.choices, default=WeighInMode.NONE
    )
    weight_unit = models.CharField(
        max_length=2, choices=WeightUnit.choices, blank=True, default=""
    )
    max_weight_grams = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "name"], name="partition_unique_name_per_event"
            ),
            # A recorded weight, and a maximum, need a unit to show in.
            models.CheckConstraint(
                condition=~Q(weight_unit="")
                | (
                    ~Q(weigh_in_mode="recorded")
                    & (~Q(weigh_in_mode="pass_fail") | Q(max_weight_grams=None))
                ),
                name="partition_weight_unit_when_needed",
            ),
            models.CheckConstraint(
                condition=Q(max_weight_grams=None) | Q(max_weight_grams__gt=0),
                name="partition_max_weight_positive",
            ),
        ]

    def __str__(self):
        return self.name

    @staticmethod
    def name_key(name: str) -> str:
        return name.casefold()


class CarRange(EventScoped):
    """A span of car numbers the race coordinator gives a partition (D48).

    A partition holds an ordered list of them. One with none uses the
    `car-numbering` setting. Django only: PHP has no such table.
    """

    partition = models.ForeignKey(
        Partition, on_delete=models.CASCADE, related_name="car_ranges"
    )
    first = models.IntegerField()
    last = models.IntegerField()
    sort_order = models.IntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(last__gte=models.F("first")),
                name="car_range_last_not_below_first",
            ),
        ]

    def __str__(self):
        return f"{self.first} to {self.last}"


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
    # The two award settings (D33). `exclude` is the legacy name of "not
    # eligible for any award", speed and judged awards alike.
    exclude = models.BooleanField(default=False)
    not_eligible_for_speed_awards = models.BooleanField(default=False)
    # The weigh-in (D8, D54), Django only: null until the car is weighed.
    # `weight_grams` is set only in a partition that records weights.
    weigh_in_passed = models.BooleanField(null=True, blank=True)
    weight_grams = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    weighed_at = models.DateTimeField(null=True, blank=True)

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
            models.CheckConstraint(
                condition=Q(weight_grams=None) | Q(weight_grams__gt=0),
                name="racer_weight_positive",
            ),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
