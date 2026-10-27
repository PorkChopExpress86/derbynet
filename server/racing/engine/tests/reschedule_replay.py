"""`testing/test-reschedule.sh` replayed through the engine, with no database.

The PHP transcript `http/suite/reschedule.http` records each
`schedule.reschedule` reply, but not the chart each one started from. This
module rebuilds that chart: it keeps the script's roster, check-ins, charts,
results and current heat as plain data, schedules rounds with the ported
generator, and applies each committed reschedule, as the PHP server did.

The script runs against a server: `engine(False)` runs the adjuster on
PHP's inputs, where only heats with a result stay fixed, and must give PHP's
chart. `engine(True)` also keeps the running heat and the staged heat, as the
reschedule service does (D34), and starts from the charts it committed
itself. `racing/services/tests/test_reschedule_replay.py` runs the same
script against the database and the real service, so this copy of the
service's rules cannot drift from it unnoticed.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import NamedTuple

from racing.engine.scheduling.adjuster import NodeBudget, ScheduleAdjuster
from racing.engine.scheduling.adjuster import n_runs_per_lane as runs_per_lane
from racing.engine.scheduling.lanes import count_one_bits
from racing.engine.scheduling.round import make_chart
from racing.engine.tests.golden import GOLDEN_DIR

ROSTER_SCRIPT = GOLDEN_DIR.parents[2] / "testing" / "import-roster.sh"
SUITE_FIXTURE = "http/suite/reschedule.http"
QUIRK_FIXTURES = [
    "http/reschedule/dry-run-while-racing.http",
    "http/reschedule/master-schedule.http",
    "http/reschedule/last-added-heat.http",
]

# Classes are made in the order their first racer is imported: Lions & Tigers
# (1xx), White's Wolves (2xx), Bears and Freres (3xx), Webelos (5xx), Arrows
# (4xx). Each class has one round, with the class's id.
ROUND_OF_HUNDREDS = {1: 1, 2: 2, 3: 3, 5: 4, 4: 5}
LIONS, WHITE, ARROWS = 1, 2, 5

UNLIMITED = 10**12
"""A NodeBudget no fixture search reaches, so each search ends as PHP's did:
on a perfect score or with every proposal tried."""


class Reply(NamedTuple):
    """The reply's `chart`, None for PHP's `nochart`, the keys of its
    `timing.internal`, and the heats the call kept (D34) as they must stand
    after it, departed racers' lanes empty (D37), `{heat number: chart
    string}`. `failure` is the code of a request Django refuses before any
    search, where PHP has no refusal (Q25)."""

    chart: list[str | None] | None
    timing_keys: list[str]
    kept: dict[int, str]
    failure: str | None = None

    def django(self) -> dict | None:
        """The reply in `django_reply` form, or `{"failure": code}`."""
        if self.failure:
            return {"failure": self.failure}
        return django_reply(self.chart, self.timing_keys)


def django_reply(chart, timing_keys) -> dict | None:
    """The part of a `schedule.reschedule` reply the reschedule service
    computes, None for `nochart`: the chart and the `timing.internal` keys,
    their values as the capture normalizes them."""
    if chart is None:
        return None
    internal = {
        key: True if key.endswith("-timed-out") else "<seconds>" for key in timing_keys
    }
    return {"chart": chart, "timing": {"internal": internal}}


def heat_text(heat):
    return " ".join("-" if r is None else str(r) for r in heat)


def php_changed_a_kept_heat(php_chart, kept):
    """Whether PHP's reply chart changed, moved or dropped a heat Django
    keeps (D34), or answered `nochart` for a call that kept some."""
    return any(
        php_chart is None or len(php_chart) < number or php_chart[number - 1] != before
        for number, before in kept.items()
    )


@dataclass
class Round:
    chart: list[list[int | None]] = field(default_factory=list)
    raced: set[int] = field(default_factory=set)  # heat numbers with a result


@dataclass
class Server:
    """The PHP server's state as `test-reschedule.sh` drives it."""

    keep_staged_heats: bool
    round_of: dict[int, int]  # racer -> round
    mask: int = 0
    master: bool = False  # one round, so master order is heat order here
    lane_count: int = 4
    passed: set[int] = field(default_factory=set)
    rounds: dict[int, Round] = field(default_factory=dict)
    current: tuple[int, int] | None = None
    racing: bool = False

    def roster(self, roundid):
        """Racer ids in the round's roster, in roster row order. A racer moved
        by `racer.edit` keeps its row, and rows were made in racer id order.
        `round_of` puts each racer on one roster only, as the scripts have no
        final, so a racer on a first round and a final is not modeled."""
        return sorted(r for r, rid in self.round_of.items() if rid == roundid)

    def import_racer(self, roundid):
        racerid = max(self.round_of) + 1
        self.round_of[racerid] = roundid
        return racerid

    def check_in_class(self, roundid):
        self.passed |= set(self.roster(roundid))

    def generate(self, roundid, n_times_per_lane=1):
        roster = [r for r in self.roster(roundid) if r in self.passed]
        nlanes = self.lane_count - count_one_bits(self.mask)
        chart = make_chart(roster, nlanes, n_times_per_lane, unused_lane_mask=self.mask)
        self.rounds[roundid] = Round(
            [heat + [None] * (self.lane_count - len(heat)) for heat in chart]
        )

    def unschedule(self, roundid):
        self.rounds[roundid] = Round()

    def select(self, roundid, heat=1):
        self.current = (roundid, heat)
        self.racing = True

    def run_heat(self, roundid, heat):
        """A timer result for the current heat, then PHP's advance to the
        next heat of the round, or racing off after the last one."""
        assert self.racing and self.current == (roundid, heat), (self.current, heat)
        round = self.rounds[roundid]
        round.raced.add(heat)
        if heat < len(round.chart):
            self.current = (roundid, heat + 1)
        else:
            self.racing = False

    def kept_heats(self, roundid):
        """The unraced heat numbers the reschedule keeps: the running heat and
        the staged heat after it, when they are in this round."""
        if not self.keep_staged_heats or self.current is None:
            return set()
        current_round, running = self.current
        if current_round != roundid:
            return set()
        round = self.rounds[roundid]
        unraced = [h for h in range(1, len(round.chart) + 1) if h not in round.raced]
        later = [h for h in unraced if h > running]
        return ({running} & set(unraced)) | set(later[:1])

    def reschedule(self, roundid, racerid=None, dry_run=False):
        """The adjuster's reply, as a `Reply`. Django refuses a racer who is
        not on the round's roster (D39, Q25)."""
        if self.keep_staged_heats and racerid is not None:
            if self.round_of[racerid] != roundid:
                return Reply(None, [], {}, "notonroster")
        round = self.rounds[roundid]
        kept = self.kept_heats(roundid)
        on_chart = {r for heat in round.chart for r in heat if r is not None}
        in_unraced = {
            r
            for h, heat in enumerate(round.chart, start=1)
            if h not in round.raced
            for r in heat
            if r is not None
        }
        roster = self.roster(roundid)
        adjuster = ScheduleAdjuster(
            round.chart,
            {h - 1: h for h in round.raced},
            self.mask,
            self.lane_count,
            runs_per_lane(round.chart),
            NodeBudget(UNLIMITED),
            kept={h - 1 for h in kept},
        )
        departed = sorted(in_unraced - {r for r in roster if r in self.passed})
        # A departed racer's lane in a kept heat runs empty (D37).
        before = {
            h: heat_text([None if r in departed else r for r in round.chart[h - 1]])
            for h in sorted(kept)
        }
        adjuster.remove_departed(departed)
        if racerid is not None:
            adjuster.inject(racerid)
        else:
            for late in roster:
                if late in self.passed and late not in on_chart:
                    adjuster.inject(late)
        adjuster.squeeze_out_byes()
        chart = adjuster.updated_chart()
        if chart is None:
            return Reply(None, adjuster.timing_keys, before)
        debug = adjuster.chart_debug()
        if not dry_run:
            self._commit(
                roundid, chart, adjuster.renumbering_map(), adjuster.kept_heats()
            )
            if adjuster.heats_squeezed_out and self.current[0] == roundid:
                self.racing = False
        return Reply(debug, adjuster.timing_keys, before)

    def _commit(self, roundid, chart, renumbering, kept):
        old = self.rounds[roundid]
        new = Round(raced={renumbering.get(h, h) for h in old.raced})
        for index, heat in enumerate(chart):
            if heat is None:
                number = index + 1
                source = next(
                    h for h in old.raced | set(kept) if renumbering.get(h, h) == number
                )
                heat = kept.get(source, old.chart[source - 1])
            new.chart.append(heat)
        # A heat is its rows, so heats of byes at the end are not written.
        while new.chart and not any(new.chart[-1]):
            new.chart.pop()
        self.rounds[roundid] = new


def imported_roster() -> dict[int, int]:
    """`{racer id: round}` after `testing/import-roster.sh`."""
    lines = [
        line
        for line in ROSTER_SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("#") and "action=racer.import" in line
    ]
    cars = [int(re.search(r"carnumber=(\d+)", line)[1]) for line in lines]
    round_of = {}
    # The last racer imported is deleted again, and SQLite reuses its id.
    for racerid, car in enumerate(cars[:-1], start=1):
        assert car % 100 == racerid, (car, racerid)
        round_of[racerid] = ROUND_OF_HUNDREDS[car // 100]
    return round_of


type NewServer = Callable[..., Server]
"""`new_server(round_of, mask=0, master=False)`: a server with the roster
`{racer id: round}`, every racer not passed, and no round scheduled."""


def engine(keep_staged_heats: bool) -> NewServer:
    def new_server(round_of, mask=0, master=False):
        return Server(keep_staged_heats, dict(round_of), mask, master)

    return new_server


def replay(new_server: NewServer) -> Iterator[tuple[str, Reply]]:
    """Yield `(exchange, Reply)` for each reschedule that
    `http/suite/reschedule.http` holds, in transcript order."""
    seq = iter(range(1, 1000))

    def name():
        return f"{next(seq):03d} schedule.reschedule"

    s = new_server(imported_roster())
    s.passed |= {5, 15, 25, 35}
    s.generate(ARROWS)
    s.select(ARROWS)
    s.run_heat(ARROWS, 1)
    s.passed.add(80)
    yield name(), s.reschedule(ARROWS)
    s.passed.discard(80)
    yield name(), s.reschedule(ARROWS)
    s.passed.add(80)
    yield name(), s.reschedule(ARROWS)

    for n_times_per_lane in (1, 2):
        s = new_server(imported_roster()) if n_times_per_lane == 1 else s
        s.mask = 2
        s.passed |= {1, 11}
        for added, heats in ((None, 1), (16, 1), (21, 4)):
            if added:
                s.unschedule(LIONS)
                s.passed.add(added)
            s.generate(LIONS, n_times_per_lane)
            s.select(LIONS)
            for heat in range(1, heats + 1):
                s.run_heat(LIONS, heat)
                yield name(), s.reschedule(LIONS, 6, dry_run=True)
        s.unschedule(LIONS)
        s.passed -= {21, 16}

    s.check_in_class(LIONS)
    s.check_in_class(WHITE)
    s.generate(LIONS)
    s.generate(WHITE)
    s.select(LIONS, 4)
    s.run_heat(LIONS, 4)
    s.select(WHITE, 6)
    s.run_heat(WHITE, 6)
    for mover, to in ((61, WHITE), (62, LIONS), (41, WHITE)):
        s.round_of[mover] = to
        yield name(), s.reschedule(LIONS)
        yield name(), s.reschedule(WHITE)

    s.unschedule(LIONS)
    s.unschedule(WHITE)
    s.mask = 0
    s.check_in_class(WHITE)
    s.generate(WHITE)
    s.select(WHITE)
    for heat in range(1, 18):
        s.run_heat(WHITE, heat)
        yield name(), s.reschedule(WHITE, 6, dry_run=True)

    s.unschedule(WHITE)
    s.generate(WHITE)
    s.select(WHITE)
    s.run_heat(WHITE, 1)
    for _ in range(6):
        yield name(), s.reschedule(WHITE, s.import_racer(WHITE))


def replay_quirks(new_server: NewServer) -> Iterator[tuple[tuple[str, str], Reply]]:
    """Yield `((fixture, exchange), Reply)` for each reschedule
    of `capture/scenarios/11-reschedule-quirks.sh`. Every case starts from an
    empty database, so racer ids count from 1 in import order."""
    dry_run_fixture, master_fixture, last_heat_fixture = QUIRK_FIXTURES

    s = new_server(dict.fromkeys(range(1, 12), 1))
    s.passed |= set(s.round_of)
    s.generate(1)
    s.select(1)
    s.run_heat(1, 1)
    s.passed.discard(10)
    yield (
        (dry_run_fixture, "002 dry-run-squeezes-a-heat"),
        s.reschedule(1, dry_run=True),
    )

    s = new_server(dict.fromkeys(range(1, 7), 1), master=True)
    s.passed |= set(range(1, 6))
    s.generate(1)
    s.select(1)
    s.run_heat(1, 1)
    s.run_heat(1, 2)
    s.passed.add(6)
    yield (master_fixture, "002 late-racer-in-the-running-round"), s.reschedule(1)

    s = new_server({1: 1, 2: 1, 3: 2, 4: 2, 5: 1}, mask=2)
    s.passed |= {1, 2, 3, 4}
    s.generate(1, 2)
    s.generate(2)
    s.select(1)
    for heat in (1, 2, 3):
        s.run_heat(1, heat)
    s.select(2)
    yield (
        (last_heat_fixture, "002 dry-run-runs-out-of-added-heats"),
        s.reschedule(1, 5, dry_run=True),
    )
