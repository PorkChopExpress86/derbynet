"""The kiosk polls: each first shows a finished playlist step's scene once
it is due, as `query.poll.kiosk.inc` calls `maybe_apply_playlist_scene()`.

Kept apart from `kiosks.services`, which `racing.services.playlist` imports.
"""

from django.utils import timezone

from kiosks import services
from racing.services.playlist import apply_due_scene


def poll_kiosk(event, address: str | None) -> dict | None:
    """`query=poll.kiosk`: the kiosk's setting, or None with no address."""
    now = timezone.now()
    apply_due_scene(event, now)
    if address is None:
        return None
    return services.kiosk_setting(event, address, now)


def poll_dashboard(event) -> dict:
    """`query=poll.kiosk.all`, polled by the kiosk dashboard every 2 s."""
    now = timezone.now()
    apply_due_scene(event, now)
    return services.dashboard(event, now)
