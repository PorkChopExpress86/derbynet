from django.db import models

from core.models import EventScoped, PerEvent


class Kiosk(EventScoped):
    """Legacy `Kiosks`. The legacy `page` packs `!` + path + `#` + JSON params."""

    address = models.CharField(max_length=50)
    name = models.CharField(max_length=200, blank=True, default="")
    page = models.CharField(max_length=200, blank=True, default="")
    params = models.JSONField(default=dict, blank=True)
    reload_requested = models.BooleanField(default=False)
    last_contact = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "address"], name="kiosk_unique_address"
            ),
        ]


class Scene(EventScoped):
    name = models.CharField(max_length=200)
    sort_order = models.IntegerField()

    def __str__(self):
        return self.name


class SceneKiosk(EventScoped):
    """The page that kiosks named `kiosk_name` show while `scene` is current."""

    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="kiosks")
    kiosk_name = models.CharField(max_length=200)
    page = models.CharField(max_length=200)
    params = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "scene", "kiosk_name"],
                name="scenekiosk_unique_kiosk_name",
            ),
        ]


class SignalMessage(EventScoped):
    """Legacy `MessageQueue`."""

    recipient = models.CharField(max_length=50)
    expires_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField()


class ReplayStatus(PerEvent):
    """Replay connection state that replaces the `replay_*` keys."""

    class State(models.IntegerChoices):
        NOT_CONNECTED = 1
        CONNECTED = 2
        TROUBLE = 3

    state = models.PositiveSmallIntegerField(
        choices=State.choices, default=State.NOT_CONNECTED
    )
    reported = models.CharField(max_length=50, blank=True, default="")
    last_contact = models.DateTimeField(null=True, blank=True)
    trouble = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        verbose_name_plural = "replay statuses"


class ReplayCommand(EventScoped):
    """One queued replay command, legacy `replay_queue` lines. Drained by id."""

    command = models.CharField(max_length=500)
