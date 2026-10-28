"""Race control and the racing polls (#31): `heat.select`, `heat.rerun`,
`heat.reinstate`, `result.write`, `result.delete`, `schedule.generate`,
`schedule.unschedule`, `schedule.reschedule`, `roster.new`, `roster.delete`,
and `query=poll.coordinator`, `query=poll` and `query=poll.results`.

Thin adapters over `racing.services`. Each service takes the `RaceState`
lock and reads the current heat under it, so no handler reads race state
before a service call. The ten actions end with the coordinator payload, as
PHP's `require('ajax/query.poll.coordinator.inc')` appends it, from the one
builder `racing.services.polls.coordinator`.
"""

from accounts.permissions import Access, Perm, granted_for
from legacy_api.registry import REFUSALS, LegacyContext, action, query
from racing.engine import heat_state as hs
from racing.engine.phpcompat import php_intval, php_truthy
from racing.models import Round
from racing.services import heats, polls, reschedule, results, rounds, schedule
from racing.services.reschedule import RescheduleFailure
from racing.services.rounds import (
    FollowOn,
    FromAggregate,
    NewAggregate,
    RoundFailure,
)
from racing.services.schedule import ScheduleFailure
from roster.models import Group, Racer, Subgroup

# The `timing` keys of PHP's `schedule.reschedule` reply, each a number of
# whole seconds. Django's search reports no times, so each is 0.
RESCHEDULE_TIMING = ("load", "inject", "update", "master-heat")


def _text(ctx: LegacyContext, name: str) -> str | None:
    """A posted field as PHP's `isset()` sees it: None when absent. A form
    array is not text, so it reads as absent too."""
    value = ctx.args.get(name)
    return value if isinstance(value, str) else None


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _fail(ctx: LegacyContext, failure) -> None:
    ctx.failure(str(failure.code), failure.description)


def _coordinator(ctx: LegacyContext) -> None:
    """Append the coordinator payload, as the ten actions do."""
    for key, value in polls.coordinator(ctx.event).items():
        ctx.put(key, value)


def _done(ctx: LegacyContext, failure: hs.Failure | None) -> None:
    if failure is None:
        ctx.success()
    else:
        _fail(ctx, failure)


def _fields(ctx: LegacyContext) -> list[tuple[str, str]]:
    return [(key, value) for key, value in ctx.args.items() if isinstance(value, str)]


# ----- heats ---------------------------------------------------------------------


# Every reply that carries `current-heat`: the coordinator payload and the polls.
PAYLOAD = (
    "R3: with no current heat after a round stops being current, classid and"
    " heat are null; PHP reports the -1 that set_current_heat(-1, -1) wrote.",
)
REFUSED_WITH_PAYLOAD = PAYLOAD + (
    "R4: an anonymous caller is refused in the dispatcher, with no payload"
    " (D5). A logged-in caller without CONTROL_RACE gets PHP's notauthorized"
    " and the coordinator payload, which poll.coordinator already shows them.",
)


def _may_control(ctx: LegacyContext) -> bool:
    """PHP refuses `heat.select` and `heat.rerun` inside the handler and
    still appends the coordinator payload."""
    if granted_for(ctx.session) & Perm.CONTROL_RACE:
        return True
    ctx.failure("notauthorized", REFUSALS["notauthorized"])
    _coordinator(ctx)
    return False


@action("heat.select", permission=Access.LOGGED_IN, divergences=REFUSED_WITH_PAYLOAD)
def heat_select(ctx: LegacyContext) -> None:
    """`roundid` alone means its heat 1; `heat` is a number or `next`,
    `prev`, `next-up` or `first`; `now_racing` applies after the move."""
    if not _may_control(ctx):
        return
    round_text, heat = _text(ctx, "roundid"), _text(ctx, "heat")
    round_id = None
    if round_text is not None:
        # A round of another event is not in this event's chart, so the
        # service finds no such heat, as for any id that names no round.
        round_id = _int(round_text) or 0
    racing = _text(ctx, "now_racing")
    failure = heats.select_heat(
        ctx.event,
        round_id=round_id,
        heat=heat,
        now_racing=None if racing is None else php_truthy(racing),
    )
    _done(ctx, failure)
    _coordinator(ctx)


@action("heat.rerun", permission=Access.LOGGED_IN, divergences=REFUSED_WITH_PAYLOAD)
def heat_rerun(ctx: LegacyContext) -> None:
    """`heat=current` reruns the current heat, anything else the last heat
    with a result."""
    if not _may_control(ctx):
        return
    which = "current" if _text(ctx, "heat") == "current" else "last"
    _done(ctx, heats.rerun(ctx.event, which))
    _coordinator(ctx)


@action("heat.reinstate", permission=Perm.CONTROL_RACE, divergences=PAYLOAD)
def heat_reinstate(ctx: LegacyContext) -> None:
    failure = heats.reinstate(ctx.event)
    _done(ctx, failure)
    if failure is None:
        _coordinator(ctx)


# ----- results -------------------------------------------------------------------


@action(
    "result.write",
    permission=Perm.CONTROL_RACE,
    divergences=PAYLOAD
    + (
        "R5: PHP answers a write with no current round or for another heat"
        " with an XML <failure> inside the JSON body; Django answers a JSON"
        " failure with the same code.",
    ),
)
def result_write(ctx: LegacyContext) -> None:
    """`lane<N>` times and `place<N>` places for the current heat, which
    advances only while racing."""
    recorded = results.record_manual_result(ctx.event, _fields(ctx))
    if recorded.failure is not None:
        _fail(ctx, recorded.failure)
        return
    ctx.success()
    _coordinator(ctx)


@action(
    "result.delete",
    permission=Perm.CONTROL_RACE,
    divergences=PAYLOAD + ("C23: no `sql` key; PHP returns the SQL text it ran.",),
)
def result_delete(ctx: LegacyContext) -> None:
    """`roundid` is a round, `current` (the default) or `all`; `heat` is a
    heat number or `current`."""
    round_text = _text(ctx, "roundid")
    heat_text = _text(ctx, "heat")
    round = heat = None
    run = True
    if round_text != "all":
        if round_text is None or round_text == "current":
            round = results.CURRENT
        else:
            round = ctx.resolve(Round, round_text)
            run = round is not None
        if heat_text is not None:
            heat = results.CURRENT if heat_text == "current" else _int(heat_text)
            run = run and heat is not None
    failure = results.delete_results(ctx.event, round, heat) if run else None
    _done(ctx, failure)
    if failure is None:
        _coordinator(ctx)


# ----- schedules -----------------------------------------------------------------


@action(
    "schedule.generate",
    permission=Perm.CONTROL_RACE,
    divergences=PAYLOAD
    + (
        "Q1: a request the generator tables cannot meet fails with"
        " cant-fulfill and keeps the chart.",
        "Q18: a refused generate renumbers no master heat and moves no heat.",
        "R7: the coordinator payload shows the master heats numbered and the"
        " next-up heat selected; PHP's shows the state before.",
        "R6: `trace` adds nothing, and under rotation-schedule nor do PHP's"
        " debug keys nheats, ncars and heats.",
    ),
)
def schedule_generate(ctx: LegacyContext) -> None:
    round = ctx.resolve(Round, _text(ctx, "roundid"))
    runs = _text(ctx, "n_times_per_lane")
    n_times_per_lane = 1 if runs is None else php_intval(runs)
    if round is None:
        # PHP schedules a round that does not exist and finds no racers.
        ctx.failure(
            str(schedule.Failure.TOO_FEW_RACERS),
            "There need to be at least two racers checked in to generate a schedule.",
        )
        return
    try:
        schedule.generate_schedule(round, n_times_per_lane)
    except ScheduleFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()
    _coordinator(ctx)


@action("schedule.unschedule", permission=Perm.CONTROL_RACE, divergences=PAYLOAD)
def schedule_unschedule(ctx: LegacyContext) -> None:
    round = ctx.resolve(Round, _text(ctx, "roundid"))
    if round is not None:
        schedule.unschedule(round)
    ctx.success()
    _coordinator(ctx)


@action(
    "schedule.reschedule",
    permission=Perm.CONTROL_RACE,
    divergences=PAYLOAD
    + (
        "Q5: a dry run leaves racing on.",
        "Q20, Q21, Q25: the charts and failures #20 decided.",
        "R6: `trace` adds no `adjuster`; `timing` holds 0 seconds.",
    ),
)
def schedule_reschedule(ctx: LegacyContext) -> None:
    """Fit late racers in and take departed racers out of a round's unraced
    heats. Only one runs at a time per event: another answers `busy` until
    the first finishes, or for 120 s if it died without finishing."""
    round = ctx.resolve(Round, _text(ctx, "roundid"))
    racer_text = _text(ctx, "racerid")
    racer_id = None
    if racer_text:
        racer = ctx.resolve(Racer, racer_text)
        # An id that names no racer of the event is the service's noracer.
        racer_id = racer.pk if racer is not None else 0
    dry_run = php_truthy(_text(ctx, "dry-run") or "")
    if round is None:
        ctx.failure(str(reschedule.Failure.NOCHART), "Unable to build new schedule")
        return
    try:
        done = reschedule.reschedule(round, racer_id, dry_run)
    except RescheduleFailure as failure:
        _fail(ctx, failure)
        return
    ctx.put("chart", done.chart)
    ctx.put(
        "timing",
        {
            **dict.fromkeys(RESCHEDULE_TIMING, 0),
            "internal": {
                # PHP's `run-N-timed-out` flag is true, not a time.
                key: True if key.endswith("-timed-out") else 0
                for key in done.timing_keys
            },
        },
    )
    ctx.success()
    _coordinator(ctx)


# ----- rosters -------------------------------------------------------------------


def _flagged(ctx: LegacyContext, model, prefix: str) -> list:
    """The rows named by keys `<prefix><id>`, in posted order."""
    rows = []
    for key in ctx.args:
        if key.startswith(prefix):
            row = ctx.resolve(model, key.removeprefix(prefix))
            if row is not None and row not in rows:
                rows.append(row)
    return rows


def _roster_source(ctx: LegacyContext):
    """The round a `roster.new` builds, or None for a `roundid` that names
    no round."""
    round_text = _text(ctx, "roundid")
    if round_text:
        round = ctx.resolve(Round, round_text)
        return FollowOn(round) if round is not None else None
    name = _text(ctx, "classname")
    if name is not None:
        subgroups = _flagged(ctx, Subgroup, "rankid_")
        groups = _flagged(ctx, Group, "classid_")
        return NewAggregate(name, groups=groups, subgroups=subgroups)
    group = ctx.resolve(Group, _text(ctx, "classid"))
    return FromAggregate(group) if group is not None else None


@action(
    "roster.new",
    permission=Perm.CONTROL_RACE,
    divergences=PAYLOAD
    + (
        "S17: needs CONTROL_RACE; PHP lets anyone build a round.",
        "C24: an unknown round fails with invalid-roundid; PHP uses its"
        " message, Invalid roundid, as the code, and then answers 500.",
    ),
)
def roster_new(ctx: LegacyContext) -> None:
    """A follow-on round of `roundid`, round 1 of a new aggregate group
    `classname` drawn from `classid_<n>` groups or `rankid_<n>` subgroups,
    or round 1 of the aggregate group `classid`. `top` racers qualify, or
    with `bucketed` that many from each group or subgroup."""
    source = _roster_source(ctx)
    if source is None:
        if _text(ctx, "roundid"):
            ctx.failure("invalid-roundid", "Invalid roundid")
            return
        ctx.put("finalists", [])
        ctx.failure(str(rounds.Failure.NO_RACERS), "No eligible racers found.")
        return
    try:
        built = rounds.new_round_roster(
            source,
            php_intval(_text(ctx, "top") or ""),
            bucketed=php_truthy(_text(ctx, "bucketed") or ""),
        )
    except RoundFailure as failure:
        ctx.put("finalists", [])
        _fail(ctx, failure)
        return
    ctx.put(
        "finalists",
        [{"racerid": f.racer_id, "score": f.score} for f in built.qualifiers],
    )
    if built.concerns:
        ctx.put("concerns", list(built.concerns))
    ctx.put("new-round", {"roundid": built.round.pk})
    ctx.success()
    _coordinator(ctx)


@action("roster.delete", permission=Perm.CONTROL_RACE, divergences=PAYLOAD)
def roster_delete(ctx: LegacyContext) -> None:
    round_text = _text(ctx, "roundid")
    if round_text is None:
        ctx.failure("missing_param", "No roundid parameter")
        return
    round = ctx.resolve(Round, round_text)
    if round is None:
        ctx.failure("bad_param", "No such round exists")
        return
    try:
        rounds.delete_round_roster(round)
    except RoundFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()
    _coordinator(ctx)


# ----- polls ---------------------------------------------------------------------


@query("poll.coordinator", permission=Access.LOGGED_IN, divergences=PAYLOAD)
def poll_coordinator(ctx: LegacyContext) -> None:
    """`roundid` and `heat` name the heat the coordinator page shows, for
    `heat-results`. PHP answers anyone; the payload names racers, so it
    needs a login (#17, D5)."""
    payload = polls.coordinator(ctx.event, _text(ctx, "roundid"), _text(ctx, "heat"))
    for key, value in payload.items():
        ctx.put(key, value)


@query("poll", permission=Access.ANONYMOUS, divergences=PAYLOAD)
def poll(ctx: LegacyContext) -> None:
    """`values` lists the keys of `racing.services.polls.POLL_VALUES` to
    answer, in order. The kiosks poll it anonymously (D5)."""
    args = polls.PollArgs(
        roundid=_text(ctx, "roundid"),
        heat=_text(ctx, "heat"),
        row_height=_text(ctx, "row-height"),
        head_size=_text(ctx, "head-size"),
        car_size=_text(ctx, "car-size"),
        repo=_text(ctx, "repo"),
    )
    for key, value in polls.poll(ctx.event, _text(ctx, "values") or "", args).items():
        ctx.put(key, value)


@query(
    "poll.results",
    permission=Access.ANONYMOUS,
    divergences=PAYLOAD
    + (
        "R8: the body starts at `{`; PHP's query.poll.results.inc writes a"
        " space before its JSON.",
    ),
)
def poll_results(ctx: LegacyContext) -> None:
    """The results-by-racer kiosk's poll: `roundid` (the current round by
    default) and `details` for every column."""
    found = polls.poll_results(
        ctx.event, _text(ctx, "roundid"), details="details" in ctx.args
    )
    for key, value in found.items():
        ctx.put(key, value)
