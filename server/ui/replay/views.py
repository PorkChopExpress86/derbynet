"""The replay page and the remote replay camera (#53).

- `/replay/` is `replay.php`: the camera setup, then a kiosk page in a
  full-window frame that the page replays each heat over. Its script,
  `ui/replay.js`, polls `replay.message` and plays the clips; the clip
  window is D18's (`racing.services.replay.clip_length_ms`).
- `/replay/camera/` is `camera.php`: a phone's camera as the remote camera
  the replay page can choose.

Both need CONTROL_RACE, the replay login (D5, D9, D16), as their registry
entries say; PHP's pages are anonymous. `replay.php` and `camera.php` answer
with a 301 to them, query string kept (D20, `ui.redirects`).
"""

from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_GET

from core.models import RaceSettings
from racing.services import replay
from ui.responses import active_event


def _config(request) -> tuple[RaceSettings, dict]:
    settings = RaceSettings.objects.get(event=active_event(request))
    config = {
        **replay.page_settings(settings),
        "action": reverse("action-php"),
        "debug": "debug" in request.GET,
    }
    return settings, config


def kiosk_url(request) -> str:
    """The frame's default URL: the kiosk page on this server, with no
    scheme, so an https page frames an https kiosk (replay.php:14-28). An
    `address` query value is passed on."""
    url = f"//{request.get_host()}{reverse('kiosk-php')}"
    if address := request.GET.get("address"):
        url += "?" + urlencode({"address": address})
    return url


def _seconds(ms: int) -> str:
    return f"{ms / 1000:g} s"


def _settings_rows(settings: RaceSettings) -> list[tuple[str, str, str]]:
    """The replay settings the setup screen shows, by their settings key."""
    return [
        ("replay-rate", "Playback speed", f"{settings.replay_rate}%"),
        ("replay-num-showings", "Showings", str(settings.replay_num_showings)),
        ("replay-skipback", "Before the finish", _seconds(settings.replay_skipback_ms)),
        (
            "replay-post-roll",
            "After the last finisher",
            _seconds(settings.replay_post_roll_ms),
        ),
        ("replay-max-clip", "Longest clip", _seconds(settings.replay_max_clip_ms)),
        ("clip", "Each clip runs", _seconds(replay.clip_length_ms(settings))),
    ]


@require_GET
def page(request):
    settings, config = _config(request)
    return render(
        request,
        "ui/replay/page.html",
        {
            "config": config,
            "kiosk_url": kiosk_url(request),
            "settings_rows": _settings_rows(settings),
            "settings_url": reverse("settings") + "#replay",
            "page_title": "Replay",
        },
    )


@require_GET
def camera(request):
    _, config = _config(request)
    return render(
        request,
        "ui/replay/camera.html",
        {"config": config, "page_title": "Replay Camera"},
    )
