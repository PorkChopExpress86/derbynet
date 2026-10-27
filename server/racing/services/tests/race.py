"""A race set up as the PHP capture scenarios set one up, and readers of the
race state, the replay queue and a heat's results."""

from datetime import datetime
from urllib.parse import parse_qsl, quote

from django.utils import timezone

from core.models import RaceSettings, RaceState
from kiosks.models import ReplayCommand, ReplayStatus
from racing.models import HeatLane, Round
from racing.services.schedule import generate_schedule
from roster.tests.php_replay import Replay


def setup_race(racers=6, lanes=4, mask=0, partition="Wolves", first_car=101):
    """`setup_four_lane_race` of `capture/scenarios/03-timer.sh`: racers
    in one partition, checked in, and round 1 scheduled. Returns the
    `Replay`, whose `event` is the race, and the round."""
    replay = Replay()
    for car in range(first_car, first_car + racers):
        replay.post(
            f"action=racer.import&firstname=First{car}&lastname=Last{car}"
            f"&carnumber={car}&partition={quote(partition)}"
        )
    replay.post("action=racer.bulk&who=all&what=checkin")
    replay.post(f"action=settings.write&unused-lane-mask={mask}&n-lanes={lanes}")
    round = Round.objects.get(event=replay.event, group__name=partition, number=1)
    generate_schedule(round, 1)
    return replay, round


def settings(event) -> RaceSettings:
    return RaceSettings.objects.get(event=event)


def state(event) -> RaceState:
    return RaceState.objects.get(event=event)


def current(event):
    """`(roundid, heat, now_racing)`."""
    now = state(event)
    return (now.current_round_id, now.current_heat, now.racing)


def connect_replay(event, now: datetime | None = None):
    """A replay client that just polled, as `replay.message` records it."""
    ReplayStatus.objects.filter(event=event).update(
        state=ReplayStatus.State.CONNECTED, last_contact=now or timezone.now()
    )


def drain(event) -> list[str]:
    """The queued replay commands, oldest first, as `replay.message` hands
    them over."""
    rows = ReplayCommand.objects.filter(event=event).order_by("id")
    commands = list(rows.values_list("command", flat=True))
    rows.delete()
    return commands


def fields(body: str) -> list[tuple[str, str]]:
    return parse_qsl(body, keep_blank_values=True)


def lanes(round, heat):
    """`{lane: (time, place)}` of a heat."""
    rows = HeatLane.objects.filter(round=round, heat=heat).order_by("lane")
    return {
        lane: (time, place)
        for lane, time, place in rows.values_list("lane", "finish_time", "finish_place")
    }
