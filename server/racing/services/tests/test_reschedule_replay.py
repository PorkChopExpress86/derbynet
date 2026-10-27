"""The reschedule scripts of `reschedule_replay`, run against the database
and the real reschedule service.

The engine parity test checks a copy of the service's rules: which heats it
keeps (D34) and how it writes the new chart. Here the same scripts drive the
service itself, so each reply must equal what PHP recorded or what a
deviation entry records, and the copy cannot drift from the service.
"""

import pytest

from core.models import RaceSettings, RaceState
from racing.engine.scheduling.adjuster import NodeBudget
from racing.engine.tests.reschedule_replay import (
    SUITE_FIXTURE,
    UNLIMITED,
    Reply,
    replay,
    replay_quirks,
)
from racing.engine.tests.test_reschedule_parity import PHP, expected_django_reply
from racing.models import HeatLane, RosterEntry
from racing.services.reschedule import Failure, RescheduleFailure, reschedule
from racing.services.schedule import generate_schedule, unschedule
from racing.tests.factories import RoundFactory
from roster.models import Racer
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db


class Database:
    """The script's server: PHP racer and round ids mapped to rows. The
    script sets `passed`, `round_of` and `mask` directly, so each step first
    writes them to the database."""

    def __init__(self, round_of, mask=0, master=False):
        self.round_of = dict(round_of)
        self.passed: set[int] = set()
        self.mask = mask
        first = RoundFactory()
        self.event = first.event
        RaceSettings.objects.filter(event=self.event).update(
            lane_count=4, use_master_sched=master
        )
        self.rounds = {}
        for roundid in sorted(set(self.round_of.values())):
            self.rounds[roundid] = (
                first if not self.rounds else RoundFactory(group__event=self.event)
            )
        self.racers: dict[int, Racer] = {}
        for racerid in sorted(self.round_of):
            self._add(racerid)

    def _add(self, racerid):
        round = self.rounds[self.round_of[racerid]]
        racer = RacerFactory(group=round.group, car_number=racerid)
        RosterEntry.objects.create(event=self.event, round=round, racer=racer)
        self.racers[racerid] = racer

    def _sync(self):
        RaceSettings.objects.filter(event=self.event).update(unused_lane_mask=self.mask)
        for racerid, racer in self.racers.items():
            Racer.objects.filter(pk=racer.pk).update(
                passed_inspection=racerid in self.passed
            )
            RosterEntry.objects.filter(racer=racer).update(
                round=self.rounds[self.round_of[racerid]]
            )

    def import_racer(self, roundid):
        racerid = max(self.round_of) + 1
        self.round_of[racerid] = roundid
        self._add(racerid)
        return racerid

    def check_in_class(self, roundid):
        self.passed |= {r for r, rid in self.round_of.items() if rid == roundid}

    def generate(self, roundid, n_times_per_lane=1):
        self._sync()
        generate_schedule(self.rounds[roundid], n_times_per_lane)

    def unschedule(self, roundid):
        unschedule(self.rounds[roundid])

    def select(self, roundid, heat=1):
        round = self.rounds[roundid]
        RaceState.objects.filter(event=self.event).update(
            current_round=round, current_group=round.group, current_heat=heat
        )
        RaceState.objects.filter(event=self.event).update(racing=True)

    def run_heat(self, roundid, heat):
        """A result for the current heat, then PHP's advance."""
        round = self.rounds[roundid]
        state = RaceState.objects.get(event=self.event)
        assert state.racing and (state.current_round_id, state.current_heat) == (
            round.id,
            heat,
        )
        rows = HeatLane.objects.filter(round=round)
        rows.filter(heat=heat).update(finish_time=3.0)
        if rows.filter(heat__gt=heat).exists():
            state.current_heat = heat + 1
        else:
            state.racing = False
        state.save(update_fields=["current_heat", "racing"])

    def reschedule(self, roundid, racerid=None, dry_run=False):
        self._sync()
        php_id = {racer.id: r for r, racer in self.racers.items()}
        racer = self.racers[racerid].id if racerid is not None else None
        try:
            result = reschedule(
                self.rounds[roundid], racer, dry_run, budget=NodeBudget(UNLIMITED)
            )
        except RescheduleFailure as failure:
            if failure.code == Failure.NOT_ON_ROSTER:
                return Reply(None, [], {}, str(failure.code))
            assert failure.code == Failure.NOCHART, failure
            return Reply(None, [], {})
        chart = [
            None
            if heat is None
            else " ".join(r if r == "-" else str(php_id[int(r)]) for r in heat.split())
            for heat in result.chart
        ]
        return Reply(chart, result.timing_keys, {})


def test_the_service_gives_each_recorded_reply_or_its_deviation():
    replies = [((SUITE_FIXTURE, name), r) for name, r in replay(Database)]
    replies += replay_quirks(Database)

    assert [key for key, _ in replies] == list(PHP)
    for key, reply in replies:
        expected = expected_django_reply(*key)
        assert reply.django() == expected, key
