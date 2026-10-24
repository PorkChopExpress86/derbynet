from django.apps import apps
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Event(models.Model):
    """One race event. Every event-scoped model has a foreign key to it."""

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)
    copied_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="copies",
    )
    read_only = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class SiteState(models.Model):
    """The single site-level row. It points at the event the legacy API serves."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    active_event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name="sitestate_single_row"),
        ]

    def __str__(self):
        return f"active event {self.active_event_id}"

    @classmethod
    def get(cls):
        return cls.objects.select_related("active_event").get(pk=1)


class EventScoped(models.Model):
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True

    def __str__(self):
        return f"{type(self).__name__} {self.pk}"


class PerEvent(models.Model):
    """A row that exists exactly once per event, created by `new_event()`."""

    event = models.OneToOneField(Event, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True

    def __str__(self):
        return f"{type(self).__name__} of event {self.event_id}"

    @classmethod
    def for_event(cls, event):
        return cls.objects.get(event=event)


def is_event_scoped(model):
    """True when `model` has an `event` foreign key to `Event`."""
    field = next((f for f in model._meta.fields if f.name == "event"), None)
    return field is not None and field.related_model is Event


def event_scoped_models():
    """Every event-scoped model, in a stable order."""
    found = [model for model in apps.get_models() if is_event_scoped(model)]
    return sorted(found, key=lambda m: m._meta.label_lower)


class FullFieldRule(models.TextChoices):
    """Legacy `full-field-calc`: 0, -1, or the id of an aggregate group."""

    STANDARD = "standard"
    NONE = "none"
    GROUP = "group"


class RaceSettings(PerEvent):
    """Typed settings that replace the setting keys of `RaceInfo`.

    `core.legacy_keys` maps each legacy key name to a field here.
    """

    lane_count = models.PositiveSmallIntegerField(default=0)
    unused_lane_mask = models.PositiveIntegerField(default=0)
    reverse_lanes = models.BooleanField(default=False)

    use_master_sched = models.BooleanField(default=False)
    use_points = models.BooleanField(default=False)
    scoring = models.SmallIntegerField(default=0)
    max_runs_per_car = models.PositiveSmallIntegerField(default=0)
    rotation_schedule = models.BooleanField(default=False)

    track_length = models.PositiveIntegerField(default=40)
    finish_formatting = models.CharField(max_length=20, default="%5.3f")
    update_period_ms = models.PositiveIntegerField(default=2500)
    warn_no_timer = models.BooleanField(default=True)
    timer_send_logs = models.BooleanField(default=False)

    group_formation_rule = models.CharField(max_length=20, default="by-partition")
    use_subgroups = models.BooleanField(default=False)
    supergroup_label = models.CharField(max_length=100, default="Pack")
    partition_label = models.CharField(max_length=100, default="Den")

    full_field_rule = models.CharField(
        max_length=10, choices=FullFieldRule.choices, default=FullFieldRule.STANDARD
    )
    full_field_group = models.ForeignKey(
        "roster.Group",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    n_pack_trophies = models.PositiveSmallIntegerField(default=3)
    n_den_trophies = models.PositiveSmallIntegerField(default=3)
    n_rank_trophies = models.PositiveSmallIntegerField(default=0)
    one_trophy_per = models.BooleanField(default=False)

    use_xbs = models.BooleanField(default=False)
    xbs_award = models.CharField(max_length=100, default="Exclusively By Scout")
    car_numbering = models.CharField(max_length=20, default="100+101")
    name_style = models.PositiveSmallIntegerField(default=0)
    ballot_password = models.CharField(max_length=200, blank=True, default="")

    photos_on_now_racing = models.CharField(max_length=10, default="head")
    show_cars_on_deck = models.BooleanField(default=True)
    show_racer_photos_rr = models.BooleanField(default=True)
    show_car_photos_rr = models.BooleanField(default=True)
    now_racing_linger_ms = models.PositiveIntegerField(default=10000)
    lane_colors = models.CharField(max_length=200, blank=True, default="")
    images_dir = models.CharField(max_length=200, default="Generic")
    racing_scene = models.ForeignKey(
        "kiosks.Scene",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    upload_videos = models.BooleanField(default=True)

    replay_skipback_ms = models.PositiveIntegerField(default=4000)
    replay_num_showings = models.PositiveSmallIntegerField(default=2)
    replay_rate = models.PositiveSmallIntegerField(default=50)
    replay_post_roll_ms = models.PositiveIntegerField(default=2000)
    replay_max_clip_ms = models.PositiveIntegerField(default=10000)

    photo_directory = models.CharField(max_length=500, blank=True, default="")
    car_photo_directory = models.CharField(max_length=500, blank=True, default="")
    video_directory = models.CharField(max_length=500, blank=True, default="")
    logs_directory = models.CharField(max_length=500, blank=True, default="")

    websocket_url = models.CharField(max_length=200, blank=True, default="")
    ws_trigger_port = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        verbose_name_plural = "race settings"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        full_field_rule=FullFieldRule.GROUP,
                        full_field_group__isnull=False,
                    )
                    | (
                        ~Q(full_field_rule=FullFieldRule.GROUP)
                        & Q(full_field_group__isnull=True)
                    )
                ),
                name="racesettings_full_field_group_iff_rule_group",
            ),
        ]


class RaceState(PerEvent):
    """Live race state that replaces the per-heat keys of `RaceInfo`."""

    current_group = models.ForeignKey(
        "roster.Group",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    current_round = models.ForeignKey(
        "racing.Round",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    current_heat = models.IntegerField(null=True, blank=True)
    racing = models.BooleanField(default=False)
    timer_test = models.BooleanField(default=False)
    last_heat = models.JSONField(null=True, blank=True)
    refused_results = models.PositiveIntegerField(default=0)
    reschedule_started_at = models.DateTimeField(null=True, blank=True)
    current_scene = models.ForeignKey(
        "kiosks.Scene",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    playlist_scene = models.ForeignKey(
        "kiosks.Scene",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    playlist_scene_at = models.DateTimeField(null=True, blank=True)
    current_award_key = models.CharField(max_length=100, blank=True, default="")
    reveal_current_award = models.BooleanField(default=False)
    balloting_open = models.BooleanField(default=False)
    fake_racers = models.BooleanField(default=False)


class ActionLog(EventScoped):
    """One received legacy action, kept verbatim for the Retrospective page."""

    received = models.DateTimeField(default=timezone.now)
    request = models.JSONField()
