"""What the timer pages show (#50), read from the timer services of #32.

The status card is `timer-test.php`'s summary and details, which
`json_timer_state()` and `json_timer_details()` fill, with the coordinator
page's refused-results count. The test card is `poll.timer.test`: the
current test heat's lane mask and its times and places. Reading writes
nothing.
"""

from dataclasses import dataclass, field
from datetime import datetime

from core.models import RaceSettings, RaceState
from racing.models import TimerStatus
from racing.services import polls, timer
from racing.services.lanes import lanes_of
from ui.dashboard.presenter import pill_state, strip_seconds

# The status card's poll, and the test card's, which `timer-test.js:326`
# polls every 500 ms.
STATUS_POLL_SECONDS = 2
TEST_POLL_MS = 500


@dataclass(frozen=True)
class Status:
    # `expand_timer_state_status()`'s text without its "(N sec.)", which the
    # page counts from `contact` itself.
    text: str
    pill: str
    state: int
    connected: bool
    lanes: int
    # The lane count the timer reported, when it differs from the race's.
    reported_lanes: int | None
    malfunction: str
    details: dict
    # The User-Agent and interface of the timer client heard from last. A
    # second client that competes with it is told so, and this names the
    # one the server kept (`<competing>`).
    agent: str
    interface: str
    remote_start: bool
    refused_results: int
    test_running: bool
    # The epoch second of the last contact, or None; never signed, since a
    # heartbeat changes it every second.
    contact: int | None = field(compare=False)


def status(event, now: datetime) -> Status:
    row = TimerStatus.objects.get(event=event)
    race = RaceState.objects.get(event=event)
    found = polls.timer_state(event, now)
    state = found["state"]
    lanes = found["lanes"] or 0
    text = strip_seconds(found["message"])
    # A malfunction in force, unless the status text already is its message.
    malfunction = timer.TimerState.read(row, now).malfunction_message
    return Status(
        text=text,
        pill=pill_state(found["icon"]),
        state=state,
        connected=state != TimerStatus.State.NOT_CONNECTED,
        lanes=lanes,
        reported_lanes=timer.reported_lane_count(row, lanes, now),
        malfunction="" if malfunction == text else malfunction,
        details=timer.details(event),
        agent=row.agent,
        interface=row.interface,
        remote_start=found["remote-start"],
        refused_results=race.refused_results,
        test_running=race.timer_test and race.racing,
        contact=None if row.last_contact is None else int(row.last_contact.timestamp()),
    )


@dataclass(frozen=True)
class TestLane:
    lane: int
    occupied: bool
    time: float | None
    place: int | None


@dataclass(frozen=True)
class Test:
    running: bool
    lane_count: int
    reverse_lanes: bool
    lanes: list[TestLane]
    heat: int


def test(event) -> Test:
    """`poll.timer.test`'s `tt` and current heat, by lane. A track with no
    lane count shows one lane, as `timer-test.js:12` and `:197` draw one
    (test_a_track_with_no_lane_count_shows_one_lane)."""
    settings = RaceSettings.objects.get(event=event)
    race = RaceState.objects.get(event=event)
    found = polls.timer_test(event)
    unused = set(lanes_of(found["mask"]))
    results = {r["lane"]: r for r in found["results"]}
    count = settings.lane_count or 0
    return Test(
        running=race.timer_test and race.racing,
        lane_count=count,
        reverse_lanes=settings.reverse_lanes,
        lanes=[
            TestLane(
                lane=n,
                occupied=n not in unused,
                time=results.get(n, {}).get("time"),
                place=results.get(n, {}).get("place"),
            )
            for n in range(1, max(count, 1) + 1)
        ],
        heat=found["heat"],
    )


def settings_sheet(event) -> dict:
    """`query=timer.settings`: the ports, the devices and the flags, each
    flag with the control its type takes, as `make_flag_control()` picks."""
    report = timer.settings_report(event)
    ports = [p for p in report.get("ports", "").split(",") if p] if report else []
    flags = []
    for f in report["flags"]:
        kind = (
            "bool"
            if f["type"] == "bool"
            else "text"
            if f["type"] in ("string", "int", "long")
            else "value"
        )
        flags.append({**f, "kind": kind, "on": f["value"] != "false"})
    return {"ports": ports, "devices": report["devices"], "flags": flags}
