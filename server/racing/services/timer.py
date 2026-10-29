"""The timer: `timer-message`, its state, flags, remote start, test mode and
log (#32).

Ported from `website/ajax/action.timer-message.inc`,
`website/inc/timer-state.inc`, `timer-flags.inc`, `remote-start.inc`,
`timer-logging.inc` and `timer-test.inc`. `handle_message` runs one message
in one transaction that locks `RaceState` first, and writes the audit after
it (D19, D26). Its reply is the XML PHP echoes, built as text so the order,
whitespace and quoting match byte for byte (ADR-02).
"""

import fcntl
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone

from core.models import ActionLog, Event, RaceSettings, RaceState
from core.services import lock_race_state
from legacy_api.xml import php_escape
from racing.engine import heat_state as hs
from racing.engine.phpcompat import php_equals, php_numeric, php_truthy
from racing.models import HeatLane, Round, TimerSetting, TimerStatus
from racing.services import audit, replay
from racing.services.heats import transition
from racing.services.results import Outcome, timer_result_locked

State = TimerStatus.State

CONTACT_TIMEOUT = timedelta(seconds=60)
"""`TimerState`: a timer that has not reported for longer is not connected."""
MALFUNCTION_DURATION = timedelta(seconds=60)
"""How long a malfunction the timer cannot detect stays in force."""
REMOTE_START_TIMEOUT = timedelta(seconds=60)
"""`has_remote_start()`: a timer offered remote start within the last minute."""
COMPETING_WINDOW = 1
"""Seconds, as PHP's `time()` counts them, in which a second timer's HELLO
competes with the first."""

SUCCESS = "\n  <success/>\n"


# ----- the timer's state -----


@dataclass
class TimerState:
    """`new TimerState()`: the stored state with the contact timeout and an
    expired malfunction applied. Reading writes nothing; `timed_out` says
    the timeout changed the state, for the caller to store and log."""

    state: int
    last_contact: datetime | None
    assume_connected: bool
    malfunction_expires_at: datetime | None
    malfunction_requires_reset: bool
    malfunction_message: str
    timed_out: bool = False

    @classmethod
    def read(cls, row: TimerStatus, now: datetime) -> TimerState:
        found = cls(
            state=row.state,
            last_contact=row.last_contact,
            assume_connected=row.assume_connected_for_test,
            malfunction_expires_at=row.malfunction_expires_at,
            malfunction_requires_reset=row.malfunction_requires_reset,
            malfunction_message=row.malfunction_message,
        )
        stale = found.last_contact is None or found.last_contact + CONTACT_TIMEOUT < now
        if not found.assume_connected and stale:
            found.timed_out = found.state != State.NOT_CONNECTED
            found.state = State.NOT_CONNECTED
        expires = found.malfunction_expires_at
        if expires is not None and expires <= now:
            found.malfunction_expires_at = None
            found.malfunction_message = ""
        return found

    @property
    def unhealthy(self) -> bool:
        """It may get better on its own, with a good heartbeat."""
        return self.state in (State.NOT_CONNECTED, State.SEARCHING, State.UNHEALTHY)

    @property
    def troubled(self) -> bool:
        """Unhealthy, or a malfunction in force."""
        return (
            self.unhealthy
            or self.malfunction_requires_reset
            or self.malfunction_expires_at is not None
        )

    def set_malfunction(self, now: datetime, message: str) -> None:
        self.malfunction_expires_at = now + MALFUNCTION_DURATION
        self.malfunction_requires_reset = False
        self.malfunction_message = message

    def set_unhealthy(self, message: str) -> None:
        self.state = State.UNHEALTHY
        self.malfunction_message = message

    def store(self, row: TimerStatus, now: datetime, renew: bool) -> None:
        """`TimerState::write`. `renew` records a contact now."""
        if renew and not self.assume_connected:
            self.last_contact = now
        row.state = self.state
        row.last_contact = self.last_contact
        row.malfunction_expires_at = self.malfunction_expires_at
        row.malfunction_requires_reset = self.malfunction_requires_reset
        row.malfunction_message = self.malfunction_message


_STATE_FIELDS = [
    "state",
    "last_contact",
    "malfunction_expires_at",
    "malfunction_requires_reset",
    "malfunction_message",
]


def check_contact(event: Event, now: datetime | None = None) -> bool:
    """The contact timeout, written: a timer silent for more than a minute
    goes to `NOT_CONNECTED`, logged as `timer.*overdue`. PHP writes it on
    every read of the state; Django writes it here and on each timer
    message, and reads never write. The audit dates the change to when
    the minute ran out. Returns whether it timed out."""
    now = now or timezone.now()
    with transaction.atomic():
        row = TimerStatus.objects.select_for_update().get(event=event)
        timer = TimerState.read(row, now)
        if not timer.timed_out:
            return False
        record = _time_out(event, row, timer, now)
    audit.write(event.id, [record])
    return True


def _time_out(event: Event, row: TimerStatus, timer: TimerState, now) -> audit.Record:
    """Store the timeout and log `timer.*overdue`. Returns its audit record."""
    was = row.state
    timer.store(row, now, renew=False)
    row.save(update_fields=_STATE_FIELDS)
    ActionLog.objects.create(event=event, request={"action": "timer.*overdue"})
    at = None if timer.last_contact is None else timer.last_contact + CONTACT_TIMEOUT
    return audit.timer_state_changed(None, was, timer.state, at)


# ----- the current heat, as get_running_round() gives it -----


@dataclass(frozen=True)
class _Current:
    roundid: int  # -1 for none, the timer test's -100
    heat: int | None
    round: int | None
    class_name: str


def _current(race: RaceState) -> _Current:
    if race.timer_test:
        return _Current(hs.TIMER_TEST_ROUND, race.current_heat, 1, "Timer Test")
    if race.current_round_id is None:
        return _Current(-1, race.current_heat, None, "")
    found = (
        Round.objects.filter(pk=race.current_round_id, group_id=race.current_group_id)
        .values_list("number", "group__name")
        .first()
    )
    number, name = found or (None, "")
    return _Current(race.current_round_id, race.current_heat, number, name)


def _staged(row: TimerStatus) -> tuple[int, int] | None:
    if row.staged_heat is None:
        return None
    round_id = row.staged_round_id
    return (hs.TIMER_TEST_ROUND if round_id is None else round_id, row.staged_heat)


def _stage(row: TimerStatus, current: _Current | None) -> None:
    if current is None or current.roundid == -1:
        row.staged_round_id, row.staged_heat = None, None
        return
    round_id = None if current.roundid == hs.TIMER_TEST_ROUND else current.roundid
    row.staged_round_id, row.staged_heat = round_id, current.heat


def _text(value) -> str:
    """A form field PHP would echo; an array field counts as missing."""
    return value if isinstance(value, str) else ""


def _blank(value) -> str:
    return "" if value is None else str(value)


# ----- timer-message -----


@dataclass
class _Message:
    event: Event
    race: RaceState
    row: TimerStatus
    settings: RaceSettings
    timer: TimerState
    fields: Mapping
    user_agent: str
    now: datetime
    racing: bool
    finalists_for: object = None
    out: list[str] = field(default_factory=list)
    records: list[audit.Record] = field(default_factory=list)

    def has(self, key: str) -> bool:
        """PHP's `isset($_POST[key])`. A bare key with no `=` is set."""
        return key in self.fields

    def get(self, key: str) -> str:
        return _text(self.fields.get(key))


def handle_message(
    event: Event,
    fields: Mapping,
    user_agent: str = "",
    *,
    now: datetime | None = None,
    finalists_for=None,
) -> str:
    """One `timer-message` from a timer that holds `TIMER_MESSAGE`. Returns
    the reply's children, between `<action-response ...>` and its end."""
    now = now or timezone.now()
    with transaction.atomic():
        race = lock_race_state(event.id)
        row = TimerStatus.objects.select_for_update().get(event=event)
        timer = TimerState.read(row, now)
        timed_out = _time_out(event, row, timer, now) if timer.timed_out else None
        was = row.state
        message = _text(fields.get("message"))
        msg = _Message(
            event,
            race,
            row,
            RaceSettings.objects.get(event=event),
            timer,
            fields,
            user_agent,
            now,
            race.racing,
            finalists_for,
        )
        ref = _ref(race)
        if timed_out is not None:
            msg.records.append(timed_out)
        msg.records.append(audit.timer_message(ref, _detail(message, fields)))
        if msg.has("overdue"):
            msg.records.append(
                audit.Record(
                    audit.Action.TIMER_OVERDUE,
                    *(ref or (None, None)),
                    detail={"other": msg.get("overdue")},
                )
            )
        row.remote_start_seen_at = now if msg.get("remote-start") == "YES" else None
        branch = _BRANCHES.get(message)
        if branch is None:
            msg.out.append(
                "\n  <failure code='notunderstood'>Message "
                f"{php_escape(message)} not recognized.</failure>\n"
            )
        else:
            branch(msg)
        _after(msg)
        timer.store(row, now, renew=True)
        row.save(update_fields=[*_STATE_FIELDS, *_IDENTITY_FIELDS])
        if timer.state != was:
            msg.records.append(audit.timer_state_changed(_ref(race), was, timer.state))
    audit.write(event.id, msg.records)
    return "".join(msg.out)


_IDENTITY_FIELDS = [
    "agent",
    "interface",
    "timer_type",
    "human",
    "ident",
    "options",
    "vid",
    "pid",
    "reported_lane_count",
    "log_path",
    "remote_start_seen_at",
    "staged_round",
    "staged_heat",
]


def _ref(race: RaceState) -> tuple[int, int] | None:
    if race.timer_test or race.current_round_id is None:
        return None
    return (race.current_round_id, race.current_heat)


def _detail(message: str, fields: Mapping) -> dict:
    """What the audit keeps of a message: its name and its other fields."""
    detail = {"message": message}
    detail.update(
        (key, value)
        for key, value in fields.items()
        if key not in ("action", "message") and isinstance(value, str)
    )
    return detail


def _hello(msg: _Message) -> None:
    # Searching until the timer is identified.
    msg.timer.state = State.SEARCHING
    msg.row.log_path = new_log_path(msg.settings, msg.now)
    if msg.has("interface"):
        interface = msg.get("interface")
        if msg.has("build"):
            interface += "-" + msg.get("build")
        msg.row.interface = interface
    last = msg.timer.last_contact
    accept = True
    if (
        not msg.timer.assume_connected
        and last is not None
        and int(last.timestamp()) + COMPETING_WINDOW >= int(msg.now.timestamp())
    ):
        agent = msg.row.agent
        if agent and agent != msg.user_agent:
            accept = False
            msg.out.append(f"<competing>{php_escape(agent)}</competing>\n")
    # The timer that made contact most recently; the others compete with it.
    if accept:
        msg.row.agent = msg.user_agent
    msg.out.append(SUCCESS + "    <query/>\n")


def _identified(msg: _Message) -> None:
    lanes = php_numeric(msg.get("lane_count")) if msg.has("lane_count") else None
    if lanes is not None and lanes > 0:
        # C26: kept for parity. The timer's lane count wins, with no check
        # against the schedules (#50 asks the owner).
        msg.row.reported_lane_count = int(lanes)
        msg.settings.lane_count = int(lanes)
        msg.settings.save(update_fields=["lane_count"])
    for key, column in (
        ("timer", "timer_type"),
        ("human", "human"),
        ("ident", "ident"),
        ("options", "options"),
    ):
        if msg.has(key):
            setattr(msg.row, column, msg.get(key))
    for key in ("vid", "pid"):
        if php_truthy(msg.get(key)):
            setattr(msg.row, key, msg.get(key))
    if msg.has("confirmed") and php_equals(0, msg.get("confirmed")):
        msg.timer.state = State.UNCONFIRMED
    else:
        msg.timer.state = State.CONNECTED
    msg.out.append(SUCCESS)


def _started(msg: _Message) -> None:
    msg.timer.state = State.RUNNING
    replay.send(msg.event.id, replay.race_starts(msg.settings), msg.now)
    msg.out.append(SUCCESS)


def _finished(msg: _Message) -> None:
    fields = [(k, v) for k, v in msg.fields.items() if isinstance(v, str)]
    recorded, records = timer_result_locked(
        msg.race, fields, now=msg.now, finalists_for=msg.finalists_for
    )
    msg.records.extend(records)
    match recorded.outcome:
        case Outcome.NOT_RACING:
            msg.out.append("\n  <failure code='notracing'>Not racing now.</failure>\n")
        case Outcome.WRONG_HEAT:
            msg.out.append(
                f"<failure code='wrong-heat'>{recorded.failure.description}</failure>"
                + SUCCESS
            )
        case Outcome.NO_CURRENT_ROUND:
            msg.out.append(
                '<failure code="nocurrentround">'
                f"{recorded.failure.description}</failure>" + SUCCESS
            )
        case Outcome.RECORDED:
            # PHP reads the racing state again only after a recorded result,
            # so an all-zero result still gets this message's heat-ready.
            msg.racing = msg.race.racing
            msg.out.append(SUCCESS)
        case _:
            msg.out.append(SUCCESS)
    # Moves on to STAGING below if another heat is ready.
    msg.timer.state = State.CONNECTED


def _heartbeat(msg: _Message) -> None:
    msg.out.append(SUCCESS)
    timer = msg.timer
    if msg.has("unhealthy"):
        if not timer.unhealthy:
            timer.state = State.SEARCHING
    elif msg.has("confirmed"):
        if php_equals(0, msg.get("confirmed")):
            timer.state = State.UNCONFIRMED
        elif timer.state in (State.NOT_CONNECTED, State.SEARCHING, State.UNCONFIRMED):
            # jeffpiazza/derbynet#218: never STAGING or RUNNING back to
            # CONNECTED.
            timer.state = State.CONNECTED
    elif timer.unhealthy:
        timer.state = State.CONNECTED
    agent = msg.row.agent
    if agent and agent != msg.user_agent:
        msg.out.append(f"<competing>{php_escape(agent)}</competing>\n")


def _malfunction(msg: _Message) -> None:
    error = msg.get("error") if msg.has("error") else "Malfunction"
    timer = msg.timer
    if not timer.troubled:
        msg.records.append(
            audit.Record(
                audit.Action.TIMER_MALFUNCTION,
                *(_ref(msg.race) or (None, None)),
                detail={"other": error},
            )
        )
    msg.out.append(SUCCESS)
    if timer.state == State.NOT_CONNECTED:
        timer.state = State.CONNECTED
    if msg.has("detectable") and php_equals(0, msg.get("detectable")):
        timer.set_malfunction(msg.now, error)
    else:
        timer.set_unhealthy(error)


def _flags(msg: _Message) -> None:
    decode_flags(msg.event, msg.fields)
    msg.out.append(SUCCESS)


_BRANCHES = {
    "HELLO": _hello,
    "IDENTIFIED": _identified,
    "STARTED": _started,
    "FINISHED": _finished,
    "HEARTBEAT": _heartbeat,
    "MALFUNCTION": _malfunction,
    "FLAGS": _flags,
}


def _after(msg: _Message) -> None:
    """The children every message ends with, in PHP's order."""
    row, timer, settings = msg.row, msg.timer, msg.settings
    # A FINISHED clears the staged heat and writes test results directly.
    row.refresh_from_db(fields=["staged_round", "staged_heat", "test_lane_mask"])
    current = _current(msg.race)
    staged = _staged(row)
    if staged is not None and staged != (current.roundid, current.heat):
        msg.out.append("\n <abort />\n")
        if timer.state != State.UNCONFIRMED:
            timer.state = State.CONNECTED
        _stage(row, None)

    tstate = timer.state
    if msg.racing and tstate in (State.CONNECTED, State.UNCONFIRMED):
        ready = heat_ready(current, settings, row.test_lane_mask)
        if ready is not None:
            msg.out.append(ready)
            if tstate == State.CONNECTED:
                timer.state = State.STAGING
            _stage(row, current)

    msg.out.extend(_send_assignments(msg.event))
    if take_remote_start(msg.event):
        msg.out.append("  <remote-start/>\n")
    send = "true" if settings.timer_send_logs else "false"
    msg.out.append(f"<remote-log send='{send}'/>\n")
    if not msg.racing and tstate in (State.STAGING, State.RUNNING):
        msg.out.append("\n  <abort/>\n")
        timer.state = State.CONNECTED
        _stage(row, None)


def lane_mask(lanes: int, used: int, reverse: bool) -> int:
    """The `heat-ready` lane mask: bit 0 for lane 1, mirrored when the
    lanes are numbered from the other end (`reverse-lanes`)."""
    if not reverse:
        return used
    return sum(1 << (lanes - lane - 1) for lane in range(lanes) if used & (1 << lane))


def heat_ready(current: _Current, settings: RaceSettings, test_mask: int) -> str | None:
    """`<heat-ready/>` for the current heat, or None once it has a result."""
    lanes = settings.lane_count
    used = 0
    if current.roundid == hs.TIMER_TEST_ROUND:
        used = (1 << lanes) - 1 - test_mask
    elif current.roundid > 0:
        for lane, time, place in (
            HeatLane.objects.filter(round_id=current.roundid, heat=current.heat)
            .order_by("lane")
            .values_list("lane", "finish_time", "finish_place")
        ):
            if time or place:
                return None
            used |= 1 << (lane - 1)
    mask = lane_mask(lanes, used, settings.reverse_lanes)
    return (
        f'  <heat-ready lane-mask="{mask}"'
        f' class="{php_escape(current.class_name)}"'
        f' round="{_blank(current.round)}"'
        f' roundid="{current.roundid}"'
        f' heat="{_blank(current.heat)}"'
        f' lanes="{lanes}"'
        "/>\n"
    )


# ----- flags, ports and devices (timer-flags.inc) -----


def _cut(text: str, column: str) -> str:
    return text[: TimerSetting._meta.get_field(column).max_length]


def decode_flags(event: Event, fields: Mapping) -> None:
    """A FLAGS message: every row with nothing pending is replaced by what
    the timer reports, in the order it reports it."""
    rows = TimerSetting.objects.filter(event=event)
    rows.filter(pending__isnull=True).delete()
    Kind = TimerSetting.Kind
    for key, value in fields.items():
        if not isinstance(value, str):
            continue
        if key.startswith("flag-"):
            value_type, _, flag_value = value.partition(":")
            TimerSetting.objects.create(
                event=event,
                kind=Kind.FLAG,
                name=_cut(key[5:], "name"),
                value_type=_cut(value_type, "value_type"),
                value=_cut(flag_value, "value"),
                description=None,
            )
        elif key.startswith("desc-"):
            rows.filter(kind=Kind.FLAG, name=key[5:]).update(
                description=_cut(value, "description")
            )
        elif key == "ports":
            TimerSetting.objects.create(
                event=event, kind=Kind.PORTS, name="ports", value=_cut(value, "value")
            )
        elif key.startswith("device-"):
            TimerSetting.objects.create(
                event=event,
                kind=Kind.DEVICE,
                name=_cut(key[7:], "name"),
                description=_cut(value, "description"),
            )


def _send_assignments(event: Event) -> list[str]:
    """The changes the race coordinator asked for, sent once, then a query
    for the timer's new flags. `derby-timer.jar` reads only the first
    `<assign-flag>` of a reply (`HttpTask.java`); all are sent, as PHP
    sends them. A flag's name is the timer's own, written raw as PHP
    writes it."""
    Kind = TimerSetting.Kind
    rows = TimerSetting.objects.filter(event=event)
    out = []
    for kind, name, pending in (
        rows.filter(pending__isnull=False)
        .order_by("id")
        .values_list("kind", "name", "pending")
    ):
        if kind == Kind.FLAG:
            out.append(
                f'<assign-flag flag="{name}"'
                f' value="{php_escape(pending)}"/>\n'
            )
        elif kind == Kind.PORTS:
            port = f' port="{php_escape(pending)}"' if pending != "" else ""
            out.append(f"<assign-port{port}/>\n")
        elif kind == Kind.PENDING_DEVICE:
            device = f' device="{php_escape(pending)}"' if pending != "" else ""
            out.append(f"<assign-device{device}/>\n")
    if out:
        rows.update(pending=None)
        rows.filter(kind=Kind.PENDING_DEVICE).delete()
        out.append("  <query/>\n")
    return out


def _assign(event: Event, write) -> None:
    """A change for the next timer message to send. It locks the timer's
    row, as a message does, so a FLAGS message never deletes a setting
    between the lookup and the write."""
    with transaction.atomic():
        TimerStatus.objects.select_for_update().get(event=event)
        write(TimerSetting.objects.filter(event=event))


def assign_flag(event: Event, flag: str, value: str) -> None:
    _assign(
        event,
        lambda rows: rows.filter(kind=TimerSetting.Kind.FLAG, name=flag).update(
            pending=_cut(value, "pending")
        ),
    )


def assign_port(event: Event, port: str) -> None:
    _assign(
        event,
        lambda rows: rows.filter(kind=TimerSetting.Kind.PORTS, name="ports").update(
            pending=_cut(port, "pending")
        ),
    )


def assign_device(event: Event, device: str) -> None:
    _assign(
        event,
        lambda rows: TimerSetting.objects.create(
            event=event,
            kind=TimerSetting.Kind.PENDING_DEVICE,
            name="",
            pending=_cut(device, "pending"),
        ),
    )


def settings_report(event: Event) -> dict:
    """`query=timer.settings`: the ports, devices and flags the timer last
    reported."""
    report: dict = {}
    devices, flags = [], []
    for row in TimerSetting.objects.filter(event=event).order_by("id"):
        if row.kind == TimerSetting.Kind.FLAG:
            flags.append(
                {
                    "name": row.name,
                    "type": row.value_type,
                    "value": row.value,
                    "description": row.description,
                }
            )
        elif row.kind == TimerSetting.Kind.DEVICE:
            devices.append({"name": row.name, "description": row.description})
        elif row.kind == TimerSetting.Kind.PORTS:
            report["ports"] = row.value
    report["devices"] = devices
    report["flags"] = flags
    return report


def details(event: Event) -> dict:
    """`json_timer_details()`."""
    row = TimerStatus.objects.get(event=event)
    return {
        "type": row.timer_type,
        "human": row.human,
        "ident": row.ident,
        "options": row.options,
    }


# ----- remote start -----


def has_remote_start(row: TimerStatus, now: datetime) -> bool:
    seen = row.remote_start_seen_at
    return seen is not None and seen + REMOTE_START_TIMEOUT >= now


def request_remote_start(event: Event, now: datetime | None = None) -> bool:
    """`timer.remote-start`: queue a start for the next timer message, if
    the timer offered remote start within the last minute."""
    now = now or timezone.now()
    queued = TimerStatus.objects.filter(
        event=event, remote_start_seen_at__gte=now - REMOTE_START_TIMEOUT
    ).update(remote_start_requested=True)
    return queued == 1


def take_remote_start(event: Event) -> bool:
    """Whether a start is queued, clearing it in the same statement, so one
    `<remote-start/>` goes out per queued start however many messages
    arrive together."""
    return (
        TimerStatus.objects.filter(event=event, remote_start_requested=True).update(
            remote_start_requested=False
        )
        == 1
    )


# ----- the timer test (timer-test.inc) -----


def _test_start(race: RaceState, row: TimerStatus) -> None:
    if not race.timer_test and race.current_round_id is not None:
        row.test_saved_round_id = race.current_round_id
        row.test_saved_heat = race.current_heat
    heat = race.current_heat
    transition(race, hs.TimerTestStart(heat + 1 if heat and heat > 0 else 1))


def _test_end(race: RaceState, row: TimerStatus) -> bool:
    if not race.timer_test:
        return False
    saved = None
    if row.test_saved_round_id is not None and row.test_saved_heat is not None:
        saved = (row.test_saved_round_id, row.test_saved_heat)
    transition(race, hs.TimerTestEnd(saved))
    return True


def _test_change(event: Event, change) -> None:
    with transaction.atomic():
        race = lock_race_state(event.id)
        row = TimerStatus.objects.select_for_update().get(event=event)
        change(race, row)
        row.save(
            update_fields=["test_saved_round", "test_saved_heat", "test_lane_mask"]
        )


def set_test_mode(event: Event, on: bool) -> None:
    """`timer.test` with `test-mode`: start or end the timer test."""
    _test_change(event, _test_start if on else _test_end)


def set_test_mask(event: Event, mask: int) -> None:
    """`timer.test` with `tt-mask`: the lanes the test leaves out, then a
    new test heat so the timer gets the new mask."""

    def change(race, row):
        was_testing = _test_end(race, row)
        row.test_lane_mask = mask
        if was_testing:
            _test_start(race, row)
        _test_start(race, row)

    _test_change(event, change)


# ----- the timer log (timer-logging.inc, post-timer-log.php) -----

_LOG_NAME = re.compile(r"timer-\d{8}-\d{4}-\d{2}\.log")


def _logs_folder(settings: RaceSettings) -> Path:
    """The event's logs folder, made if it is missing, or the system's
    temporary folder when the event has none, as in PHP."""
    if settings.logs_directory:
        folder = Path(settings.logs_directory)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            return folder
        except OSError:
            pass
    return Path(tempfile.gettempdir())


def new_log_path(settings: RaceSettings, now: datetime) -> str:
    """A new log file name for a new timer connection. The file is made
    only when the timer sends a log."""
    stamp = timezone.localtime(now).strftime("%Y%m%d-%H%M-%S")
    return str(_logs_folder(settings) / f"timer-{stamp}.log")


def _safe_log(settings: RaceSettings, raw: str) -> Path | None:
    """The stored log path, only when it names a timer log in the logs
    folder. The path comes from the database, so nothing else is opened."""
    if not raw:
        return None
    path = Path(raw)
    if not _LOG_NAME.fullmatch(path.name):
        return None
    try:
        folder = _logs_folder(settings).resolve()
        return path if path.resolve().parent == folder else None
    except OSError:
        return None


def append_log(event: Event, data: bytes, now: datetime | None = None) -> int | None:
    """`post-timer-log.php`: append to the timer's log under an exclusive
    lock. Returns the bytes written, or None when the file cannot be
    opened, after choosing a new file for the next upload."""
    now = now or timezone.now()
    settings = RaceSettings.objects.get(event=event)
    row = TimerStatus.objects.get(event=event)
    path = _safe_log(settings, row.log_path)
    if path is None:
        path = Path(new_log_path(settings, now))
        TimerStatus.objects.filter(event=event).update(log_path=str(path))
    try:
        with path.open("ab") as log:
            fcntl.flock(log, fcntl.LOCK_EX)
            try:
                log.write(data)
            finally:
                fcntl.flock(log, fcntl.LOCK_UN)
    except OSError:
        TimerStatus.objects.filter(event=event).update(
            log_path=new_log_path(settings, now)
        )
        return None
    return len(data)


@dataclass(frozen=True)
class LogRead:
    path: str
    size: int
    data: str | None = None
    found: bool = True


def read_log(event: Event, seek: int) -> LogRead:
    """`query=timer.log`: the log from byte `seek`, as the browser timer
    page tails it."""
    settings = RaceSettings.objects.get(event=event)
    raw = TimerStatus.objects.get(event=event).log_path
    path = _safe_log(settings, raw)
    try:
        if path is None:
            raise FileNotFoundError(raw)
        with path.open("rb") as log:
            size = Path(log.name).stat().st_size
            if size < seek:
                return LogRead(raw, 0)
            if size == seek:
                return LogRead(raw, size)
            fcntl.flock(log, fcntl.LOCK_SH)
            try:
                log.seek(seek)
                data = log.read(size - seek)
            finally:
                fcntl.flock(log, fcntl.LOCK_UN)
    except OSError:
        return LogRead(raw, 0, found=False)
    return LogRead(raw, size, data.decode("utf-8", errors="replace"))


def log_path_hidden() -> bool:
    """PHP's `locked_settings()`: a locked install hides the log's path."""
    return bool(django_settings.LOCK_SETTINGS)
