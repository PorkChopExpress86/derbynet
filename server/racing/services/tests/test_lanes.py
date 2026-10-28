"""Closing and reopening a damaged lane (#108, D36, D37)."""

from unittest import mock

import pytest
from django.utils import timezone

from core.models import RaceSettings, RaceState
from racing.engine.scheduling.adjuster import ScheduleAdjuster
from racing.engine.standings.scoring import Scoring
from racing.models import (
    HAS_RESULT,
    EmptiedHeat,
    HeatLane,
    LaneClosure,
    RosterEntry,
    Round,
)
from racing.services.heats import select_heat
from racing.services.lanes import (
    Failure,
    LanesFailure,
    close_lanes,
    poll_lanes,
    reopen_lanes,
)
from racing.services.master import master_index
from racing.services.reschedule import reschedule
from racing.services.results import record_timer_result
from racing.services.schedule import generate_schedule
from racing.services.standings import standings_snapshot
from racing.services.tests.race import fields, setup_race
from roster.models import Racer

pytestmark = pytest.mark.django_db


# ----- Helpers ---------------------------------------------------------------


def time_of(heat, lane):
    return round(3.0 + lane / 10 + heat / 100, 3)


def run_heats(event, round, *heats):
    """Select and race each heat through the timer, lane L of heat H in
    3.L + H/100 seconds."""
    for heat in heats:
        assert select_heat(event, round_id=round.id, heat=heat, now_racing=True) is None
        timer(event, round, heat)


def timer(event, round, heat, nlanes=4):
    body = "&".join(
        f"lane{lane}={time_of(heat, lane)}" for lane in range(1, nlanes + 1)
    )
    recorded = record_timer_result(
        event, fields(f"{body}&roundid={round.id}&heat={heat}")
    )
    assert recorded.outcome == "recorded", recorded


def make_current(event, round, heat):
    assert select_heat(event, round_id=round.id, heat=heat, now_racing=True) is None


def chart(round):
    """`{heat: {lane: racer id}}`."""
    heats = {}
    for heat, lane, racer in HeatLane.objects.filter(round=round).values_list(
        "heat", "lane", "racer_id"
    ):
        heats.setdefault(heat, {})[lane] = racer
    return dict(sorted(heats.items()))


def mask(event):
    return RaceSettings.objects.get(event=event).unused_lane_mask


def set_settings(event, **values):
    RaceSettings.objects.filter(event=event).update(**values)


def scores(event, round):
    """`{racer id: (heats counted, score)}` of the round in the standings."""
    return {
        row.racer.id: (row.summary.base, row.summary.score)
        for row in standings_snapshot(event).standings.rows
        if row.round.id == round.id
    }


def averages(round, closed=()):
    """What every racer's average is with the closed lanes' times left out."""
    times = {}
    for racer, lane, time in (
        HeatLane.objects.filter(round=round)
        .filter(HAS_RESULT)
        .values_list("racer_id", "lane", "finish_time")
    ):
        if lane not in closed:
            times.setdefault(racer, []).append(time)
    return {r: (len(t), pytest.approx(sum(t) / len(t))) for r, t in times.items()}


def new_group(replay, name, racers=6, first_car=201):
    """Another partition's round 1, its racers checked in, not scheduled."""
    for car in range(first_car, first_car + racers):
        replay.post(
            f"action=racer.import&firstname=F{car}&lastname=L{car}"
            f"&carnumber={car}&partition={name}"
        )
    replay.post("action=racer.bulk&who=all&what=checkin")
    return Round.objects.get(event=replay.event, group__name=name, number=1)


def lanes_used(round):
    return set(HeatLane.objects.filter(round=round).values_list("lane", flat=True))


def failure_of(call):
    with pytest.raises(LanesFailure) as caught:
        call()
    return caught.value.code


@pytest.fixture
def race():
    """4 lanes, 6 racers, 6 heats, heats 1 and 2 raced, heat 3 running and
    heat 4 staged."""
    replay, round = setup_race(racers=6, lanes=4)
    run_heats(replay.event, round, 1, 2)
    make_current(replay.event, round, 3)
    return replay, round


# ----- Closing a lane during a round -----------------------------------------


def test_closing_a_lane_keeps_the_running_and_staged_heats_and_drops_its_times(race):
    replay, round = race
    event = replay.event
    before = chart(round)

    close_lanes(event, {3}, scoring=Scoring.AVERAGE)

    after = chart(round)
    assert {h: after[h] for h in (1, 2, 3, 4)} == {h: before[h] for h in (1, 2, 3, 4)}
    assert all(3 not in after[h] for h in after if h > 4)
    assert len(after) == len(before)
    assert mask(event) == 0b0100
    timer(event, round, 3)
    assert set(
        HeatLane.objects.filter(round=round, lane=3)
        .filter(HAS_RESULT)
        .values_list("heat", flat=True)
    ) == {1, 2, 3}
    assert scores(event, round) == averages(round, closed={3})
    assert Round.objects.get(pk=round.pk).scoring == Scoring.AVERAGE
    assert RaceSettings.objects.get(event=event).scoring == Scoring.AVERAGE


def test_every_racer_still_runs_once_in_each_open_lane(race):
    replay, round = race
    event = replay.event
    close_lanes(event, {3})
    lanes = {}
    for racer, lane in HeatLane.objects.filter(round=round).values_list(
        "racer_id", "lane"
    ):
        if lane != 3:
            lanes.setdefault(racer, []).append(lane)
    assert all(sorted(seen) == [1, 2, 4] for seen in lanes.values())
    assert len(lanes) == 6


def test_a_finished_round_keeps_its_scoring(race):
    """Round 1 of the Wolves finished under drop the slowest. Closing a lane
    in the Bears' round with average scoring leaves the Wolves' standings
    as they were and scores the Bears by the average."""
    replay, wolves = race
    event = replay.event
    run_heats(event, wolves, 3, 4, 5, 6)
    set_settings(event, scoring=Scoring.DROP_SLOWEST)
    finished = scores(event, wolves)
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    run_heats(event, bears, 1, 2)
    make_current(event, bears, 3)

    close_lanes(event, {3}, scoring=Scoring.AVERAGE)
    run_heats(event, bears, *range(3, 1 + max(chart(bears))))

    assert scores(event, wolves) == finished
    assert Round.objects.get(pk=wolves.pk).scoring == Scoring.DROP_SLOWEST
    assert scores(event, bears) == averages(bears, closed={3})


def test_the_next_rounds_use_only_the_open_lanes_and_the_chosen_scoring(race):
    replay, wolves = race
    event = replay.event
    set_settings(event, scoring=Scoring.DROP_SLOWEST)
    close_lanes(event, {3}, scoring=Scoring.AVERAGE)
    run_heats(event, wolves, *range(3, 1 + max(chart(wolves))))

    for name, first_car in (("Bears", 201), ("Cubs", 301)):
        later = new_group(replay, name, first_car=first_car)
        generate_schedule(later, 1)
        assert lanes_used(later) == {1, 2, 4}
        run_heats(event, later, *chart(later))
        assert Round.objects.get(pk=later.pk).scoring is None
        assert scores(event, later) == averages(later)


def test_closing_between_rounds_changes_only_the_next_rounds(race):
    replay, wolves = race
    event = replay.event
    run_heats(event, wolves, 3, 4, 5, 6)
    finished = chart(wolves)

    close_lanes(event, {2})

    assert chart(wolves) == finished
    assert scores(event, wolves) == averages(wolves)
    assert LaneClosure.objects.get(event=event).round is None
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    assert lanes_used(bears) == {1, 3, 4}


# ----- Reopening -------------------------------------------------------------


def test_reopening_before_the_next_round_is_scheduled(race):
    replay, wolves = race
    event = replay.event
    close_lanes(event, {3})
    reopen_lanes(event, {3})

    assert mask(event) == 0
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    assert lanes_used(bears) == {1, 2, 3, 4}


def test_reopening_after_the_next_round_is_scheduled_is_refused(race):
    replay, wolves = race
    event = replay.event
    close_lanes(event, {3})
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    closure = LaneClosure.objects.values().get(event=event)

    assert failure_of(lambda: reopen_lanes(event, {3})) == Failure.ROUND_SCHEDULED
    assert mask(event) == 0b0100
    assert LaneClosure.objects.values().get(event=event) == closure


def test_the_closing_round_keeps_its_chart_and_dropped_times_after_a_reopen():
    """Lane 3 closes in heat 4 of 6 and reopens before the next round (D37)."""
    replay, round = setup_race(racers=6, lanes=4)
    event = replay.event
    run_heats(event, round, 1, 2, 3)
    make_current(event, round, 4)
    close_lanes(event, {3})
    closed = chart(round)

    reopen_lanes(event, {3})
    run_heats(event, round, *range(4, 1 + max(closed)))

    assert chart(round) == closed
    assert scores(event, round) == averages(round, closed={3})
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    assert 3 in lanes_used(bears)


def test_a_reopened_lane_stays_closed_for_the_rest_of_its_round(race):
    """A late racer fitted in after the reopen gets no run in lane 3."""
    replay, round = race
    event = replay.event
    close_lanes(event, {3})
    reopen_lanes(event, {3})
    late = Racer.objects.create(
        event=event,
        group=round.group,
        subgroup=Racer.objects.filter(event=event).first().subgroup,
        partition=Racer.objects.filter(event=event).first().partition,
        car_number=150,
        first_name="Late",
        last_name="Racer",
        passed_inspection=True,
    )
    RosterEntry.objects.create(event=event, round=round, racer=late)

    reschedule(round, late.id)
    assert set(
        HeatLane.objects.filter(round=round, racer=late).values_list("lane", flat=True)
    ) == {1, 2, 4}


# ----- An abbreviated round and points ----------------------------------------


def abbreviated(event, round, rows):
    """Replace the round's chart: `rows` is one list of car numbers by lane
    per heat."""
    car = dict(Racer.objects.filter(event=event).values_list("car_number", "id"))
    HeatLane.objects.filter(round=round).delete()
    HeatLane.objects.bulk_create(
        HeatLane(event=event, round=round, heat=h, lane=lane, racer_id=car[c])
        for h, cars in enumerate(rows, start=1)
        for lane, c in enumerate(cars, start=1)
        if c is not None
    )


def test_a_car_whose_only_run_was_on_the_closed_lane_gets_one_new_run():
    """Car 107's only run is on lane 3. Car 108 also ran on lane 2 (D37)."""
    replay, round = setup_race(racers=8, lanes=4)
    event = replay.event
    set_settings(event, max_runs_per_car=2)
    abbreviated(
        event,
        round,
        [
            [101, 108, 107, 102],
            [103, 104, 108, 105],
            [106, 101, 102, 103],
            [104, 105, 106, None],
            [102, 103, 101, 104],
            [105, None, 106, 101],
        ],
    )
    run_heats(event, round, 1, 2)
    make_current(event, round, 3)
    ids = dict(Racer.objects.filter(event=event).values_list("car_number", "id"))
    heats_of = {
        car: HeatLane.objects.filter(round=round, racer_id=ids[car]).count()
        for car in (107, 108)
    }

    close_lanes(event, {3})

    new = HeatLane.objects.filter(round=round, racer_id=ids[107], heat__gt=2)
    assert new.count() == 1
    assert new.get().lane != 3
    assert (
        HeatLane.objects.filter(round=round, racer_id=ids[108]).count()
        == (heats_of[108])
    )


def test_under_points_the_places_after_a_dropped_lane_move_up():
    """Lane 3 placed first, lane 1 second and lane 2 third."""
    replay, round = setup_race(racers=4, lanes=4)
    event = replay.event
    set_settings(event, use_points=True)
    make_current(event, round, 1)
    recorded = record_timer_result(event, fields("place1=2&place2=3&place3=1&place4=4"))
    assert recorded.outcome == "recorded"
    in_lane = {
        lane: racer
        for lane, racer in HeatLane.objects.filter(round=round, heat=1).values_list(
            "lane", "racer_id"
        )
    }

    close_lanes(event, {3})

    best = {
        row.racer.id: row.summary.best
        for row in standings_snapshot(event).standings.rows
        if row.round.id == round.id
    }
    assert best == {in_lane[1]: 1, in_lane[2]: 2, in_lane[4]: 3}


# ----- The recheck under the lock (Q15) ---------------------------------------


def test_a_timer_result_during_the_closure_makes_it_retry(race):
    replay, round = race
    event = replay.event
    real = ScheduleAdjuster.close_lanes
    calls = []

    def result_for_heat_3_first(adjuster, closing):
        if not calls:
            timer(event, round, 3)
        calls.append(closing)
        real(adjuster, closing)

    before = chart(round)
    with mock.patch.object(ScheduleAdjuster, "close_lanes", result_for_heat_3_first):
        close_lanes(event, {3})

    assert len(calls) == 2
    after = chart(round)
    assert after[3] == before[3]
    assert HeatLane.objects.filter(round=round, heat=3).exclude(HAS_RESULT).count() == 0
    # Heat 4 is running and heat 5 staged once heat 3 has a result.
    assert {h: after[h] for h in (4, 5)} == {h: before[h] for h in (4, 5)}
    assert all(3 not in after[h] for h in after if h > 5)


# ----- Refusals and the poll ---------------------------------------------------


@pytest.mark.parametrize(
    ("lanes", "code"),
    [
        (set(), Failure.NO_LANES),
        ({5}, Failure.NO_SUCH_LANE),
        ({0}, Failure.NO_SUCH_LANE),
        ({1, 2, 3}, Failure.TOO_FEW_LANES),
    ],
)
def test_a_closure_that_cannot_be_made_changes_nothing(race, lanes, code):
    replay, round = race
    event = replay.event
    before = chart(round)
    assert failure_of(lambda: close_lanes(event, lanes)) == code
    assert chart(round) == before
    assert mask(event) == 0
    assert not LaneClosure.objects.exists()


def test_closing_a_closed_lane_again_is_refused(race):
    replay, round = race
    event = replay.event
    close_lanes(event, {3})
    assert failure_of(lambda: close_lanes(event, {3})) == Failure.ALREADY_CLOSED
    assert failure_of(lambda: close_lanes(event, {3}, scoring=7)) == (
        Failure.NO_SUCH_SCORING
    )


def test_reopening_a_lane_that_is_not_closed_is_refused(race):
    replay, round = race
    event = replay.event
    set_settings(event, unused_lane_mask=0b1000)
    assert failure_of(lambda: reopen_lanes(event, {4})) == Failure.NOT_CLOSED
    assert mask(event) == 0b1000


def test_the_poll_lists_the_closed_lanes_and_the_scoring_in_force(race):
    replay, round = race
    event = replay.event
    assert poll_lanes(event) == {"closed-lanes": [], "scoring": 0}

    close_lanes(event, {3}, scoring=Scoring.FASTEST)

    assert poll_lanes(event) == {
        "closed-lanes": [{"lane": 3, "roundids": [round.id], "reopen": True}],
        "scoring": 2,
    }
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    assert poll_lanes(event)["closed-lanes"] == [
        {"lane": 3, "roundids": [round.id], "reopen": False}
    ]


def test_a_reopened_lane_leaves_the_poll(race):
    replay, round = race
    event = replay.event
    close_lanes(event, {2, 3})
    reopen_lanes(event, {3})
    assert poll_lanes(event)["closed-lanes"] == [
        {"lane": 2, "roundids": [round.id], "reopen": True}
    ]
    assert mask(event) == 0b0010
    assert scores(event, round) == averages(round, closed={2, 3})


# ----- Every round with a heat left to run (D45) -----------------------------


def closed_rounds(event):
    return set(
        LaneClosure.objects.filter(event=event).values_list("round_id", flat=True)
    )


def once_in_each_open_lane(round, open_lanes=(1, 2, 4)):
    lanes = {}
    for racer, lane in HeatLane.objects.filter(round=round).values_list(
        "racer_id", "lane"
    ):
        if lane in open_lanes:
            lanes.setdefault(racer, []).append(lane)
    return all(sorted(seen) == list(open_lanes) for seen in lanes.values())


def test_a_round_scheduled_but_not_started_is_rescheduled_on_the_open_lanes(race):
    replay, wolves = race
    event = replay.event
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    set_settings(event, scoring=Scoring.DROP_SLOWEST)

    close_lanes(event, {3}, scoring=Scoring.AVERAGE)

    assert 3 not in lanes_used(bears)
    assert once_in_each_open_lane(bears)
    # A round with no result has no time to drop, so it gets no closure.
    assert closed_rounds(event) == {wolves.id}
    assert Round.objects.get(pk=bears.pk).scoring is None
    run_heats(event, bears, *chart(bears))
    assert scores(event, bears) == averages(bears)


def test_under_the_master_schedule_every_interleaved_round_closes_the_lane():
    replay, wolves = setup_race(racers=6, lanes=4)
    event = replay.event
    bears = new_group(replay, "Bears")
    set_settings(event, use_master_sched=True)
    generate_schedule(bears, 1)
    generate_schedule(wolves, 1)
    order = sorted({(n, r, h) for n, r, h, _ in master_index(event)})
    rounds = {wolves.id: wolves, bears.id: bears}
    for _, r, h in order[:3]:
        run_heats(event, rounds[r], h)
    assert {r for _, r, _ in order[:3]} == {wolves.id, bears.id}
    _, r, h = order[3]
    make_current(event, rounds[r], h)
    held = {(r, h) for _, r, h in order[3:5]}

    close_lanes(event, {3})

    assert closed_rounds(event) == {wolves.id, bears.id}
    for round in (wolves, bears):
        later = {
            heat: lanes
            for heat, lanes in chart(round).items()
            if (round.id, heat) not in held
            and not HeatLane.objects.filter(round=round, heat=heat)
            .filter(HAS_RESULT)
            .exists()
        }
        assert all(3 not in lanes for lanes in later.values()), round
        assert once_in_each_open_lane(round), round
    for round in (wolves, bears):
        assert scores(event, round) == averages(round, closed={3}), round


def test_a_round_whose_last_heat_is_a_held_emptied_heat_drops_the_lanes_times():
    replay, round = setup_race(racers=6, lanes=4)
    event = replay.event
    run_heats(event, round, 1, 2, 3, 4, 5)
    HeatLane.objects.filter(round=round, heat=6).delete()
    EmptiedHeat.objects.create(event=event, round=round, heat=6)
    RaceState.objects.filter(event=event).update(current_heat=6)

    close_lanes(event, {3})

    assert closed_rounds(event) == {round.id}
    assert scores(event, round) == averages(round, closed={3})


def test_a_closure_with_only_the_running_and_staged_heats_left_changes_no_heat():
    replay, round = setup_race(racers=6, lanes=4)
    event = replay.event
    run_heats(event, round, 1, 2, 3, 4)
    make_current(event, round, 5)
    before = chart(round)

    close_lanes(event, {3})

    assert chart(round) == before
    assert closed_rounds(event) == {round.id}
    assert mask(event) == 0b0100
    run_heats(event, round, 5, 6)
    assert scores(event, round) == averages(round, closed={3})


def test_a_round_scheduled_during_the_closure_is_closed_too(race):
    """Bears' round is scheduled, on all 4 lanes, after the closure read
    which rounds to close and before it locks; the recheck under the lock
    finds it."""
    replay, wolves = race
    event = replay.event
    bears = new_group(replay, "Bears")
    real = ScheduleAdjuster.close_lanes
    calls = []

    def schedule_the_bears_first(adjuster, closing):
        if not calls:
            generate_schedule(bears, 1)
        calls.append(closing)
        real(adjuster, closing)

    with mock.patch.object(ScheduleAdjuster, "close_lanes", schedule_the_bears_first):
        close_lanes(event, {3})

    assert len(calls) == 3  # Wolves, then Wolves and Bears again
    assert 3 not in lanes_used(bears)
    assert closed_rounds(event) == {wolves.id}


def test_a_car_whose_only_run_to_come_is_on_the_closed_lane_gets_one_new_run():
    """Car 107 has not raced, and its one run is heat 6 lane 3."""
    replay, round = setup_race(racers=8, lanes=4)
    event = replay.event
    set_settings(event, max_runs_per_car=2)
    abbreviated(
        event,
        round,
        [
            [101, 108, 102, 103],
            [104, 105, 106, 108],
            [102, 101, 103, 104],
            [105, 106, None, 101],
            [103, 102, 104, 105],
            [106, None, 107, None],
        ],
    )
    run_heats(event, round, 1, 2)
    make_current(event, round, 3)
    car = dict(Racer.objects.filter(event=event).values_list("car_number", "id"))

    close_lanes(event, {3})

    lanes = list(
        HeatLane.objects.filter(round=round, racer_id=car[107]).values_list(
            "lane", flat=True
        )
    )
    assert len(lanes) == 1
    assert lanes[0] != 3


def test_under_points_a_heat_with_a_closed_lane_scores_the_open_lanes():
    """On 4 lanes with lane 3 closed, first place is worth 3 points (D37)."""
    replay, round = setup_race(racers=4, lanes=4)
    event = replay.event
    set_settings(event, use_points=True)
    make_current(event, round, 1)
    record_timer_result(event, fields("place1=2&place2=3&place3=1&place4=4"))
    first = HeatLane.objects.get(round=round, heat=1, lane=1).racer_id

    close_lanes(event, {3})

    points = {
        row.racer.id: row.summary.avg
        for row in standings_snapshot(event).standings.rows
        if row.round.id == round.id
    }
    assert points[first] == 3


def test_reopening_a_lane_the_track_does_not_have_is_refused(race):
    replay, round = race
    event = replay.event
    close_lanes(event, {3})
    assert failure_of(lambda: reopen_lanes(event, {0})) == Failure.NO_SUCH_LANE
    assert failure_of(lambda: reopen_lanes(event, {9})) == Failure.NO_SUCH_LANE
    assert mask(event) == 0b0100


def test_under_the_master_schedule_the_current_and_staged_heats_keep_their_numbers():
    """Two rounds rewritten in one closure. The current heat, Wolves heat 3,
    has a result in one lane. A Wolves racer has left the roster, so once
    lane 4 closes the closure squeezes out a heat before heat 3, which moves
    down; the staged heat is in the Bears' round. Both keep their master
    numbers, so `next` still goes from one to the other."""
    replay, wolves = setup_race(racers=6, lanes=4)
    event = replay.event
    bears = new_group(replay, "Bears", racers=6)
    set_settings(event, use_master_sched=True)
    generate_schedule(wolves, 1)
    generate_schedule(bears, 1)
    lane = min(
        HeatLane.objects.filter(round=wolves, heat=3).values_list("lane", flat=True)
    )
    HeatLane.objects.filter(round=wolves, heat=3, lane=lane).update(
        finish_time=3.1, completed=timezone.now()
    )
    RaceState.objects.filter(event=event).update(
        current_round=wolves, current_group=wolves.group, current_heat=3
    )
    RosterEntry.objects.filter(round=wolves).order_by("racer_id")[1].delete()
    number = {(r, h): n for n, r, h, _ in master_index(event)}
    staged = min(
        (n, (r, h)) for (r, h), n in number.items() if n > number[(wolves.id, 3)]
    )[1]
    assert staged[0] == bears.id
    current_racers = set(
        HeatLane.objects.filter(round=wolves, heat=3).values_list("racer_id", "lane")
    )

    close_lanes(event, {4})

    moved = RaceState.objects.get(event=event).current_heat
    assert moved < 3
    assert (
        set(
            HeatLane.objects.filter(round=wolves, heat=moved).values_list(
                "racer_id", "lane"
            )
        )
        == current_racers
    )
    after = {(r, h): n for n, r, h, _ in master_index(event)}
    assert after[(wolves.id, moved)] == number[(wolves.id, 3)]
    assert after[staged] == number[staged]


# ----- A round with no result when the lane reopens ---------------------------


def late_racer(round, car_number):
    model = Racer.objects.filter(event=round.event, group=round.group).first()
    racer = Racer.objects.create(
        event=round.event,
        group=round.group,
        subgroup=model.subgroup,
        partition=model.partition,
        car_number=car_number,
        first_name="Late",
        last_name=f"Racer{car_number}",
        passed_inspection=True,
    )
    RosterEntry.objects.create(event=round.event, round=round, racer=racer)
    return racer


def test_a_round_regenerated_after_a_reopen_counts_the_reopened_lane(race):
    """The Bears' round was scheduled but not started when lane 3 closed.
    Once lane 3 reopens and the Bears are scheduled again, their lane 3
    times count."""
    replay, wolves = race
    event = replay.event
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    close_lanes(event, {3})
    reopen_lanes(event, {3})

    generate_schedule(bears, 1)
    assert 3 in lanes_used(bears)
    run_heats(event, bears, *chart(bears))

    assert scores(event, bears) == averages(bears)
    assert all(base == 4 for base, _ in scores(event, bears).values())


def test_a_late_racer_fitted_in_after_a_reopen_can_use_the_lane(race):
    replay, wolves = race
    event = replay.event
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    close_lanes(event, {3})
    reopen_lanes(event, {3})
    late = late_racer(bears, 250)

    reschedule(bears, late.id)

    assert 3 in set(
        HeatLane.objects.filter(round=bears, racer=late).values_list("lane", flat=True)
    )
    assert closed_rounds(event) == {wolves.id}


def test_under_points_a_round_scheduled_after_a_closure_scores_its_lanes(race):
    """Bears are scheduled on lanes 1, 2 and 4 after lane 3 closed, so first
    place is worth 3 points, as in the round that closed it."""
    replay, wolves = race
    event = replay.event
    set_settings(event, use_points=True)
    close_lanes(event, {3})
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    make_current(event, bears, 1)
    record_timer_result(event, fields("place1=1&place2=2&place4=3"))
    first = HeatLane.objects.get(round=bears, heat=1, lane=1).racer_id

    points = {
        row.racer.id: row.summary.avg
        for row in standings_snapshot(event).standings.rows
        if row.round.id == bears.id
    }
    assert points[first] == 3


def test_a_new_chart_keeps_only_the_closures_of_lanes_still_closed(race):
    """Lane 3 is opened on the settings page, not reopened, and the Bears'
    round, closed while not started, is scheduled again."""
    replay, wolves = race
    event = replay.event
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    close_lanes(event, {3})
    set_settings(event, unused_lane_mask=0)

    generate_schedule(bears, 1)
    run_heats(event, bears, *chart(bears))

    assert scores(event, bears) == averages(bears)
    assert closed_rounds(event) == {wolves.id}


def test_a_round_racing_its_first_heat_keeps_its_closure_after_a_reopen():
    """Heat 1 is running and heat 2 staged, with no time yet. The round was
    racing when lane 3 closed, so the reopen leaves its closure, and the
    lane 3 times of its kept heats stay dropped."""
    replay, round = setup_race(racers=6, lanes=4)
    event = replay.event
    make_current(event, round, 1)
    close_lanes(event, {3})
    reopen_lanes(event, {3})

    run_heats(event, round, *chart(round))

    assert closed_rounds(event) == {round.id}
    assert scores(event, round) == averages(round, closed={3})


def test_the_poll_after_a_lane_is_opened_on_the_settings_page(race):
    """The Wolves were racing when lane 3 closed and the Bears only
    scheduled. Once the settings page opens lane 3 again, lane 3 is closed
    only for the Wolves' remaining heats, the Bears' late racer can use it,
    and once the Wolves finish no lane is closed."""
    replay, wolves = race
    event = replay.event
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    close_lanes(event, {3})
    set_settings(event, unused_lane_mask=0)

    assert poll_lanes(event)["closed-lanes"] == [
        {"lane": 3, "roundids": [wolves.id], "reopen": True}
    ]
    late = late_racer(bears, 250)
    reschedule(bears, late.id)
    assert 3 in set(
        HeatLane.objects.filter(round=bears, racer=late).values_list("lane", flat=True)
    )
    run_heats(event, wolves, *range(3, 1 + max(chart(wolves))))
    assert poll_lanes(event)["closed-lanes"] == []


def bears_raced_on_a_lane_opened_again(replay, wolves):
    """The Bears were only scheduled when lane 3 closed during the Wolves'
    round. The Wolves finish, the settings page opens lane 3, a late racer
    is fitted into the Bears on all 4 lanes, and the Bears race."""
    event = replay.event
    bears = new_group(replay, "Bears")
    generate_schedule(bears, 1)
    close_lanes(event, {3})
    run_heats(event, wolves, *range(3, 1 + max(chart(wolves))))
    set_settings(event, unused_lane_mask=0)
    late = late_racer(bears, 250)
    reschedule(bears, late.id)
    assert lanes_of(bears, late) == {1, 2, 3, 4}
    run_heats(event, bears, *chart(bears))
    assert scores(event, bears)[late.id][0] == 4
    return bears, late


def lanes_of(round, racer):
    return set(
        HeatLane.objects.filter(round=round, racer=racer).values_list("lane", flat=True)
    )


def test_a_later_closure_never_drops_times_raced_while_the_lane_was_open(race):
    replay, wolves = race
    event = replay.event
    bears, late = bears_raced_on_a_lane_opened_again(replay, wolves)

    close_lanes(event, {3})

    assert scores(event, bears) == averages(bears)
    assert scores(event, bears)[late.id][0] == 4


def test_closing_the_lane_again_on_the_settings_page_drops_no_time(race):
    replay, wolves = race
    event = replay.event
    bears, late = bears_raced_on_a_lane_opened_again(replay, wolves)

    set_settings(event, unused_lane_mask=0b0100)

    assert scores(event, bears) == averages(bears)
    assert scores(event, wolves) == averages(wolves, closed={3})
