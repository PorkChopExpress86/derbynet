"""The racing polls of the legacy API (#31): the coordinator payload,
`query=poll`'s value table, and `query=poll.results`.

Ported from `website/ajax/query.poll.coordinator.inc`, `query.poll.inc`,
`query.poll.results.inc` and the `website/inc/json-*.inc` builders they call.
Each function returns PHP's JSON shape as plain data; the legacy handlers
encode it, and the new dashboard (#41) reads the same coordinator payload.
Nothing here writes. PHP's `TimerState` writes `NOT CONNECTED` back when it
reads a timer that has timed out; here the poll only reports it.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from django.db.models import Exists, Max, OuterRef, Q
from django.utils import timezone

from core import race_settings
from core.models import Event, RaceSettings, RaceState
from core.setup_details import plural
from kiosks.models import ReplayStatus
from racing.engine import heat_state as hs
from racing.engine.standings.ordinals import ordinal
from racing.models import HeatLane, PlaylistEntry, RosterEntry, Round, TimerStatus
from racing.services import rounds as rounds_service
from racing.services.heats import LastHeat, engine_state, load_chart
from racing.services.timer import TimerState, has_remote_start
from roster.models import Group, GroupConstituentSubgroup, Racer
from roster.services import car_numbers, structure
from roster.services.groups import labels
from roster.services.racers import CAR_PHOTOS, HEADSHOTS, photo_url, render_label

FULL_NAME = 0
FIRST_NAME_LAST_INITIAL = 1

REPLAY_TIMEOUT = timedelta(seconds=10)
"""`REPLAY_TIMEOUT_LIMIT` in `website/inc/replay.inc`."""
RESULTS_WINDOW = timedelta(seconds=300)
"""`poll.results` also lists every result written in the last five minutes."""

# `$fps_to_scale_mph` in `json-heat-results.inc`: feet per second to scale
# miles per hour, for a 7" car that scales to 14.5 feet.
FPS_TO_SCALE_MPH = 3600 * 25 / 5280

REPLAY_STATUS = {
    0: "Ready",
    1: "Recording",
    2: "Playing",
    -1: "Connecting...",
    -2: "No video source chosen",
    -3: "No audio source chosen",
    -4: "Recording error",
}


# ----- the current heat, as `get_running_round()` reads it ---------------------


@dataclass(frozen=True)
class Running:
    """`get_running_round()`: `RoundID` is -1 with no current round, and the
    timer test's -100; `round` and `class` come from the round only when it
    belongs to the current group."""

    state: RaceState
    settings: RaceSettings
    classid: int | None
    roundid: int
    heat: int | None
    round: int | None
    class_name: str

    @property
    def round_id(self) -> int | None:
        """The current round's id, or None with no round or in the timer test."""
        return self.roundid if self.roundid > 0 else None


def running(event: Event) -> Running:
    state = RaceState.objects.get(event=event)
    settings = RaceSettings.objects.get(event=event)
    number, name = None, ""
    if state.timer_test:
        roundid, number, name = hs.TIMER_TEST_ROUND, 1, "Timer Test"
    elif state.current_round_id is not None:
        roundid = state.current_round_id
        found = (
            Round.objects.filter(pk=roundid, group_id=state.current_group_id)
            .values_list("number", "group__name")
            .first()
        )
        if found is not None:
            number, name = found
    else:
        roundid = -1
    return Running(
        state=state,
        settings=settings,
        classid=state.current_group_id,
        roundid=roundid,
        heat=state.current_heat,
        round=number,
        class_name=name,
    )


# ----- formatting ----------------------------------------------------------------


def format_time(fmt: str, value) -> str:
    """PHP's `sprintf()` of a finish time with `finish-formatting`, such as
    `%5.3f`. A missing time formats as zero, as PHP's null does."""
    try:
        return fmt % float(value or 0)
    except TypeError, ValueError:
        return f"{float(value or 0):5.3f}"


def mangled_name(first: str, last: str, style: int) -> str:
    """`mangled_name()` in `website/inc/name-mangler.inc`."""
    if style == FIRST_NAME_LAST_INITIAL:
        return f"{first} {last[:1]}."
    return f"{first} {last}"


def _epoch(moment: datetime | None) -> int:
    return int(moment.timestamp()) if moment is not None else 0


def _photo_mtime(folder: str, path: str) -> int:
    try:
        return int((Path(folder) / path).stat().st_mtime)
    except OSError:
        return 0


@dataclass(frozen=True)
class Render:
    """A photo render the poll names: a repository and a render label."""

    repository: str
    label: str

    def url(self, settings: RaceSettings, racer: Racer) -> str:
        if self.repository == CAR_PHOTOS:
            return photo_url(
                CAR_PHOTOS, settings.car_photo_directory, racer.car_photo, self.label
            )
        return photo_url(
            HEADSHOTS, settings.photo_directory, racer.headshot, self.label
        )


# ----- the coordinator payload's parts --------------------------------------------


def current_heat(run: Running) -> dict:
    """`json_current_heat()`."""
    settings = run.settings
    round_id = run.round_id
    heats = HeatLane.objects.filter(round_id=round_id) if round_id else None
    heat = {
        "now_racing": run.state.racing,
        "use_master_sched": settings.use_master_sched,
        "use_points": settings.use_points,
        "classid": run.classid,
        "roundid": run.roundid,
        "round": run.round,
        "tbodyid": run.round if settings.use_master_sched else run.roundid,
        "heat": run.heat,
        "number-of-heats": (heats.aggregate(n=Max("heat"))["n"] or 0)
        if heats is not None
        else 0,
    }
    if Group.objects.filter(event_id=run.state.event_id).count() > 1:
        heat["class"] = run.class_name
    if settings.use_master_sched:
        master = None
        if heats is not None:
            master = (
                heats.filter(heat=run.heat)
                .values_list("master_heat", flat=True)
                .first()
            )
        heat["masterheat"] = False if master is None else master
        highest = HeatLane.objects.filter(event_id=run.state.event_id).aggregate(
            n=Max("master_heat")
        )["n"]
        heat["max_masterheat"] = False if highest is None else highest
    return heat


def current_racers(
    run: Running,
    name_style: int,
    render: Render | None = None,
    render2: Render | None = None,
) -> list[dict]:
    """`json_current_racers()`: the racers of the current heat by lane."""
    settings = run.settings
    if run.round_id is None or run.heat is None:
        return []
    rows = (
        HeatLane.objects.filter(round_id=run.round_id, heat=run.heat)
        .select_related("racer__subgroup")
        .order_by("lane")
    )
    racers = []
    for row in rows:
        racer = row.racer
        entry = {
            "lane": row.lane,
            "racerid": racer.pk,
            "name": mangled_name(racer.first_name, racer.last_name, name_style),
            "carname": racer.car_name,
            "carnumber": racer.car_number,
            "note": racer.note,
            "photo": render.url(settings, racer) if render else "",
            "finishtime": format_time(settings.finish_formatting, row.finish_time)
            if row.finish_time
            else "",
            "finishplace": row.finish_place if row.finish_place else "",
        }
        if render2:
            entry["photo2"] = render2.url(settings, racer)
        if settings.use_subgroups:
            entry["subgroup"] = racer.subgroup.name
        racers.append(entry)
    return racers


def _timer(event: Event, now: datetime) -> TimerState:
    return TimerState.read(TimerStatus.objects.get(event=event), now)


def timer_state(event: Event, now: datetime | None = None) -> dict:
    """`json_timer_state()`, with the contact timeout applied."""
    now = now or timezone.now()
    settings = RaceSettings.objects.get(event=event)
    row = TimerStatus.objects.get(event=event)
    timer = TimerState.read(row, now)
    message, icon = _timer_status(timer, now)
    return {
        "lanes": settings.lane_count,
        "last-contact": -1 if timer.assume_connected else _epoch(timer.last_contact),
        "state": int(timer.state),
        "icon": icon,
        "remote-start": has_remote_start(row, now),
        "message": message,
    }


def _timer_status(timer: TimerState, now: datetime) -> tuple[str, str]:
    """`expand_timer_state_status()` in `website/inc/timer-state-xml.inc`."""
    State = TimerStatus.State
    state = timer.state
    if state == State.NOT_CONNECTED:
        return "NOT CONNECTED", "img/status/not_connected.png"
    if state == State.SEARCHING:
        return "Scanning for timer", "img/status/unknown.png"
    if timer.troubled:
        return timer.malfunction_message, "img/status/trouble.png"
    if state == State.CONNECTED:
        seconds = (
            0
            if timer.assume_connected
            else int((now - timer.last_contact).total_seconds())
        )
        return f"CONNECTED ({seconds} sec.)", "img/status/ok.png"
    if state == State.STAGING:
        return "Staging", "img/status/ok.png"
    if state == State.RUNNING:
        return "Race running", "img/status/ok.png"
    if state == State.UNCONFIRMED:
        return "UNCONFIRMED", "img/status/unknown.png"
    return f"Unknown ({state})", "img/status/trouble.png"


def timer_troubled(event: Event, now: datetime | None = None) -> bool:
    """`TimerState::troubled()`: unhealthy, or a malfunction in force."""
    return _timer(event, now or timezone.now()).troubled


def replay_state(event: Event, now: datetime | None = None) -> dict:
    """`json_replay()`."""
    now = now or timezone.now()
    replay = ReplayStatus.objects.get(event=event)
    state = replay.state
    if replay.last_contact is None or replay.last_contact + REPLAY_TIMEOUT < now:
        state = ReplayStatus.State.NOT_CONNECTED
    if state == ReplayStatus.State.NOT_CONNECTED:
        message, icon = "NOT CONNECTED", "img/status/not_connected.png"
    elif state == ReplayStatus.State.CONNECTED:
        try:
            code = int(replay.reported or 0)
        except ValueError:
            code = 0
        seconds = int((now - replay.last_contact).total_seconds())
        message = f"{REPLAY_STATUS.get(code, '')} ({seconds} sec.)"
        icon = "img/status/ok.png"
    else:
        message, icon = f"TROUBLE: {replay.trouble}", "img/status/trouble.png"
    return {
        "last_contact": _epoch(replay.last_contact),
        "state": int(state),
        "icon": icon,
        "connected": state != ReplayStatus.State.NOT_CONNECTED,
        "message": message,
    }


def last_heat(run: Running) -> str:
    """`json_last_heat()`: `none`, `available` or `recoverable`."""
    return LastHeat.kind(run.state.last_heat)


def heat_results(event: Event, raw_round, raw_heat) -> list[dict]:
    """`json_heat_results()`: the lanes of a heat that have a result. The
    round and heat are what the polling page shows as current."""
    try:
        round_id, heat = int(raw_round), int(raw_heat)
    except TypeError, ValueError:
        return []
    settings = RaceSettings.objects.get(event=event)
    rows = (
        HeatLane.objects.filter(event=event, round_id=round_id, heat=heat)
        .exclude(finish_time__isnull=True, finish_place__isnull=True)
        .order_by("lane")
    )
    results = []
    for row in rows:
        time = row.finish_time
        speed = (
            "--"
            if not time
            else "%4.1f" % (settings.track_length / time * FPS_TO_SCALE_MPH)
        )
        results.append(
            {
                "lane": row.lane,
                "time": format_time(settings.finish_formatting, time),
                "place": row.finish_place,
                "speed": speed,
            }
        )
    return results


def timer_test(event: Event) -> dict:
    """`timer_test_recorded_results()`: the heat of the stored test results,
    the test's lane mask, and the results."""
    row = TimerStatus.objects.get(event=event)
    return {
        "heat": row.test_results_heat or 0,
        "mask": row.test_lane_mask,
        "results": timer_test_results(event),
    }


def timer_test_results(event: Event) -> list[dict]:
    """`timer_test_recorded_results()['results']`: each lane's test time and
    place, by lane, a missing place counted from the times."""
    recorded = TimerStatus.objects.get(event=event).test_results or {}
    results = []
    for lane in sorted(recorded, key=int):
        entry = {"lane": int(lane)}
        if "time" in recorded[lane]:
            entry["time"] = float(recorded[lane]["time"])
        if "place" in recorded[lane]:
            entry["place"] = recorded[lane]["place"]
        results.append(entry)
    times = sorted(r["time"] for r in results if "time" in r)
    for entry in results:
        if "place" not in entry:
            entry["place"] = 1 + times.index(entry["time"]) if "time" in entry else 1
    return results


def _next_round_id(run: Running) -> int | None:
    """The round of the playlist step after the current round's, or of the
    first step not yet raced, as `json_all_rounds()` finds it."""
    event_id = run.state.event_id
    entries = PlaylistEntry.objects.filter(event_id=event_id).order_by("seq")
    current = None
    if run.round_id is not None:
        found = (
            Round.objects.filter(pk=run.round_id)
            .values_list("group_id", "number")
            .first()
        )
        if found is not None:
            current = entries.filter(group_id=found[0], round_number=found[1]).first()
    if current is not None:
        step = entries.filter(seq__gt=current.seq).first()
    else:
        step = next((e for e in entries if _unraced(e)), None)
    if step is None:
        return None
    return (
        Round.objects.filter(group_id=step.group_id, number=step.round_number)
        .values_list("id", flat=True)
        .first()
    )


def _unraced(entry: PlaylistEntry) -> bool:
    """`first_playlist_entry()`: no round, no chart, or a heat lane with no
    result."""
    round = Round.objects.filter(
        group_id=entry.group_id, number=entry.round_number
    ).first()
    if round is None:
        return True
    lanes = HeatLane.objects.filter(round=round)
    return (
        not lanes.exists()
        or lanes.filter(finish_time__isnull=True, finish_place__isnull=True).exists()
    )


def _adjustment_rows(why: str, racer_ids) -> list[dict]:
    racers = Racer.objects.filter(pk__in=racer_ids).order_by("id")
    return [
        {
            "why": why,
            "racerid": r.pk,
            "lastname": r.last_name,
            "firstname": r.first_name,
            "carnumber": r.car_number,
        }
        for r in racers
    ]


def all_rounds(run: Running) -> list[dict]:
    """`json_all_rounds()`: every round with its counts and the racers a
    reschedule would add or take off."""
    event = run.state.event
    next_round = _next_round_id(run)
    pending = rounds_service.adjustments(event)
    result = []
    for counts in rounds_service.round_counts(event):
        round = counts.round
        adjustments = []
        if counts.unscheduled > 0 and counts.heats_scheduled > 0:
            adjustments += _adjustment_rows(
                "unscheduled", pending[round.id].unscheduled
            )
        if counts.departed > 0:
            adjustments += _adjustment_rows("departed", pending[round.id].departed)
        entry = {
            "roundid": round.id,
            "classid": round.group_id,
            "class": round.group.name,
            "round": round.number,
            "aggregate": counts.aggregate,
            "roster_size": counts.roster_size,
            "passed": counts.passed,
            "unscheduled": counts.unscheduled,
            "adjustments": adjustments,
            "heats_scheduled": counts.heats_scheduled,
            "heats_run": counts.heats_run,
            "name": counts.name,
        }
        if round.id == next_round:
            entry["next-round"] = True
        result.append(entry)
    return result


def ready_aggregates(event: Event) -> list[dict]:
    """`ready-aggregate`: the aggregate groups whose rosters can be built."""
    by_subgroup = set(
        GroupConstituentSubgroup.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    )
    return [
        {"classid": g.id, "by-subgroup": g.id in by_subgroup, "class": g.name}
        for g in rounds_service.ready_aggregate_groups(event)
    ]


def coordinator(event: Event, shown_round=None, shown_heat=None) -> dict:
    """The coordinator payload of `query=poll.coordinator`, which ten
    actions also append to their reply. `shown_round` and `shown_heat` are
    the heat the coordinator page shows, for `heat-results`; PHP's defaults
    are round 0, which names no round, and heat 1."""
    run = running(event)
    return {
        "current-heat": current_heat(run),
        "racers": current_racers(run, FULL_NAME),
        "timer-state": timer_state(event),
        "replay-state": replay_state(event),
        "last-heat": last_heat(run),
        "refused-results": run.state.refused_results,
        "heat-results": heat_results(
            event,
            "0" if shown_round is None else shown_round,
            "1" if shown_heat is None else shown_heat,
        ),
        "classes": structure.classes(event),
        "rounds": all_rounds(run),
        "ready-aggregate": ready_aggregates(event),
    }


# ----- `query=poll`: one function per value key -----------------------------------


@dataclass(frozen=True)
class PollArgs:
    """The `query=poll` request parameters some values read."""

    roundid: str | None = None
    heat: str | None = None
    row_height: str | None = None
    head_size: str | None = None
    car_size: str | None = None
    repo: str | None = None


type PollValue = Callable[[Running, PollArgs], dict]
"""A value key's function: the top-level keys it adds to the reply."""


def _best_times(run: Running, args: PollArgs) -> dict:
    """The 15 best results of racers on the roster of the heat's round,
    by time, or by place for heats that record places only."""
    on_roster = RosterEntry.objects.filter(
        round_id=OuterRef("round_id"), racer_id=OuterRef("racer_id")
    )
    rows = (
        HeatLane.objects.filter(event_id=run.state.event_id)
        .filter(Exists(on_roster))
        .select_related("racer")
    )
    best = []
    for row in rows:
        if row.finish_time is not None:
            if row.finish_time > 0:
                best.append((row.finish_time, row.pk, row.racer.car_number))
        elif row.finish_place is not None and row.finish_place > 0:
            best.append((row.finish_place, row.pk, row.racer.car_number))
    best.sort()
    return {
        "best-times": [
            {"carnumber": car, "finishtime": value} for value, _, car in best[:15]
        ]
    }


def _car_numbers(run: Running, args: PollArgs) -> dict:
    return {"car-numbers": car_numbers.next_car_numbers(run.state.event_id)}


def _classes(run: Running, args: PollArgs) -> dict:
    return {"classes": structure.classes(run.state.event)}


def _current_heat(run: Running, args: PollArgs) -> dict:
    return {"current-heat": current_heat(run)}


def _current_reschedule(run: Running, args: PollArgs) -> dict:
    """`json_current_reschedule()`: whether a reschedule of the current
    round would add or take off a racer."""
    round = Round.objects.filter(pk=run.round_id).first() if run.round_id else None
    if round is None:
        return {"current-reschedule": False}
    pending = rounds_service.round_adjustments(round)
    return {"current-reschedule": bool(pending.unscheduled or pending.departed)}


def ondeck(run: Running) -> dict:
    """`json_ondeck()`: the next heat, and every heat lane with its racer and
    result. Outside the master schedule PHP never narrows the query, so the
    chart holds every heat lane of the event (C25). That is kept for
    parity, and is a known cost on a large event (#52)."""
    settings = run.settings
    event_id = run.state.event_id
    style = settings.name_style
    out: dict = {}
    nxt = next_heat(run)
    if nxt is not None:
        out["next"] = {"roundid": nxt[0], "heat": nxt[1]}
    rows = HeatLane.objects.filter(event_id=event_id).select_related(
        "round__group", "racer"
    )
    if settings.use_master_sched:
        rows = sorted(
            rows.filter(round__number=run.round), key=lambda r: (r.master_heat, r.lane)
        )
    else:
        rows = sorted(
            rows,
            key=lambda r: (
                r.round.number,
                r.round.group.sort_order,
                r.round.group_id,
                r.heat,
                r.lane,
            ),
        )
    chart = []
    for row in rows:
        racer = row.racer
        if row.completed is None:
            result = ""
        elif settings.use_points:
            result = "z" + (ordinal(row.finish_place) or "")
        else:
            result = "z" + format_time(settings.finish_formatting, row.finish_time)
        chart.append(
            {
                "resultid": row.pk,
                "roundid": row.round_id,
                "heat": row.heat,
                "lane": row.lane,
                "racerid": racer.pk,
                "name": mangled_name(racer.first_name, racer.last_name, style),
                "carnumber": racer.car_number,
                "masterheat": row.master_heat,
                "result": result,
                "carphoto": {
                    "basename": racer.car_photo.rsplit("/", 1)[-1],
                    "cache-breaker": _photo_mtime(
                        settings.car_photo_directory, racer.car_photo
                    ),
                }
                if racer.car_photo
                else None,
            }
        )
    out["chart"] = chart
    return out


def _ondeck(run: Running, args: PollArgs) -> dict:
    return {"ondeck": ondeck(run)}


def _heat_results(run: Running, args: PollArgs) -> dict:
    if run.roundid == hs.TIMER_TEST_ROUND:
        return {"heat-results": timer_test_results(run.state.event)}
    return {
        "heat-results": heat_results(
            run.state.event,
            "0" if args.roundid is None else args.roundid,
            "1" if args.heat is None else args.heat,
        )
    }


def _last_heat(run: Running, args: PollArgs) -> dict:
    return {"last-heat": last_heat(run)}


def _partitions(run: Running, args: PollArgs) -> dict:
    return {"partitions": structure.partitions(run.state.event)}


def _precision(run: Running, args: PollArgs) -> dict:
    return {"precision": 4 if run.settings.finish_formatting == "%6.4f" else 3}


def _race_structure(run: Running, args: PollArgs) -> dict:
    """Four top-level keys, as PHP writes them."""
    names = labels(run.settings)
    return {
        "use-subgroups": run.settings.use_subgroups,
        "group-formation-rule": run.settings.group_formation_rule,
        "labels": {
            "supergroup": [names.supergroup, plural(names.supergroup)],
            "partition": [names.partition, plural(names.partition)],
            "group": [names.group, plural(names.group)],
            "subgroup": [names.subgroup, plural(names.subgroup)],
        },
        "full-field-calc": race_settings.read(run.state.event, "full-field-calc"),
    }


def _render(repository: str | None, label: str) -> Render | None:
    if repository not in (HEADSHOTS, CAR_PHOTOS):
        return None
    return Render(repository, render_label(label))


def _positive(text: str | None) -> float:
    try:
        return float(text) if text is not None else 0
    except ValueError:
        return 0


def _racers(run: Running, args: PollArgs) -> dict:
    """The current heat's racers, with photos at the size the now-racing
    page asks for. In the timer test, one entry per lane."""
    if run.roundid == hs.TIMER_TEST_ROUND:
        return {"racers": [{"lane": n} for n in range(1, run.settings.lane_count + 1)]}
    render = render2 = None
    if args.head_size is not None:
        render = _render(HEADSHOTS, args.head_size)
        if args.car_size is not None:
            render2 = _render(CAR_PHOTOS, args.car_size)
    elif _positive(args.row_height) > 0:
        repository = args.repo if args.repo is not None else None
        if repository is None:
            repository = run.settings.photos_on_now_racing
        render = _render(
            repository,
            f"{2 * int(_positive(args.row_height))}x{int(_positive(args.row_height))}",
        )
    return {"racers": current_racers(run, run.settings.name_style, render, render2)}


def _replay_state(run: Running, args: PollArgs) -> dict:
    return {"replay-state": replay_state(run.state.event)}


def _rounds(run: Running, args: PollArgs) -> dict:
    return {"rounds": all_rounds(run)}


def schedule(run: Running) -> list[list[int | None]]:
    """`json_schedule()`: the current round's chart, a racer id per lane."""
    nlanes = run.settings.lane_count
    chart: list[list[int | None]] = []
    if run.round_id is None:
        return chart
    for heat, lane, racer_id in (
        HeatLane.objects.filter(round_id=run.round_id)
        .order_by("heat", "lane")
        .values_list("heat", "lane", "racer_id")
    ):
        while heat > len(chart):
            chart.append([None] * nlanes)
        row = chart[heat - 1]
        while lane > len(row):
            row.append(None)
        row[lane - 1] = racer_id
    return chart


def _schedule(run: Running, args: PollArgs) -> dict:
    return {"schedule": schedule(run)}


def _timer_state(run: Running, args: PollArgs) -> dict:
    return {"timer-state": timer_state(run.state.event)}


def _timer_trouble(run: Running, args: PollArgs) -> dict:
    if run.settings.warn_no_timer and timer_troubled(run.state.event):
        return {"timer-trouble": True}
    return {}


def _awards(run: Running, args: PollArgs) -> dict:
    """PHP lists `awards` and writes nothing for it."""
    return {}


POLL_VALUES: Mapping[str, PollValue] = MappingProxyType(
    {
        "awards": _awards,
        "best-times": _best_times,
        "car-numbers": _car_numbers,
        "classes": _classes,
        "current-heat": _current_heat,
        "current-reschedule": _current_reschedule,
        "ondeck": _ondeck,
        "heat-results": _heat_results,
        "last-heat": _last_heat,
        "partitions": _partitions,
        "precision": _precision,
        "race-structure": _race_structure,
        "racers": _racers,
        "replay-state": _replay_state,
        "rounds": _rounds,
        "schedule": _schedule,
        "timer-state": _timer_state,
        "timer-trouble": _timer_trouble,
    }
)
"""`query=poll`'s value keys, each to the function that answers it."""


def poll(event: Event, values: str, args: PollArgs) -> dict:
    """`query=poll&values=a,b,...`: each known key's keys, in the order
    asked. An unknown key adds nothing."""
    run = running(event)
    out: dict = {}
    for key in values.split(","):
        value = POLL_VALUES.get(key)
        if value is not None:
            out.update(value(run, args))
    return out


# ----- navigation and `query=poll.results` ----------------------------------------


def next_heat(run: Running) -> hs.HeatRef | tuple[int | None, int] | None:
    """`get_next_heat($now_running, 'next')`. Outside the master schedule it
    is the next heat number, whether or not that heat exists."""
    chart = load_chart(run.state.event_id, run.settings.use_master_sched)
    round_id = None if run.roundid == -1 else run.roundid
    found = hs.ask(engine_state(run.state), "next", round_id, chart)
    if found is None:
        return None
    if found[0] is None:
        return (-1, found[1])
    return found


def results_cutoff(now: datetime) -> datetime:
    """The start of `poll.results`'s window. The comparison is made on
    aware datetimes, so the server's time zone never splits the window."""
    return now - RESULTS_WINDOW


def schedule_signature(event: Event) -> dict | list:
    """`schedule_signature()` in `website/inc/rounds.inc`: per round,
    `resultid-racerid` for each heat lane in heat and lane order, or "" for
    a round with no chart. Rounds with no chart come first."""
    signature: dict[int, str] = {}
    rounds = Round.objects.filter(event=event).order_by("id")
    charted = set(
        HeatLane.objects.filter(event=event).values_list("round_id", flat=True)
    )
    for round_id in rounds.values_list("id", flat=True):
        if round_id not in charted:
            signature[round_id] = ""
    parts: dict[int, list[str]] = {}
    for round_id, lane_id, racer_id in (
        HeatLane.objects.filter(event=event)
        .order_by("round_id", "round__group_id", "heat", "lane")
        .values_list("round_id", "id", "racer_id")
    ):
        parts.setdefault(round_id, []).append(f"{lane_id}-{racer_id}")
    for round_id, lanes in parts.items():
        signature[round_id] = "/".join(lanes)
    return signature or []


def poll_results(
    event: Event,
    raw_round: str | None = None,
    details: bool = False,
    now: datetime | None = None,
) -> dict:
    """`query=poll.results`: the schedule signature, the results of a round
    (the current one by default) and of every heat lane written in the last
    five minutes, the current heat, and the next."""
    now = now or timezone.now()
    run = running(event)
    settings = run.settings
    out: dict = {"schedule-signature": schedule_signature(event)}
    try:
        round_id = int(raw_round) if raw_round is not None else run.roundid
    except ValueError:
        round_id = None
    rows = (
        HeatLane.objects.filter(event=event)
        .filter(Q(round_id=round_id) | Q(completed__gt=results_cutoff(now)))
        .order_by("id")
    )
    results = []
    for row in rows:
        if row.finish_time is None and row.finish_place is None:
            outcome = "--"
        elif settings.use_points:
            outcome = ordinal(row.finish_place) if row.finish_place else None
        else:
            outcome = "x" + format_time(settings.finish_formatting, row.finish_time)
        if details:
            entry = {
                "resultid": row.pk,
                "roundid": row.round_id,
                "heat": row.heat,
                "lane": row.lane,
                "racerid": row.racer_id,
                "finishtime": row.finish_time,
                "finishplace": row.finish_place,
                "completed": timezone.localtime(row.completed).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if row.completed
                else None,
            }
        else:
            entry = {"resultid": row.pk}
        entry["outcome"] = outcome
        results.append(entry)
    out["results"] = results
    out["current-heat"] = current_heat(run)
    nxt = next_heat(run)
    if nxt is not None:
        out["next-heat"] = {"roundid": nxt[0], "heat": nxt[1]}
    return out
