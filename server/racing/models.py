from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import EventScoped, PerEvent

HAS_RESULT = Q(finish_time__isnull=False) | Q(finish_place__isnull=False)
"""A `HeatLane` that has a result (#20, Q11): a time or a place, whatever
`completed` says."""


class Round(EventScoped):
    """Legacy `Rounds`."""

    group = models.ForeignKey(
        "roster.Group", on_delete=models.PROTECT, related_name="rounds"
    )
    number = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "group", "number"], name="round_unique_number"
            ),
            models.CheckConstraint(
                condition=Q(number__gte=1), name="round_number_gte_1"
            ),
        ]


class RosterEntry(EventScoped):
    """Legacy `Roster`: one racer entered in one round."""

    round = models.ForeignKey(Round, on_delete=models.PROTECT, related_name="roster")
    racer = models.ForeignKey(
        "roster.Racer", on_delete=models.PROTECT, related_name="roster_entries"
    )

    class Meta:
        verbose_name_plural = "roster entries"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "round", "racer"], name="rosterentry_unique_racer"
            ),
        ]


class HeatLane(EventScoped):
    """Legacy `RaceChart`: one racer in one lane of one heat, and its result."""

    round = models.ForeignKey(
        Round, on_delete=models.PROTECT, related_name="heat_lanes"
    )
    heat = models.PositiveSmallIntegerField()
    lane = models.PositiveSmallIntegerField()
    racer = models.ForeignKey(
        "roster.Racer", on_delete=models.PROTECT, related_name="heat_lanes"
    )
    finish_time = models.FloatField(null=True, blank=True)
    finish_place = models.PositiveSmallIntegerField(null=True, blank=True)
    completed = models.DateTimeField(null=True, blank=True)
    master_heat = models.PositiveIntegerField(default=0)  # 0 means unused

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "round", "heat", "lane"],
                name="heatlane_one_car_per_lane",
            ),
        ]


class EmptiedHeat(EventScoped):
    """A running or staged heat whose cars were all taken off the roster
    (D39). It has no `HeatLane` rows left, so this row keeps its heat number
    and master number while the race crew runs or skips it. Django only. It
    is passed, and ignored, once a later heat of its own round gets a result
    after `held_at`, as `completed` records it
    (`racing.services.master.held_emptied_heats`). Results written before
    the heat emptied, and master renumbering, never pass it."""

    round = models.ForeignKey(
        Round, on_delete=models.CASCADE, related_name="emptied_heats"
    )
    heat = models.PositiveSmallIntegerField()
    master_heat = models.PositiveIntegerField(default=0)  # 0 means unused
    held_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "round", "heat"], name="emptiedheat_unique_heat"
            ),
        ]


class PlaylistEntry(EventScoped):
    """Legacy `Playlist`: one step of the race-day queue, ordered by `seq`."""

    seq = models.IntegerField()
    group = models.ForeignKey(
        "roster.Group", on_delete=models.PROTECT, related_name="playlist_entries"
    )
    round_number = models.PositiveSmallIntegerField()
    bucket_limit = models.PositiveIntegerField(default=0)
    bucketed = models.BooleanField(default=False)
    n_times_per_lane = models.PositiveSmallIntegerField(default=1)
    scene_at_finish = models.ForeignKey(
        "kiosks.Scene",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    continue_racing = models.BooleanField(default=False)
    waits_for_operator = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "playlist entries"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "seq"], name="playlistentry_unique_seq"
            ),
        ]


class TimerSetting(EventScoped):
    """Legacy `TimerSettings`. `name` is the legacy `key`, a MySQL reserved word."""

    class Kind(models.TextChoices):
        FLAG = "flag"
        DEVICE = "device"
        PORTS = "ports"
        PENDING_DEVICE = "pending-device"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    name = models.CharField(max_length=100)
    value_type = models.CharField(max_length=10, blank=True, default="")
    value = models.CharField(max_length=100, blank=True, default="")
    description = models.CharField(max_length=500, blank=True, default="")
    # Null means no change is pending. An empty string is a requested value.
    pending = models.CharField(max_length=100, null=True, blank=True)  # noqa: DJ001


class TimerStatus(PerEvent):
    """Timer connection state that replaces the `timer*` and `tt-*` keys."""

    class State(models.IntegerChoices):
        NOT_CONNECTED = 1
        CONNECTED = 2
        STAGING = 3
        RUNNING = 4
        UNHEALTHY = 5
        SEARCHING = 6
        UNCONFIRMED = 7

    state = models.PositiveSmallIntegerField(
        choices=State.choices, default=State.NOT_CONNECTED
    )
    last_contact = models.DateTimeField(null=True, blank=True)
    # Legacy last_contact < 0: a test that skips the contact timeout.
    assume_connected_for_test = models.BooleanField(default=False)
    # A malfunction either expires or waits for an explicit reset. Legacy
    # malfunction_expiration=0 is the reset case (`timer-state.inc`).
    malfunction_expires_at = models.DateTimeField(null=True, blank=True)
    malfunction_requires_reset = models.BooleanField(default=False)
    malfunction_message = models.CharField(max_length=500, blank=True, default="")

    staged_round = models.ForeignKey(
        Round, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    staged_heat = models.PositiveSmallIntegerField(null=True, blank=True)

    agent = models.CharField(max_length=200, blank=True, default="")
    interface = models.CharField(max_length=200, blank=True, default="")
    timer_type = models.CharField(max_length=200, blank=True, default="")
    human = models.CharField(max_length=200, blank=True, default="")
    ident = models.CharField(max_length=200, blank=True, default="")
    options = models.CharField(max_length=500, blank=True, default="")
    vid = models.CharField(max_length=20, blank=True, default="")
    pid = models.CharField(max_length=20, blank=True, default="")
    reported_lane_count = models.PositiveSmallIntegerField(null=True, blank=True)
    log_path = models.CharField(max_length=500, blank=True, default="")

    remote_start_seen_at = models.DateTimeField(null=True, blank=True)
    remote_start_requested = models.BooleanField(default=False)

    test_saved_round = models.ForeignKey(
        Round, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    test_saved_heat = models.IntegerField(null=True, blank=True)
    test_lane_mask = models.PositiveIntegerField(default=0)
    test_results_heat = models.IntegerField(null=True, blank=True)
    # {"<lane>": {"time": float, "place": int}}, from legacy tt-lane<N>, tt-place<N>.
    test_results = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name_plural = "timer statuses"
        constraints = [
            models.CheckConstraint(
                condition=Q(malfunction_requires_reset=False)
                | Q(malfunction_expires_at__isnull=True),
                name="timerstatus_malfunction_expires_or_waits_for_reset",
            ),
        ]


class AuditEvent(EventScoped):
    """The timer and heat audit, legacy `Events`, written for new events (D19).

    Every reference uses `SET_NULL`, so a purge keeps the history.
    """

    class Action(models.IntegerChoices):
        CHECKIN = 1
        RACER_PHOTO_ASSIGN = 2
        CAR_PHOTO_ASSIGN = 3
        TIMER_MALFUNCTION = 20
        TIMER_RESULT_REFUSED = 25
        TIMER_OVERDUE = 30
        PURGE_RESULTS = 90
        PURGE_SCHEDULES = 91
        PURGE_RACERS = 92
        PURGE_AWARDS = 93
        PURGE_RESULTS_ONE_ROUND = 94
        HEAT_RESULT_DISCARDED = 100
        HEAT_RESULT_FROM_TIMER = 101
        HEAT_MANUALLY_ENTERED = 102
        HEAT_RESULT_REINSTATED = 103
        CLASS_ADDED = 200
        RANK_ADDED = 201
        ROUND_ADDED = 202
        SCHEDULE_ADDED = 203
        PARTITION_ADDED = 204
        PLAYLIST_ADDED = 209
        PLAYLIST_REMOVED = 210
        CLASS_DELETED = 301
        RANK_DELETED = 302
        # PHP defines EVENT_SCHEDULE_DELETED as 303 too.
        ROUND_DELETED = 303
        PARTITION_DELETED = 304
        GROUP_FORMATION_RULE_APPLIED = 401
        PARTITION_MOVED = 402
        PARTITION_RENAMED = 403
        TIMER_MESSAGE = 500
        TIMER_STATE_CHANGED = 501

    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.PositiveSmallIntegerField(choices=Action.choices)
    racer = models.ForeignKey(
        "roster.Racer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    group = models.ForeignKey(
        "roster.Group",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    subgroup = models.ForeignKey(
        "roster.Subgroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    round = models.ForeignKey(
        Round, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    heat = models.PositiveSmallIntegerField(null=True, blank=True)
    result_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    detail = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["event", "round", "heat", "id"], name="auditevent_heat_lookup"
            ),
        ]


class AuditLaneResult(EventScoped):
    """Legacy `EventLaneDetails`: one lane's raw result for an `AuditEvent`."""

    audit_event = models.ForeignKey(
        AuditEvent, on_delete=models.CASCADE, related_name="lane_results"
    )
    lane = models.PositiveSmallIntegerField()
    finish_time = models.FloatField(null=True, blank=True)
    finish_place = models.PositiveSmallIntegerField(null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)
