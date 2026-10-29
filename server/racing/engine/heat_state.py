"""The current-heat state machine: heat selection, racing on and off, results,
reruns and the advance that ends a round.

Ported from `website/inc/autoadvance.inc`, `website/inc/racing-state.inc`,
`set_current_heat` in `website/inc/data.inc`, and the state changes of
`website/ajax/action.heat.select.inc`, `action.heat.rerun.inc`,
`action.heat.reinstate.inc`, `action.result.write.inc` and the `FINISHED`
branch of `action.timer-message.inc`.

`TABLE` holds every transition as data. `decide` finds the first row for the
event whose guard holds and runs its steps in order. A step changes the
state, emits an effect for the service to carry out, or decides a follow-on
event, as PHP's handlers call one another. A follow-on event sees the state
and the chart as the steps before it left them, so `decide` returns the
whole transition: the final state, the effects in the order PHP performs
them (replay commands are observable), and the first failure.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from racing.engine.phpcompat import php_equals
from racing.engine.scheduling.master import master_target

type HeatRef = tuple[int, int]  # (roundid, heat)

TIMER_TEST_ROUND = -100
"""PHP's `TIMER_TEST_ROUNDID`: the round of the current heat while the
timer is tested. The heat counts the test runs."""


class Kind(StrEnum):
    NONE = "none"
    TIMER_TEST = "timer-test"
    HEAT = "heat"


@dataclass(frozen=True)
class State:
    """`RoundID`, `Heat`, `NowRacingState`, `refused-results`, whether the
    playlist intermission is open, and whether the last heat's results are
    saved for a reinstate."""

    round_id: int | None = None
    heat: int | None = None
    racing: bool = False
    refused: int = 0
    intermission: bool = False
    last_saved: bool = False

    @property
    def kind(self) -> Kind:
        if self.round_id == TIMER_TEST_ROUND:
            return Kind.TIMER_TEST
        if self.round_id is None or self.round_id < 0 or self.heat is None:
            return Kind.NONE
        return Kind.HEAT

    @property
    def ref(self) -> HeatRef | None:
        return (self.round_id, self.heat) if self.kind is Kind.HEAT else None


type LaneRow = tuple[int, int, int, int, bool]
"""master heat, roundid, heat, lane, whether it has a result"""


@dataclass(frozen=True)
class Chart:
    """Every lane row of the event in row id order, and the held emptied
    heats (D39) as `(master heat, roundid, heat)`."""

    lanes: tuple[LaneRow, ...] = ()
    emptied: tuple[tuple[int, int, int], ...] = ()
    use_master_sched: bool = False

    def _rows(self, ref: HeatRef) -> list[bool]:
        return [done for _, r, h, _, done in self.lanes if (r, h) == ref]

    def has_lanes(self, ref: HeatRef) -> bool:
        return bool(self._rows(ref))

    def exists(self, ref: HeatRef) -> bool:
        """A heat with lanes, or a held emptied heat, which the race crew runs
        or skips by hand (D39)."""
        return self.has_lanes(ref) or any((r, h) == ref for _, r, h in self.emptied)

    def allowed(self, round_id: int | None, heat: int | None) -> bool:
        """`racing_state_allowed`: the timer test, or a heat with lanes and
        no result yet."""
        if round_id == TIMER_TEST_ROUND:
            return True
        if round_id is None or heat is None:
            return False
        done = self._rows((round_id, heat))
        return bool(done) and not any(done)

    def round_complete(self, round_id: int | None) -> bool:
        """No lane of the round lacks a result (Q11). True with no chart."""
        return all(done for _, r, _, _, done in self.lanes if r == round_id)

    def master_index(self) -> list[tuple[int, int, int, bool]]:
        index = [(n, r, h, done) for n, r, h, _, done in self.lanes]
        return index + [(n, r, h, False) for n, r, h in self.emptied]

    def with_results(self, ref: HeatRef, lanes: Mapping[int, bool]) -> Chart:
        rows = tuple(
            (n, r, h, lane, lanes.get(lane, done) if (r, h) == ref else done)
            for n, r, h, lane, done in self.lanes
        )
        return replace(self, lanes=rows)


# ----- events -----


class By(StrEnum):
    """Who turns racing on. Each adds its own steps (`_RACING_ON_EXTRAS`)."""

    MANAGER = "manager"  # heat.select now_racing=1 with no round
    ROUND_BUTTON = "round-button"  # heat.select roundid=... now_racing=1
    RERUN = "rerun"
    PLAYLIST = "playlist"  # a step's continue_racing, or playlist.start
    WAITING_STEP = "waiting-step"  # the race coordinator starts a waiting step


@dataclass(frozen=True)
class RacingOn:
    by: By


@dataclass(frozen=True)
class RacingOff:
    pass


@dataclass(frozen=True)
class Advance:
    """`advance_heat`: `first`, `next`, `prev`, `next-up` or a heat number,
    from the current heat or, given `round_id`, from that round."""

    requested: int | str
    report: bool = False
    round_id: int | None = None


@dataclass(frozen=True)
class Select:
    """`heat.select` with a round, a heat, or both: racing off, then move."""

    round_id: int | None
    heat: int | str


@dataclass(frozen=True)
class Cancel:
    """`cancel_running_round`: racing off and no current heat."""


@dataclass(frozen=True)
class TimerResult:
    """A timer `FINISHED`. `written` is each lane's has-a-result after the
    write, `posted_round` and `posted_heat` the heat the timer names."""

    all_zero: bool = False
    written: tuple[tuple[int, bool], ...] = ()
    posted_round: str | None = None
    posted_heat: str | None = None


@dataclass(frozen=True)
class ManualResult:
    """`result.write`. `expected` is the heat the race coordinator was shown
    (D19); the legacy form sends none."""

    expected: HeatRef | None = None
    written: tuple[tuple[int, bool], ...] = ()
    posted_round: str | None = None
    posted_heat: str | None = None


@dataclass(frozen=True)
class Rerun:
    target: HeatRef | None


@dataclass(frozen=True)
class Reinstate:
    pass


@dataclass(frozen=True)
class TimerTestStart:
    """`timer_test_start`: heat `heat` of the timer test becomes current and
    racing is on, with no replay command."""

    heat: int


@dataclass(frozen=True)
class TimerTestEnd:
    """`timer_test_end`: back to the heat `saved` when the test started, or
    to none, and racing off. Nothing changes outside the timer test."""

    saved: HeatRef | None


type Event = (
    RacingOn
    | RacingOff
    | Advance
    | Select
    | Cancel
    | TimerResult
    | ManualResult
    | Rerun
    | Reinstate
    | TimerTestStart
    | TimerTestEnd
)

# ----- effects, for the service to carry out in order -----


class Replay(StrEnum):
    START = "START"
    CANCEL = "CANCEL"
    REPLAY = "REPLAY"


@dataclass(frozen=True)
class SendReplay:
    command: Replay
    at: tuple[int, int | None] | None = None  # the heat a START records


@dataclass(frozen=True)
class Moved:
    """The current heat changed; `set_current_heat`, and `ClassID` from the
    round (Q2)."""

    round_id: int | None
    heat: int | None


@dataclass(frozen=True)
class WriteResults:
    ref: HeatRef


@dataclass(frozen=True)
class WriteTestResults:
    heat: int | None


@dataclass(frozen=True)
class CaptureLastHeat:
    ref: HeatRef


@dataclass(frozen=True)
class ClearResults:
    ref: HeatRef


@dataclass(frozen=True)
class RestoreLastHeat:
    pass


@dataclass(frozen=True)
class ClearLastHeat:
    pass


@dataclass(frozen=True)
class ApplyRacingScene:
    pass


@dataclass(frozen=True)
class TimerToConnected:
    """A `STAGING` or `RUNNING` timer goes back to `CONNECTED`, so its next
    message gets a new `heat-ready`."""


@dataclass(frozen=True)
class ClearTimerCurrent:
    pass


@dataclass(frozen=True)
class PlaylistHook:
    """`perform_playlist_after_round` for the round that ended. Always the
    last effect: the playlist changes the race state itself."""

    round_id: int | None


type Effect = (
    SendReplay
    | Moved
    | WriteResults
    | WriteTestResults
    | CaptureLastHeat
    | ClearResults
    | RestoreLastHeat
    | ClearLastHeat
    | ApplyRacingScene
    | TimerToConnected
    | ClearTimerCurrent
    | PlaylistHook
)


class Code(StrEnum):
    CANT_RACE = "cant_race"
    NO_SUCH_HEAT = "nosuchheat"
    NOT_RACING = "notracing"
    NO_CURRENT_ROUND = "nocurrentround"
    WRONG_HEAT = "wrong-heat"
    RESULTS_LOCKED = "results-locked"  # D32, refused by the services


@dataclass(frozen=True)
class Failure:
    code: Code
    description: str


@dataclass(frozen=True)
class Decision:
    state: State
    effects: tuple[Effect, ...]
    failure: Failure | None


# ----- steps: what a row does -----


@dataclass(frozen=True)
class Emit:
    """Emit a replay command or a plain effect. `START` records the current
    heat as the steps before it left it."""

    effect: Replay | Effect


@dataclass(frozen=True)
class SetRacing:
    on: bool


@dataclass(frozen=True)
class AddRefused:
    """refused-results plus one."""


@dataclass(frozen=True)
class ResetRefused:
    pass


@dataclass(frozen=True)
class CloseIntermission:
    pass


class Where(StrEnum):
    TARGET = "target"  # the heat the event names or navigates to
    FROM = "from"  # the round the advance starts from
    CURRENT = "current"
    NONE = "none"


@dataclass(frozen=True)
class Move:
    to: Where


@dataclass(frozen=True)
class Write:
    """Write the posted results to the current heat, as `written` says, and
    record it as the last heat."""


@dataclass(frozen=True)
class WriteTest:
    pass


@dataclass(frozen=True)
class Capture:
    """Save the target heat's results as the last heat."""


@dataclass(frozen=True)
class Clear:
    """Clear the target heat's results."""


@dataclass(frozen=True)
class Hook:
    round: Where


@dataclass(frozen=True)
class Then:
    """Decide a follow-on event. `NEXT` is `Advance("next")`."""

    event: Event


@dataclass(frozen=True)
class GoToSelected:
    """Decide `Advance` to the heat `Select` names, reporting a failure."""


@dataclass(frozen=True)
class RacingOnExtras:
    """The steps `RacingOn.by` adds (`_RACING_ON_EXTRAS`)."""


type Step = (
    Emit
    | SetRacing
    | AddRefused
    | ResetRefused
    | CloseIntermission
    | Move
    | Write
    | WriteTest
    | Capture
    | Clear
    | Hook
    | Then
    | GoToSelected
    | RacingOnExtras
)

NEXT = Then(Advance("next"))
OFF = Then(RacingOff())


# ----- guards -----


@dataclass
class Context:
    """What a guard reads: the state, the event, the chart, and for an
    advance, a rerun or a move, the target heat."""

    state: State
    event: Event
    chart: Chart
    target: tuple[int | None, int | None] | None = None
    from_round: int | None = None
    asked: tuple[int | None, int | None] | None = None


type Guard = Callable[[Context], bool]


def always(ctx: Context) -> bool:
    return True


def racing(ctx: Context) -> bool:
    return ctx.state.racing


def not_racing(ctx: Context) -> bool:
    return not ctx.state.racing


def timer_test(ctx: Context) -> bool:
    return ctx.state.kind is Kind.TIMER_TEST


def timer_test_racing(ctx: Context) -> bool:
    return timer_test(ctx) and racing(ctx)


def no_current_round(ctx: Context) -> bool:
    return ctx.state.kind is Kind.NONE


def all_zero(ctx: Context) -> bool:
    return ctx.event.all_zero


def posted_other_heat(ctx: Context) -> bool:
    """A posted non-zero `roundid` or `heat` that is not the current one."""
    event, state = ctx.event, ctx.state

    def differs(posted: str | None, current: int | None) -> bool:
        return (
            posted is not None
            and not php_equals(current, posted)
            and not php_equals(0, posted)
        )

    return differs(event.posted_round, state.round_id) or differs(
        event.posted_heat, state.heat
    )


def shown_heat_moved(ctx: Context) -> bool:
    """D19: the race coordinator saw another heat than the current one."""
    expected = ctx.event.expected
    return expected is not None and expected != (ctx.state.round_id, ctx.state.heat)


def not_allowed(ctx: Context) -> bool:
    return not ctx.chart.allowed(ctx.state.round_id, ctx.state.heat)


def by_round_button(ctx: Context) -> bool:
    return not_allowed(ctx) and ctx.event.by is By.ROUND_BUTTON


def by_manager(ctx: Context) -> bool:
    return not_allowed(ctx) and ctx.event.by is By.MANAGER


def no_target(ctx: Context) -> bool:
    return ctx.target is None


def no_target_after_round(ctx: Context) -> bool:
    """`next` found no heat, and the round it started from is complete."""
    return (
        no_target(ctx)
        and ctx.event.requested == "next"
        and ctx.chart.round_complete(ctx.from_round)
    )


def target_refused(ctx: Context) -> bool:
    """Racing is on and the new heat cannot race."""
    return racing(ctx) and not ctx.chart.allowed(*ctx.target)


def target_refused_after_round(ctx: Context) -> bool:
    """Only `next` runs the playlist hook (Q2)."""
    return (
        target_refused(ctx)
        and ctx.event.requested == "next"
        and ctx.chart.round_complete(ctx.target[0])
    )


def last_saved(ctx: Context) -> bool:
    return ctx.state.last_saved


# ----- the table -----


@dataclass(frozen=True)
class Row:
    event: type
    guard: Guard
    steps: tuple[Step, ...]
    failure: Code | None = None
    name: str = field(default="", compare=False)


ON = (SetRacing(True), ResetRefused())
_OFF = (SetRacing(False), ResetRefused())

TABLE: tuple[Row, ...] = (
    Row(
        RacingOn,
        by_round_button,
        (),
        Code.CANT_RACE,
        "racing on from a round's race button, not allowed",
    ),
    Row(
        RacingOn,
        by_manager,
        (ResetRefused(),),
        Code.CANT_RACE,
        "racing on, not allowed",
    ),
    Row(RacingOn, not_allowed, (), None, "racing on by the server, not allowed"),
    Row(
        RacingOn,
        not_racing,
        (Emit(Replay.START), *ON, RacingOnExtras()),
        None,
        "racing on",
    ),
    Row(RacingOn, always, (*ON, RacingOnExtras()), None, "racing on, already on"),
    Row(RacingOff, racing, (Emit(Replay.CANCEL), *_OFF), None, "racing off"),
    Row(RacingOff, always, _OFF, None, "racing off, already off"),
    Row(
        Select,
        always,
        (CloseIntermission(), OFF, GoToSelected()),
        None,
        "select heat",
    ),
    Row(
        Advance,
        no_target_after_round,
        (OFF, Hook(Where.FROM)),
        Code.NO_SUCH_HEAT,
        "advance next, no next heat, round complete",
    ),
    Row(Advance, no_target, (OFF,), Code.NO_SUCH_HEAT, "advance, no such heat"),
    Row(
        Advance,
        target_refused_after_round,
        (Emit(Replay.CANCEL), Move(Where.TARGET), OFF, Hook(Where.TARGET)),
        None,
        "advance next while racing, next heat not allowed, round complete",
    ),
    Row(
        Advance,
        target_refused,
        (Emit(Replay.CANCEL), Move(Where.TARGET), OFF),
        None,
        "advance while racing, next heat not allowed",
    ),
    Row(
        Advance,
        racing,
        (Emit(Replay.CANCEL), Move(Where.TARGET), Emit(Replay.START)),
        None,
        "advance while racing",
    ),
    Row(
        Advance,
        always,
        (Emit(Replay.CANCEL), Move(Where.TARGET)),
        None,
        "advance, racing off",
    ),
    Row(Cancel, always, (OFF, Move(Where.NONE)), None, "cancel the running round"),
    Row(
        TimerResult,
        not_racing,
        (AddRefused(),),
        Code.NOT_RACING,
        "timer result, racing off",
    ),
    Row(
        TimerResult,
        all_zero,
        (Emit(Replay.REPLAY), OFF),
        None,
        "all-zero timer result",
    ),
    Row(
        TimerResult,
        timer_test,
        (WriteTest(), Emit(Replay.REPLAY), Emit(ClearTimerCurrent()), NEXT),
        None,
        "timer result in a timer test",
    ),
    Row(
        TimerResult,
        no_current_round,
        (),
        Code.NO_CURRENT_ROUND,
        "timer result, no current round",
    ),
    Row(
        TimerResult,
        posted_other_heat,
        (),
        Code.WRONG_HEAT,
        "timer result for another heat",
    ),
    Row(
        TimerResult,
        always,
        (Write(), Emit(Replay.REPLAY), Emit(ClearTimerCurrent()), NEXT),
        None,
        "timer result",
    ),
    Row(
        ManualResult,
        shown_heat_moved,
        (),
        Code.WRONG_HEAT,
        "manual result for the shown heat, heat moved",
    ),
    Row(
        ManualResult,
        timer_test_racing,
        (WriteTest(), NEXT),
        None,
        "manual result in a timer test, racing",
    ),
    Row(
        ManualResult, timer_test, (WriteTest(),), None, "manual result in a timer test"
    ),
    Row(
        ManualResult,
        no_current_round,
        (),
        Code.NO_CURRENT_ROUND,
        "manual result, no current round",
    ),
    Row(
        ManualResult,
        posted_other_heat,
        (),
        Code.WRONG_HEAT,
        "manual result for another heat",
    ),
    Row(ManualResult, racing, (Write(), NEXT), None, "manual result while racing"),
    Row(ManualResult, always, (Write(),), None, "manual result"),
    Row(Rerun, no_target, (), Code.NO_SUCH_HEAT, "rerun, no such heat"),
    Row(
        Rerun,
        always,
        (
            OFF,
            Emit(Replay.CANCEL),
            Move(Where.TARGET),
            Emit(Replay.START),
            Capture(),
            Clear(),
            Then(RacingOn(By.RERUN)),
        ),
        None,
        "rerun",
    ),
    Row(
        Reinstate,
        last_saved,
        (Emit(RestoreLastHeat()), Emit(ClearLastHeat()), OFF),
        None,
        "reinstate",
    ),
    Row(Reinstate, always, (Emit(ClearLastHeat()), OFF), None, "reinstate, none saved"),
    Row(TimerTestStart, always, (Move(Where.TARGET), *ON), None, "timer test start"),
    Row(
        TimerTestEnd,
        timer_test,
        (Move(Where.TARGET), SetRacing(False)),
        None,
        "timer test end",
    ),
    Row(TimerTestEnd, always, (), None, "timer test end, not testing"),
)

_RACING_ON_EXTRAS: dict[By, tuple[Step, ...]] = {
    By.MANAGER: (CloseIntermission(), Emit(ApplyRacingScene())),
    By.ROUND_BUTTON: (CloseIntermission(), Emit(ApplyRacingScene())),
    By.RERUN: (Emit(ApplyRacingScene()), Emit(TimerToConnected())),
    By.WAITING_STEP: (CloseIntermission(), Emit(ApplyRacingScene())),
    # A step's continue_racing keeps the scene the crew chose (playlist.inc).
    By.PLAYLIST: (),
}


# ----- navigation -----


def _number(requested: int | str) -> int | None:
    if isinstance(requested, int):
        return requested
    return int(requested) if requested.lstrip("-").isdigit() else None


def ask(
    state: State, requested: int | str, round_id: int | None, chart: Chart
) -> tuple[int | None, int] | None:
    """`get_next_heat`: the heat a request names, or None. The master
    schedule applies to every request but a heat number, outside the timer
    test, and reads the master index with its held emptied heats (D39)."""
    number = _number(requested)
    if chart.use_master_sched and number is None and round_id != TIMER_TEST_ROUND:
        current = (
            (round_id, state.heat)
            if round_id is not None and state.heat is not None
            else None
        )
        return master_target(chart.master_index(), current, requested)
    heat = state.heat or 0
    if requested == "first":
        heat = 1
    elif requested in ("next", "next-up"):
        heat += 1
    elif requested == "prev":
        heat -= 1
    elif number is not None:
        heat = number
    else:
        return None
    return (round_id, heat)


def _existing(asked, chart: Chart) -> tuple[int | None, int] | None:
    """`advance_heat`'s check that the heat exists, skipped for the timer
    test."""
    if asked is None:
        return None
    round_id, heat = asked
    if round_id == TIMER_TEST_ROUND or (
        round_id is not None and chart.exists((round_id, heat))
    ):
        return asked
    return None


# ----- deciding -----


def decide(state: State, event: Event, chart: Chart) -> Decision:
    run = _Run(state, chart)
    failure = run.handle(event)
    return Decision(run.state, tuple(run.effects), failure)


def find_row(state: State, event: Event, chart: Chart) -> Row:
    """The row `decide` takes for the event."""
    return _row(_Run(state, chart).context(event))


def _row(ctx: Context) -> Row:
    """The first row for the event whose guard holds. Every event's last row
    is guarded by `always`, so one always matches."""
    return next(row for row in TABLE if row.event is type(ctx.event) and row.guard(ctx))


class _Run:
    def __init__(self, state: State, chart: Chart):
        self.state = state
        self.chart = chart
        self.effects: list[Effect] = []

    def context(self, event: Event) -> Context:
        ctx = Context(self.state, event, self.chart)
        if isinstance(event, Advance):
            ctx.from_round = (
                event.round_id if event.round_id is not None else self.state.round_id
            )
            ctx.asked = ask(self.state, event.requested, ctx.from_round, self.chart)
            ctx.target = _existing(ctx.asked, self.chart)
        elif isinstance(event, Rerun):
            if event.target is not None and self.chart.has_lanes(event.target):
                ctx.target = event.target
        elif isinstance(event, TimerTestStart):
            ctx.target = (TIMER_TEST_ROUND, event.heat)
        elif isinstance(event, TimerTestEnd):
            ctx.target = event.saved or (None, None)
        return ctx

    def handle(self, event: Event) -> Failure | None:
        ctx = self.context(event)
        row = _row(ctx)
        failure = None
        if row.failure is not None and (not isinstance(event, Advance) or event.report):
            failure = Failure(row.failure, _describe(row.failure, ctx))
        for step in row.steps:
            nested = self.step(step, ctx)
            failure = failure or nested
        return failure

    def step(self, step: Step, ctx: Context) -> Failure | None:
        state = self.state
        match step:
            case GoToSelected():
                select = ctx.event
                return self.handle(
                    Advance(select.heat, report=True, round_id=select.round_id)
                )
            case Then(event=event):
                return self.handle(event)
            case RacingOnExtras():
                for extra in _RACING_ON_EXTRAS[ctx.event.by]:
                    self.step(extra, ctx)
            case Emit(effect=Replay.START):
                self.effects.append(
                    SendReplay(Replay.START, (state.round_id, state.heat))
                )
            case Emit(effect=Replay() as command):
                self.effects.append(SendReplay(command))
            case Emit(effect=effect):
                self.effects.append(effect)
            case SetRacing(on=on):
                self.state = replace(state, racing=on)
            case AddRefused():
                self.state = replace(state, refused=state.refused + 1)
            case ResetRefused():
                self.state = replace(state, refused=0)
            case CloseIntermission():
                self.state = replace(state, intermission=False)
            case Move(to=Where.NONE):
                self.state = replace(state, round_id=None, heat=None)
                self.effects.append(Moved(None, None))
            case Move(to=Where.TARGET):
                round_id, heat = ctx.target
                self.state = replace(state, round_id=round_id, heat=heat)
                self.effects.append(Moved(round_id, heat))
            case Write():
                ref = state.ref
                self.chart = self.chart.with_results(ref, dict(ctx.event.written))
                self.effects.append(WriteResults(ref))
            case WriteTest():
                self.effects.append(WriteTestResults(state.heat))
            case Capture():
                self.effects.append(CaptureLastHeat(ctx.target))
                self.state = replace(state, last_saved=True)
            case Clear():
                lanes = {lane: False for _, _, _, lane, _ in self.chart.lanes}
                self.chart = self.chart.with_results(ctx.target, lanes)
                self.effects.append(ClearResults(ctx.target))
            case Hook(round=Where.FROM):
                self.effects.append(PlaylistHook(ctx.from_round))
            case Hook(round=Where.TARGET):
                self.effects.append(PlaylistHook(ctx.target[0]))
        return None


def _describe(code: Code, ctx: Context) -> str:
    match code:
        case Code.CANT_RACE if ctx.event.by is By.ROUND_BUTTON:
            return (
                "The first heat in this group has already been run.  "
                "Please choose a heat without results."
            )
        case Code.CANT_RACE:
            return (
                "To start racing mode, there must be a valid current heat"
                " without results."
            )
        case Code.NO_SUCH_HEAT if isinstance(ctx.event, Rerun):
            return "No such heat"
        case Code.NO_SUCH_HEAT if ctx.asked is None:
            return f"No {ctx.event.requested} heat available."
        case Code.NO_SUCH_HEAT:
            round_id, heat = ctx.asked
            # PHP's get_running_round() gives RoundID -1 with no current round.
            shown = -1 if round_id is None else round_id
            return f"No heat {heat} scheduled for roundid {shown}."
        case Code.NOT_RACING:
            return "Not racing now."
        case Code.NO_CURRENT_ROUND:
            return "No currently-racing round is available!"
        case Code.WRONG_HEAT:
            return (
                f"Expecting heat results for roundid {ctx.state.round_id}"
                f" and heat {ctx.state.heat}"
            )
