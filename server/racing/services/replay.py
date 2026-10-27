"""Replay commands, ported from `website/inc/replay.inc`.

`send` is the one place a command goes out, so a push channel (#56)
changes only it. Commands are rows written in the caller's transaction, so
a rollback drops them with the race-state change that sent them.
"""

from datetime import datetime, timedelta

from django.utils import timezone

from core.models import RaceSettings
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
