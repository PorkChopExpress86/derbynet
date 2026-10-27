import json
import random
import threading
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from core.models import RaceSettings, RaceState
from kiosks.models import Kiosk
from racing.engine.scheduling.adjuster import NodeBudget
from racing.engine.scheduling.master import master_target
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HAS_RESULT, HeatLane, RosterEntry, Round
from racing.services.master import reassign_master_heats
from racing.services.reschedule import (
    GUARD_EXPIRY,
    Failure,
    RescheduleFailure,
    _adjust,
    _snapshot,
    _write,
    reschedule,
)
from racing.services.rounds import Adjustments, adjustments
from racing.services.schedule import generate_schedule
from racing.tests.factories import RosterEntryFactory, RoundFactory
from roster.models import Racer
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db

UNLIMITED = 10**12


def new_round(car_numbers, n_times_per_lane=1, event=None, **race):
    """A scheduled round of passed racers, entered in car number order."""
    round = RoundFactory(group__event=event) if event else RoundFactory()
    race.setdefault("lane_count", 4)
    RaceSettings.objects.filter(event=round.event).update(**race)
    for car in car_numbers:
        RosterEntryFactory(
            round=round,
            racer=RacerFactory(
                group=round.group, car_number=car, passed_inspection=True
            ),
        )
    generate_schedule(round, n_times_per_lane)
    return round


def late_racer(round, car_number):
    """A racer who passes check-in after the round was scheduled."""
    return RosterEntryFactory(
        round=round,
        racer=RacerFactory(
            group=round.group, car_number=car_number, passed_inspection=True
        ),
    ).racer


def race(round, *heats):
    HeatLane.objects.filter(round=round, heat__in=heats).update(finish_time=3.0)


def make_current(round, heat, racing=True):
    RaceState.objects.filter(event=round.event).update(
        current_round=round, current_group=round.group, current_heat=heat, racing=racing
    )


def state_of(round):
    return RaceState.objects.get(event=round.event)


def by_car(round):
    """`{heat: [car number or None per lane]}` as written."""
    rows = HeatLane.objects.filter(round=round).select_related("racer")
    width = RaceSettings.objects.get(event=round.event).lane_count
    heats = {}
    for row in rows:
        heats.setdefault(row.heat, [None] * width)[row.lane - 1] = row.racer.car_number
    return dict(sorted(heats.items()))


def heat_rows(round, heat):
    return set(
        HeatLane.objects.filter(round=round, heat=heat).values_list(
            "lane", "racer_id", "finish_time"
        )
    )


def cars_of(round, chart):
    """A reply chart with racer ids replaced by car numbers."""
    car = dict(Racer.objects.filter(event=round.event).values_list("id", "car_number"))
    return [
        None
        if heat is None
        else " ".join(r if r == "-" else str(car[int(r)]) for r in heat.split())
        for heat in chart
    ]


def php_reply(fixture, exchange):
    return json.loads(read_transcript(GOLDEN_DIR / fixture)[exchange]["body"])


def failure_of(call):
    with pytest.raises(RescheduleFailure) as caught:
        call()
    return caught.value


# ----- Departed racers, squeezes and racing state (Q5) ----------------------


def q5_round():
    """`http/reschedule/dry-run-while-racing.http`: 11 racers, heat 1 run,
    heat 2 racing, and car 110 no longer passed. Car 1NN is PHP's racer NN."""
    round = new_round(range(101, 112))
    race(round, 1)
    make_current(round, 2)
    Racer.objects.filter(event=round.event, car_number=110).update(
        passed_inspection=False
    )
    return round


def test_a_dry_run_that_squeezes_a_heat_leaves_racing_on():
    round = q5_round()
    Kiosk.objects.create(event=round.event, address="kiosk-1")
    before = by_car(round)

    result = reschedule(round, dry_run=True, budget=NodeBudget(UNLIMITED))

    php = php_reply(
        "http/reschedule/dry-run-while-racing.http", "002 dry-run-squeezes-a-heat"
    )
    assert cars_of(round, result.chart) == cars_of_php(php["chart"])
    assert len(result.chart) == len(before) - 1
    assert state_of(round).racing is True
    assert by_car(round) == before
    assert not Kiosk.objects.filter(reload_requested=True).exists()
    assert state_of(round).reschedule_started_at is None


def cars_of_php(chart):
    """PHP's reply chart from a capture where racer NN has car 1NN."""
    return [
        None
        if heat is None
        else " ".join(r if r == "-" else str(100 + int(r)) for r in heat.split())
        for heat in chart
    ]


def test_the_q5_deviation_records_racing_left_on():
    expected = read_transcript(
        GOLDEN_DIR / "deviations" / "Q5" / "dry-run-while-racing.http"
    )
    for name in ("002 dry-run-squeezes-a-heat", "003 racing-after"):
        body = json.loads(expected[name]["body"])
        assert body["current-heat"]["now_racing"] is True, name
        php = php_reply("http/reschedule/dry-run-while-racing.http", name)
        assert php["current-heat"]["now_racing"] is False, name


def test_committing_a_squeeze_in_the_current_round_stops_racing():
    round = q5_round()
    Kiosk.objects.create(event=round.event, address="kiosk-1")

    dry = reschedule(round, dry_run=True, budget=NodeBudget(UNLIMITED))
    result = reschedule(round, budget=NodeBudget(UNLIMITED))

    assert result.chart == dry.chart
    assert len(by_car(round)) == len(result.chart)
    assert state_of(round).racing is False
    assert state_of(round).current_heat == 2
    assert Kiosk.objects.get().reload_requested is True


def test_a_squeeze_in_another_round_leaves_racing_on():
    round = q5_round()
    other = new_round(range(201, 206), event=round.event)
    make_current(other, 1)

    reschedule(round, budget=NodeBudget(UNLIMITED))

    assert state_of(round).racing is True


def test_a_squeeze_renumbers_raced_heats_as_php_does():
    """`http/suite/reschedule.http` 016: car 161 leaves Lions & Tigers, whose
    heat 4 has run, while another round races. Car 1NN is PHP's racer NN."""
    round = new_round(range(101, 182, 5), lane_count=4, unused_lane_mask=2)
    race(round, 4)
    raced = {(lane, car) for lane, car in enumerate(by_car(round)[4], 1)}
    RosterEntry.objects.filter(round=round, racer__car_number=161).delete()

    reschedule(round, budget=NodeBudget(UNLIMITED))

    php = php_reply("http/suite/reschedule.http", "016 schedule.reschedule")["chart"]
    written = by_car(round)
    assert [
        None
        if HeatLane.objects.filter(round=round, heat=h).filter(HAS_RESULT)
        else " ".join("-" if c is None else str(c) for c in heat)
        for h, heat in written.items()
    ] == cars_of_php(php)
    assert {(lane, car) for lane, car in enumerate(written[3], 1)} == raced
    assert set(
        HeatLane.objects.filter(round=round)
        .filter(HAS_RESULT)
        .values_list("heat", flat=True)
    ) == {3}


def test_a_racer_removed_from_the_roster_is_departed():
    round = new_round(range(101, 109))
    race(round, 1)
    RosterEntry.objects.filter(round=round, racer__car_number=105).delete()
    gone = Racer.objects.get(event=round.event, car_number=105)

    assert adjustments(round.event)[round.id].departed == (gone.id,)
    reschedule(round, budget=NodeBudget(UNLIMITED))

    unraced = HeatLane.objects.filter(round=round).exclude(HAS_RESULT)
    assert not unraced.filter(racer=gone).exists()
    assert adjustments(round.event)[round.id] == Adjustments()


# ----- Late racers (D32) and the running and staged heats (D34) -------------


def test_a_late_racer_joins_only_through_add_to_schedule():
    round = new_round(range(101, 110))
    race(round, 1, 2)
    racer = late_racer(round, 120)

    assert not HeatLane.objects.filter(racer=racer).exists()
    assert adjustments(round.event)[round.id].unscheduled == (racer.id,)

    preview = reschedule(round, racer.id, dry_run=True, budget=NodeBudget(UNLIMITED))
    assert not HeatLane.objects.filter(racer=racer).exists()
    raced = {h: heat_rows(round, h) for h in (1, 2)}

    added = reschedule(round, racer.id, budget=NodeBudget(UNLIMITED))

    assert added.chart == preview.chart
    written = by_car(round)
    assert [
        None if h in raced else " ".join("-" if c is None else str(c) for c in heat)
        for h, heat in written.items()
    ] == cars_of(round, added.chart)
    lanes = HeatLane.objects.filter(racer=racer).exclude(HAS_RESULT)
    assert sorted(lanes.values_list("lane", flat=True)) == [1, 2, 3, 4]
    assert {h: heat_rows(round, h) for h in (1, 2)} == raced
    assert adjustments(round.event)[round.id] == Adjustments()


def test_a_late_racer_never_changes_the_running_or_the_staged_heat():
    round = new_round(range(101, 111))
    race(round, 1, 2)
    make_current(round, 3)
    kept = {h: heat_rows(round, h) for h in (3, 4)}
    racer = late_racer(round, 120)

    result = reschedule(round, racer.id, budget=NodeBudget(UNLIMITED))

    assert {h: heat_rows(round, h) for h in (3, 4)} == kept
    assert state_of(round).current_heat == 3
    assert state_of(round).racing is True
    lanes = HeatLane.objects.filter(racer=racer)
    assert sorted(lanes.values_list("lane", flat=True)) == [1, 2, 3, 4]
    assert not lanes.filter(heat__in=(1, 2, 3, 4)).exists()
    assert cars_of(round, result.chart)[2:4] == [
        " ".join(str(c) for c in by_car(round)[h]) for h in (3, 4)
    ]


def test_with_no_heat_after_the_staged_heat_the_racer_gets_new_heats():
    round = new_round(range(101, 105))
    race(round, 1, 2)
    make_current(round, 3)
    kept = {h: heat_rows(round, h) for h in (3, 4)}
    racer = late_racer(round, 120)

    reschedule(round, racer.id, budget=NodeBudget(UNLIMITED))

    assert {h: heat_rows(round, h) for h in (3, 4)} == kept
    assert state_of(round).current_heat == 3
    heats = set(HeatLane.objects.filter(racer=racer).values_list("heat", flat=True))
    assert min(heats) == 5
    assert max(by_car(round)) == max(heats)


def test_no_heat_before_the_running_heat_is_squeezed_out():
    """Heats 1 to 3 were skipped. When car 104 leaves, PHP squeezes out one
    of them, which renumbers heat 4, the running heat 5 and the staged heat
    6."""
    round = new_round(range(101, 112))
    race(round, 4)
    make_current(round, 5)
    Racer.objects.filter(event=round.event, car_number=104).update(
        passed_inspection=False
    )
    before = {h: heat_rows(round, h) for h in (4, 5, 6)}

    reschedule(round, budget=NodeBudget(UNLIMITED))

    assert {h: heat_rows(round, h) for h in (4, 5, 6)} == before
    assert state_of(round).current_heat == 5


def test_under_the_master_schedule_the_next_heat_of_another_round_is_staged():
    """D39: round A's last heat is running and round B's heat 1 is next in
    master order, so B's heat 1 is staged. Car 201, who left, loses its lane
    there, every other car keeps its lane, and the late car 220 goes later."""
    round = new_round(range(101, 105), use_master_sched=True)
    other = new_round(range(201, 209), event=round.event)
    Round.objects.filter(pk=other.pk).update(number=2)
    race(round, 1, 2, 3)
    reassign_master_heats(round.event)
    make_current(round, 4)
    assert master_next(round, 4) == (other.id, 1)
    gone = Racer.objects.get(event=round.event, car_number=201)
    assert gone.id in {racer for _, racer, _ in heat_rows(other, 1)}
    Racer.objects.filter(pk=gone.pk).update(passed_inspection=False)
    staged = {row for row in heat_rows(other, 1) if row[1] != gone.id}
    late = late_racer(other, 220)

    reschedule(other, budget=NodeBudget(UNLIMITED))

    assert heat_rows(other, 1) == staged
    assert master_next(round, 4) == (other.id, 1)
    assert HeatLane.objects.filter(racer=late).exists()
    assert not HeatLane.objects.filter(racer=late, heat=1).exists()


def test_under_the_master_schedule_racing_and_the_current_heat_stay():
    """Q6, `http/reschedule/master-schedule.http`: PHP's cancel branch never
    runs. Car 1NN is PHP's racer NN."""
    round = new_round(range(101, 106), use_master_sched=True)
    race(round, 1, 2)
    make_current(round, 3)
    late_racer(round, 106)

    reschedule(round, budget=NodeBudget(UNLIMITED))

    php = php_reply("http/reschedule/master-schedule.http", "003 racing-after")[
        "current-heat"
    ]
    assert (state_of(round).current_heat, state_of(round).racing) == (
        php["heat"],
        php["now_racing"],
    )


# ----- The recheck under the lock (Q15) and the guard -----------------------


class OnSearch(NodeBudget):
    """A NodeBudget that runs `hook` when each search starts."""

    def __init__(self, hook):
        super().__init__(UNLIMITED)
        self.hook = hook

    def start(self):
        super().start()
        self.hook()


def test_a_result_written_during_the_search_is_kept():
    round = new_round(range(101, 110))
    race(round, 1)
    racer = late_racer(round, 120)
    searches = []

    def finish_heat_2():
        if not searches:
            race(round, 2)
        searches.append(1)

    reschedule(round, racer.id, budget=OnSearch(finish_heat_2))

    assert len(searches) == 2
    assert set(
        HeatLane.objects.filter(round=round)
        .filter(HAS_RESULT)
        .values_list("heat", flat=True)
    ) == {1, 2}
    assert not HeatLane.objects.filter(round=round, heat=2, racer=racer).exists()
    rows = list(HeatLane.objects.filter(round=round).values_list("heat", "racer_id"))
    assert len(rows) == len(set(rows))


def test_a_round_that_keeps_changing_fails_with_busy():
    round = new_round(range(101, 110))
    racer = late_racer(round, 120)
    heats = iter(range(1, 10))

    caught = failure_of(
        lambda: reschedule(
            round, racer.id, budget=OnSearch(lambda: race(round, next(heats)))
        )
    )

    assert caught.code == Failure.BUSY
    assert not HeatLane.objects.filter(racer=racer).exists()
    assert state_of(round).reschedule_started_at is None


def test_nothing_to_change_is_nochart_and_releases_the_guard():
    round = new_round(range(101, 109))

    assert failure_of(lambda: reschedule(round)).code == Failure.NOCHART
    assert state_of(round).reschedule_started_at is None


def test_a_recent_guard_is_busy_and_an_expired_one_is_not():
    round = q5_round()
    now = timezone.now()
    state = RaceState.objects.filter(event=round.event)

    state.update(reschedule_started_at=now - GUARD_EXPIRY + timedelta(seconds=1))
    assert failure_of(lambda: reschedule(round, dry_run=True)).code == Failure.BUSY

    state.update(reschedule_started_at=now - GUARD_EXPIRY)
    reschedule(round, dry_run=True, budget=NodeBudget(UNLIMITED), clock=lambda: now)
    assert state_of(round).reschedule_started_at is None


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_concurrent_reschedules_give_one_success_and_one_busy():
    round = q5_round()
    searching, release = threading.Event(), threading.Event()
    outcomes = {}

    def wait_in_search():
        searching.set()
        assert release.wait(30)

    def first():
        try:
            outcomes["first"] = reschedule(round, budget=OnSearch(wait_in_search))
        except Exception as e:
            outcomes["first"] = e
        finally:
            connection.close()

    racer = late_racer(round, 150)
    thread = threading.Thread(target=first)
    thread.start()
    assert searching.wait(30)
    try:
        second = failure_of(lambda: reschedule(round, racer.id, dry_run=True))
    finally:
        release.set()
        thread.join()

    assert second.code == Failure.BUSY
    assert not isinstance(outcomes["first"], Exception), outcomes["first"]
    assert HeatLane.objects.filter(racer=racer).exists()


# ----- Findings of the #24 review -------------------------------------------


def test_adding_a_racer_who_already_has_heats_is_refused():
    """A second press of Add to schedule must not fit the racer in again."""
    round = new_round(range(101, 110))
    racer = late_racer(round, 120)
    reschedule(round, racer.id, budget=NodeBudget(UNLIMITED))
    rows = by_car(round)

    caught = failure_of(lambda: reschedule(round, racer.id, budget=NodeBudget(10)))

    assert caught.code == Failure.ALREADY_SCHEDULED
    assert by_car(round) == rows


def test_under_the_master_schedule_the_staged_heat_is_next_in_master_order():
    """D39: with heat 2 of round A running, the staged heat is round B's
    heat 2, which is next in master order, not A's heat 3. Fitting a late
    racer into B leaves B's heat 2 as it was, and still next."""
    round = new_round(range(101, 104), use_master_sched=True)
    other = new_round(range(201, 204), event=round.event)
    race(round, 1)
    race(other, 1)
    make_current(round, 2)

    def unraced_in_master_order():
        rows = HeatLane.objects.filter(event=round.event).exclude(HAS_RESULT)
        return list(
            rows.order_by("master_heat").values_list("round_id", "heat").distinct()
        )

    assert unraced_in_master_order()[:3] == [
        (round.id, 2),
        (other.id, 2),
        (round.id, 3),
    ]
    staged = heat_rows(other, 2)
    late = late_racer(other, 220)

    reschedule(other, late.id, budget=NodeBudget(UNLIMITED))

    assert heat_rows(other, 2) == staged
    assert unraced_in_master_order()[:2] == [(round.id, 2), (other.id, 2)]
    assert master_next(round, 2) == (other.id, 2)
    assert not HeatLane.objects.filter(racer=late, heat__lte=2).exists()


def master_next(round, heat):
    """The heat `next` lands on from `round`'s `heat` under the master
    schedule."""
    index = HeatLane.objects.filter(event=round.event).order_by("id")
    return master_target(
        (
            (n, r, h, time is not None or place is not None)
            for n, r, h, time, place in index.values_list(
                "master_heat", "round_id", "heat", "finish_time", "finish_place"
            )
        ),
        (round.id, heat),
        "next",
    )


def master_number(round, heat):
    return HeatLane.objects.filter(round=round, heat=heat).values_list(
        "master_heat", flat=True
    )[0]


def test_under_the_master_schedule_next_follows_a_current_heat_that_has_run():
    """Round B's heat 2 is current with a result in lane 1, and `next` lands
    on round A's heat 2. Fitting a late racer into B renumbers B's unraced
    heats but not heat 2."""
    a = new_round(range(101, 109), use_master_sched=True)
    b = new_round(range(201, 213), event=a.event)
    race(a, 1)
    race(b, 1)
    HeatLane.objects.filter(round=b, heat=2, lane=1).update(finish_time=3.0)
    make_current(b, 2)
    assert master_next(b, 2) == (a.id, 2)

    reschedule(b, late_racer(b, 230).id, budget=NodeBudget(UNLIMITED))

    assert master_next(b, 2) == (a.id, 2)


def test_under_the_master_schedule_a_current_heat_that_moves_keeps_its_number():
    """Heat 4 is current with a result in lane 1 only, and round B races
    alongside. Cars 105 and 106 leave heat 2, so heat 4 becomes heat 3 and
    keeps its master number."""
    round = RoundFactory()
    RaceSettings.objects.filter(event=round.event).update(
        lane_count=2, use_master_sched=True
    )
    racers = {}
    for car in range(101, 107):
        racers[car] = RosterEntryFactory(
            round=round,
            racer=RacerFactory(
                group=round.group, car_number=car, passed_inspection=car < 105
            ),
        ).racer
    chart = {1: (101, 102), 2: (105, 106), 3: (103, 104), 4: (102, 101)}
    for heat, cars in chart.items():
        for lane, car in enumerate(cars, start=1):
            HeatLane.objects.create(
                event=round.event, round=round, heat=heat, lane=lane, racer=racers[car]
            )
    new_round(range(201, 205), event=round.event, lane_count=2)
    race(round, 1)
    HeatLane.objects.filter(round=round, heat=4, lane=1).update(finish_time=3.0)
    reassign_master_heats(round.event)
    make_current(round, 4, racing=False)
    number = master_number(round, 4)

    reschedule(round, budget=NodeBudget(UNLIMITED))

    assert state_of(round).current_heat == 3
    assert master_number(round, 3) == number


def test_a_current_heat_that_has_run_follows_its_new_number():
    """Heat 4 has run and is still current. Cars 105 and 106 leave heat 2, so
    heat 3 moves into its byes and heat 4 becomes heat 3."""
    round = RoundFactory()
    RaceSettings.objects.filter(event=round.event).update(lane_count=2)
    racers = {}
    for car in range(101, 107):
        racers[car] = RosterEntryFactory(
            round=round,
            racer=RacerFactory(
                group=round.group, car_number=car, passed_inspection=car < 105
            ),
        ).racer
    chart = {1: (101, 102), 2: (105, 106), 3: (103, 104), 4: (102, 101)}
    for heat, cars in chart.items():
        for lane, car in enumerate(cars, start=1):
            HeatLane.objects.create(
                event=round.event, round=round, heat=heat, lane=lane, racer=racers[car]
            )
    race(round, 1, 4)
    make_current(round, 4, racing=False)

    reschedule(round, budget=NodeBudget(UNLIMITED))

    assert by_car(round) == {1: [101, 102], 2: [103, 104], 3: [102, 101]}
    assert state_of(round).current_heat == 3
    assert HeatLane.objects.filter(round=round, heat=3).filter(HAS_RESULT).count() == 2


def test_a_departed_car_leaves_the_running_and_staged_heats():
    """D37: the departed cars' lanes in heats 2 and 3 run empty, and every
    other car there keeps its lane."""
    round = q5_round()
    in_running = HeatLane.objects.filter(round=round, heat=2, lane=1).get()
    in_staged = HeatLane.objects.filter(round=round, heat=3, lane=4).get()
    Racer.objects.filter(pk__in=[in_running.racer_id, in_staged.racer_id]).update(
        passed_inspection=False
    )
    gone = {in_running.racer_id, in_staged.racer_id}
    rest = {
        h: {row for row in heat_rows(round, h) if row[1] not in gone} for h in (2, 3)
    }

    result = reschedule(round, budget=NodeBudget(UNLIMITED))

    assert {h: heat_rows(round, h) for h in (2, 3)} == rest
    assert result.chart[1].split()[0] == "-"
    assert result.chart[2].split()[3] == "-"
    assert state_of(round).current_heat == 2
    assert (
        not HeatLane.objects.filter(round=round, racer_id__in=gone)
        .exclude(heat=1)
        .exists()
    )
    assert adjustments(round.event)[round.id].departed == ()


def test_a_chart_wider_than_the_track_is_refused():
    round = new_round(range(101, 109))
    RaceSettings.objects.filter(event=round.event).update(lane_count=3)
    racer = late_racer(round, 120)

    caught = failure_of(lambda: reschedule(round, racer.id, dry_run=True))

    assert caught.code == Failure.NLANES


@pytest.mark.parametrize(
    ("heat_1", "others", "code"),
    [
        ({"heat": 0}, "keep", Failure.NOCHART),
        ({"heat": 0}, "delete", Failure.NOCHART),
        ({"lane": 0}, "keep", Failure.NLANES),
    ],
    ids=["heat-0", "only-heat-0", "lane-0"],
)
def test_a_chart_with_a_heat_or_lane_0_is_refused(heat_1, others, code):
    """Only an imported database can hold one. Car 101's row in heat 1 is
    moved to heat 0 or lane 0, and the other heats are kept or deleted."""
    round = new_round(range(101, 105))
    if others == "delete":
        HeatLane.objects.filter(round=round).exclude(heat=1).delete()
    HeatLane.objects.filter(round=round, heat=1, racer__car_number=101).update(**heat_1)
    racer = late_racer(round, 130)

    caught = failure_of(
        lambda: reschedule(round, racer.id, dry_run=True, budget=NodeBudget(UNLIMITED))
    )

    assert caught.code == code


def test_the_retry_renews_the_guard():
    round = new_round(range(101, 110))
    racer = late_racer(round, 120)
    times = iter(timezone.now() + timedelta(seconds=10 * i) for i in range(1000))
    guards = []

    def finish_a_heat_once():
        guards.append(state_of(round).reschedule_started_at)
        if len(guards) == 1:
            race(round, 1)

    reschedule(
        round, racer.id, budget=OnSearch(finish_a_heat_once), clock=lambda: next(times)
    )

    assert guards[1] > guards[0]
    assert state_of(round).reschedule_started_at is None


# ----- Findings of the second #24 review ------------------------------------


def race_lane(round, heat, lane):
    HeatLane.objects.filter(round=round, heat=heat, lane=lane).update(finish_time=3.0)


def test_a_current_heat_with_a_result_in_one_lane_counts_as_raced():
    """A heat is run once one of its lanes has a result, so the partly run
    current heat stays as it is and heat 4 is the staged heat."""
    round = new_round(range(101, 111))
    race(round, 1, 2)
    race_lane(round, 3, 1)
    make_current(round, 3)
    kept = {h: heat_rows(round, h) for h in (3, 4)}
    racer = late_racer(round, 120)

    result = reschedule(round, racer.id, budget=NodeBudget(UNLIMITED))

    assert result.chart[2] is None
    assert {h: heat_rows(round, h) for h in (3, 4)} == kept
    assert not HeatLane.objects.filter(racer=racer, heat__lte=4).exists()
    assert state_of(round).current_heat == 3


def test_a_staged_heat_with_a_result_in_one_lane_is_passed_over():
    """Heat 4 has a result in lane 2, so it counts as raced and heat 5 is
    the staged heat."""
    round = new_round(range(101, 111))
    race(round, 1, 2)
    race_lane(round, 4, 2)
    make_current(round, 3)
    kept = {h: heat_rows(round, h) for h in (3, 4, 5)}
    racer = late_racer(round, 120)

    result = reschedule(round, racer.id, budget=NodeBudget(UNLIMITED))

    assert result.chart[3] is None
    assert {h: heat_rows(round, h) for h in (3, 4, 5)} == kept
    assert not HeatLane.objects.filter(racer=racer, heat__lte=5).exists()


def test_no_state_the_database_can_hold_makes_the_adjuster_raise():
    """Results in any lanes of any heats, any current heat, either schedule,
    departed and late racers: every call succeeds or fails with a
    RescheduleFailure."""
    rng = random.Random(24)
    outcomes = set()
    for case in range(40):
        round = new_round(
            range(101, 101 + rng.randint(3, 9)),
            rng.choice((1, 2)),
            lane_count=rng.choice((2, 3, 4)),
            use_master_sched=rng.random() < 0.3,
        )
        rows = list(HeatLane.objects.filter(round=round))
        for row in rng.sample(rows, rng.randint(0, len(rows))):
            row.finish_place = 1
            row.save()
        nheats = max(row.heat for row in rows)
        make_current(round, rng.randint(1, nheats + 1))
        for racer in Racer.objects.filter(event=round.event):
            if rng.random() < 0.15:
                Racer.objects.filter(pk=racer.pk).update(passed_inspection=False)
        racer = late_racer(round, 200 + case) if rng.random() < 0.7 else None
        try:
            reschedule(
                round,
                racer.id if racer else None,
                dry_run=rng.random() < 0.5,
                budget=NodeBudget(2000),
            )
            outcomes.add("success")
        except RescheduleFailure as failure:
            outcomes.add(failure.code)
    assert "success" in outcomes


def test_a_retry_whose_guard_was_taken_fails_with_busy():
    """The first search ran past the guard's expiry, and another request
    took the guard; the retry must not go on without it."""
    round = new_round(range(101, 110))
    racer = late_racer(round, 120)
    other = timezone.now() + timedelta(hours=1)
    searches = []

    def finish_a_heat_and_lose_the_guard():
        if not searches:
            race(round, 1)
            RaceState.objects.filter(event=round.event).update(
                reschedule_started_at=other
            )
        searches.append(1)

    caught = failure_of(
        lambda: reschedule(
            round, racer.id, budget=OnSearch(finish_a_heat_and_lose_the_guard)
        )
    )

    assert caught.code == Failure.BUSY
    assert len(searches) == 1
    assert not HeatLane.objects.filter(racer=racer).exists()
    assert state_of(round).reschedule_started_at == other


def test_the_write_never_deletes_a_result_the_snapshot_missed():
    """A result writer that skips the lock records heat 2 lane 1 after the
    recheck. The write may fail on the occupied lane, but keeps the result."""
    round = new_round(range(101, 110))
    race(round, 1)
    racer = late_racer(round, 120)
    snapshot = _snapshot(round)
    adjuster = _adjust(round, snapshot, racer.id, NodeBudget(UNLIMITED), timezone.now)
    race_lane(round, 2, 1)
    result = heat_rows(round, 2)

    try:
        with transaction.atomic():
            _write(round, state_of(round), snapshot, adjuster)
    except IntegrityError:
        pass

    assert {r for r in result if r[2] is not None} <= heat_rows(round, 2)


def test_a_racer_of_another_event_is_refused():
    round = new_round(range(101, 109))
    stranger = late_racer(new_round(range(201, 205)), 220)

    caught = failure_of(lambda: reschedule(round, stranger.id, dry_run=True))

    assert caught.code == Failure.NO_RACER
    assert state_of(round).reschedule_started_at is None


# ----- D39: emptied heats, and late racers off the roster ---------------------


def depart_all_of(round, heat):
    """Take every car of the heat off the roster, and return their ids."""
    gone = {racer for _, racer, _ in heat_rows(round, heat)}
    RosterEntry.objects.filter(round=round, racer_id__in=gone).delete()
    return gone


def test_an_emptied_staged_heat_keeps_its_number():
    """D39: every car of the staged heat 4 leaves. Heat 4 runs empty, and no
    later heat takes its number."""
    round = new_round(range(101, 113))
    race(round, 1, 2)
    make_current(round, 3)
    running = heat_rows(round, 3)
    gone = depart_all_of(round, 4)
    running = {row for row in running if row[1] not in gone}

    result = reschedule(round, budget=NodeBudget(UNLIMITED))

    assert result.chart[3] == "- - - -"
    assert heat_rows(round, 3) == running
    assert heat_rows(round, 4) == set()
    assert min(h for h in by_car(round) if h > 4) == 5
    assert state_of(round).current_heat == 3

    late = late_racer(round, 120)
    reschedule(round, late.id, budget=NodeBudget(UNLIMITED))

    assert heat_rows(round, 3) == running
    assert heat_rows(round, 4) == set()
    assert not HeatLane.objects.filter(racer=late, heat__lte=4).exists()


def test_an_emptied_running_heat_keeps_its_number():
    """D39: every car of the running heat 3 leaves. Heat 3 stays current with
    no cars, the staged heat 4 keeps its other cars, and a later reschedule
    fits a late racer in neither heat."""
    round = new_round(range(101, 113))
    race(round, 1, 2)
    make_current(round, 3)
    gone = depart_all_of(round, 3)
    staged = {row for row in heat_rows(round, 4) if row[1] not in gone}

    result = reschedule(round, budget=NodeBudget(UNLIMITED))

    assert result.chart[2] == "- - - -"
    assert heat_rows(round, 3) == set()
    assert heat_rows(round, 4) == staged
    assert state_of(round).current_heat == 3

    late = late_racer(round, 120)
    result = reschedule(round, late.id, budget=NodeBudget(UNLIMITED))

    assert result.chart[2] == "- - - -"
    assert heat_rows(round, 3) == set()
    assert heat_rows(round, 4) == staged
    assert state_of(round).current_heat == 3
    assert not HeatLane.objects.filter(racer=late, heat__lte=4).exists()


def test_an_emptied_last_heat_keeps_its_number_while_running():
    """D39: heat 8, the round's last, is running when all its cars leave. A
    late racer then goes into heats added after it."""
    round = new_round(range(101, 109))
    race(round, *range(1, 8))
    make_current(round, 8)
    depart_all_of(round, 8)

    reschedule(round, budget=NodeBudget(UNLIMITED))

    assert max(by_car(round)) == 7
    late = late_racer(round, 120)
    result = reschedule(round, late.id, budget=NodeBudget(UNLIMITED))

    assert result.chart[7] == "- - - -"
    assert heat_rows(round, 8) == set()
    heats = set(HeatLane.objects.filter(racer=late).values_list("heat", flat=True))
    assert min(heats) == 9
    assert state_of(round).current_heat == 8


def test_under_the_master_schedule_an_emptied_running_heat_stays_current():
    """D39: every car of round A's running heat 2 leaves. The heat stays
    current with no cars, and round B's heat 2, next in master order, stays
    next."""
    round = new_round(range(101, 109), use_master_sched=True)
    other = new_round(range(201, 209), event=round.event)
    race(round, 1)
    race(other, 1)
    reassign_master_heats(round.event)
    make_current(round, 2)
    assert master_next(round, 2) == (other.id, 2)
    number = master_number(other, 2)
    depart_all_of(round, 2)

    reschedule(round, budget=NodeBudget(UNLIMITED))

    assert heat_rows(round, 2) == set()
    assert state_of(round).current_heat == 2
    assert master_number(other, 2) == number


def test_a_racer_of_another_group_is_refused():
    """D39: a late racer is fitted only into a round whose roster has them."""
    round = new_round(range(101, 109))
    other = new_round(range(201, 205), event=round.event)
    stranger = late_racer(other, 220)
    rows = by_car(round)

    caught = failure_of(lambda: reschedule(round, stranger.id, dry_run=True))

    assert caught.code == Failure.NOT_ON_ROSTER
    assert by_car(round) == rows
    assert state_of(round).reschedule_started_at is None


def test_a_racer_not_picked_for_the_final_is_refused():
    """D39: a racer of the group who did not make the final is not fitted
    into it."""
    first = new_round(range(101, 109))
    final = RoundFactory(group=first.group, number=2)
    racers = list(Racer.objects.filter(event=first.event).order_by("car_number"))
    for racer in racers[:4]:
        RosterEntryFactory(round=final, racer=racer)
    generate_schedule(final, 1)
    rows = by_car(final)

    caught = failure_of(
        lambda: reschedule(final, racers[5].id, budget=NodeBudget(UNLIMITED))
    )

    assert caught.code == Failure.NOT_ON_ROSTER
    assert by_car(final) == rows
