"""Replay commands, ported from `website/inc/replay.inc`.

`send` is the one place a command goes out, so a push channel (#56)
changes only it. Commands are rows written in the caller's transaction, so
a rollback drops them with the race-state change that sent them.
"""

import re
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import RaceSettings
from core.services import lock_race_state
from kiosks.models import ReplayCommand, ReplayStatus

REPLAY_TIMEOUT = timedelta(seconds=10)
"""PHP's `REPLAY_TIMEOUT_LIMIT`: a replay client that has not polled for
longer gets no commands."""


def connected(event_id: int, now: datetime) -> bool:
    status = ReplayStatus.objects.get(event_id=event_id)
    return (
        status.state == ReplayStatus.State.CONNECTED
        and status.last_contact is not None
        and status.last_contact + REPLAY_TIMEOUT >= now
    )


def send(event_id: int, command: str, now: datetime | None = None) -> bool:
    """Queue `command` if a replay client polled within the last 10 s.
    Returns whether it was queued."""
    if not connected(event_id, now or timezone.now()):
        return False
    ReplayCommand.objects.create(event_id=event_id, command=command)
    return True


def poll(event_id: int, reported: str, now: datetime) -> list[str]:
    """`replay.message`: the replay client polls, 4 times a second. It is
    connected from now on and reports its status; it gets the queued
    commands, each exactly once, with `HELLO` first when it was not
    connected. Under the `ReplayStatus` lock, so two polls drain the queue
    one after the other."""
    with transaction.atomic():
        status = ReplayStatus.objects.select_for_update().get(event_id=event_id)
        was_connected = connected(event_id, now)
        status.state = ReplayStatus.State.CONNECTED
        status.reported = reported
        status.last_contact = now
        status.save(update_fields=["state", "reported", "last_contact"])
        queued = list(
            ReplayCommand.objects.filter(event_id=event_id)
            .order_by("pk")
            .values_list("pk", "command")
        )
        ReplayCommand.objects.filter(pk__in=[pk for pk, _ in queued]).delete()
    commands = [command for _, command in queued]
    return commands if was_connected else ["HELLO", *commands]


def trigger(event_id: int) -> None:
    """`replay.trigger`: play the last few seconds back now."""
    with transaction.atomic():
        lock_race_state(event_id)
        send(event_id, replay(RaceSettings.objects.get(event_id=event_id)))


def send_test(event_id: int) -> None:
    """`replay.test`."""
    with transaction.atomic():
        lock_race_state(event_id)
        send(event_id, test(RaceSettings.objects.get(event_id=event_id)))


def start(group_name: str, round_number: int, heat: int) -> str:
    """`START <class>_Round<n>_Heat<NN>`. A name keeps its spaces and `&`."""
    return f"START {group_name}_Round{round_number}_Heat{heat:02d}"


CANCEL = "CANCEL"


def _timing(settings: RaceSettings) -> str:
    return (
        f"{settings.replay_skipback_ms} {settings.replay_num_showings}"
        f" {settings.replay_rate}"
    )


def replay(settings: RaceSettings) -> str:
    """`REPLAY <skipback> <showings> <rate>`."""
    return f"REPLAY {_timing(settings)}"


def race_starts(settings: RaceSettings) -> str:
    """`RACE_STARTS <skipback> <showings> <rate>`, which #32 sends when the
    gate opens."""
    return f"RACE_STARTS {_timing(settings)}"


def test(settings: RaceSettings) -> str:
    return f"TEST {_timing(settings)}"


_ICE_SEPARATORS = re.compile(r"[\s,]+")


def ice_servers(text: str) -> list[dict]:
    """The WebRTC `iceServers` of the `replay_ice_servers` setting: one
    server entry with every URL in it, or none for an empty setting, which
    leaves the replay page and the camera to host candidates on a LAN with
    no internet (#53)."""
    urls = ice_urls(text)
    return [{"urls": urls}] if urls else []


def ice_urls(text: str) -> list[str]:
    """The URLs of the `replay_ice_servers` text, in order."""
    return [url for url in _ICE_SEPARATORS.split(text) if url]


# A STUN URL Chromium's `RTCPeerConnection` takes: `stun:` or `stuns:` in
# any case, a host or a bracketed IPv6 address, and a port from 1 to 65535.
# `test_the_field_takes_what_chromium_takes` in
# `ui/tests/browser/test_settings_ice_servers.py` checks a list of URLs
# against Chromium's own parsing.
_STUN_URL = re.compile(
    r"stuns?:(?:\[[0-9a-f:.]+\]|[^\s,:/?#\[\]@]+)(?::(?P<port>\d{1,5}))?",
    re.IGNORECASE,
)
_TURN_URL = re.compile(r"turns?:", re.IGNORECASE)


class IceServersRefused(ValueError):
    pass


def ice_servers_text(lines: str) -> str:
    """The stored `replay_ice_servers` text of the settings page's field,
    one URL per line: the URLs separated by spaces, as the setting has
    always held them (D76, #241). Raises `IceServersRefused` for a URL the
    replay page's `RTCPeerConnection` would throw on. A `turn:` or `turns:`
    URL is refused: Chromium requires a username and credential for it,
    which `ice_servers()` does not send."""
    urls = ice_urls(lines)
    for url in urls:
        if _TURN_URL.match(url):
            raise IceServersRefused(
                "TURN servers need a username and password, which this field"
                " cannot hold yet; list STUN servers only."
            )
        found = _STUN_URL.fullmatch(url)
        port = found and found["port"]
        if not found or (port is not None and not 1 <= int(port) <= 65535):
            raise IceServersRefused(
                f"{url} is not a STUN server URL, such as stun:stun.l.google.com:19302."
            )
    text = " ".join(urls)
    limit = RaceSettings._meta.get_field("replay_ice_servers").max_length
    if len(text) > limit:
        raise IceServersRefused(f"at most {limit} characters in all.")
    return text


def page_settings(settings: RaceSettings) -> dict:
    """What the replay page and the remote camera are given when they load
    (#53). Skipback, showings and rate also come with each command, which
    the page reads instead; the post-roll and the maximum clip length come
    only here (D18). The websocket URL is empty, so both pages poll in v1;
    #56 decides the push channel."""
    return {
        "skipback_ms": settings.replay_skipback_ms,
        "showings": settings.replay_num_showings,
        "rate": settings.replay_rate,
        "post_roll_ms": settings.replay_post_roll_ms,
        "max_clip_ms": settings.replay_max_clip_ms,
        "upload_videos": settings.upload_videos,
        "ice_servers": ice_servers(settings.replay_ice_servers),
        "websocket_url": "",
    }


def clip_length_ms(settings: RaceSettings) -> int:
    """D18: a clip runs `skipback` before the finish and the post-roll after
    it, at most the maximum clip length. The replay page computes the same
    from each command's skipback (ui/static/ui/replay.js)."""
    return min(
        settings.replay_skipback_ms + settings.replay_post_roll_ms,
        settings.replay_max_clip_ms,
    )
