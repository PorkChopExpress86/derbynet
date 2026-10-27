import json

import pytest

from core.models import RaceSettings
from racing.engine.scheduling.round import make_chart
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HeatLane, PlaylistEntry, Round
from racing.services import schedule
from racing.services.schedule import (
    Failure,
    ScheduleFailure,
    generate_schedule,
    unschedule,
)
from racing.tests.factories import RosterEntryFactory, RoundFactory
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db


def set_race(event, **fields):
    fields.setdefault("lane_count", 4)
    RaceSettings.objects.filter(event=event).update(**fields)


def enter(round, car_numbers, passed=True):
    return [
        RosterEntryFactory(
            round=round,
            racer=RacerFactory(
                group=round.group, car_number=n, passed_inspection=passed
            ),
        ).racer
        for n in car_numbers
    ]


def enter_racers(round, racers):
    for racer in racers:
        RosterEntryFactory(round=round, racer=racer)


def new_round(car_numbers=range(101, 111), **race):
    round = RoundFactory()
    set_race(round.event, **race)
    racers = enter(round, car_numbers)
    return round, racers


def chart_of(round):
    """`{heat: {lane: racer id}}` as written."""
    chart = {}
    for row in HeatLane.objects.filter(round=round):
        chart.setdefault(row.heat, {})[row.lane] = row.racer_id
    return chart


def chart_by_car(round):
    """The chart as lists of car numbers per heat, None for an empty lane."""
    rows = HeatLane.objects.filter(round=round).select_related("racer")
    width = max(row.lane for row in rows)
    heats = {}
    for row in rows:
        heats.setdefault(row.heat, [None] * width)[row.lane - 1] = row.racer.car_number
    return [heats[h] for h in sorted(heats)]


def failure_of(call):
    with pytest.raises(ScheduleFailure) as caught:
        call()
    return caught.value


def expected_description(name, exchange):
    path = GOLDEN_DIR / "deviations" / "Q1" / f"{name}.http"
    body = json.loads(read_transcript(path)[exchange]["body"])
    return body["outcome"]["description"]


def test_writes_one_row_per_racer_with_1_based_heats_and_lanes():
    round, racers = new_round()

    assert generate_schedule(round, 1) == 10

    expected = make_chart([r.id for r in racers], 4, 1)
    assert chart_of(round) == {
        h: {lane: racer for lane, racer in enumerate(heat, 1)}
        for h, heat in enumerate(expected, 1)
    }
    assert min(chart_of(round)) == 1
    assert {lane for heat in chart_of(round).values() for lane in heat} == {1, 2, 3, 4}


def test_scouts_round_1_matches_the_php_race_day():
    """`http/race-day/playlist-queue.http` 006: Scouts 101 to 110 are racer
    ids 1 to 10 there."""
    round, _ = new_round(range(101, 111))
    generate_schedule(round, 1)
    php = read_transcript(GOLDEN_DIR / "http" / "race-day" / "playlist-queue.http")
    php_chart = json.loads(php["006 rounds-after-start"]["body"])["schedule"]
    assert chart_by_car(round) == [[100 + r for r in heat] for heat in php_chart]


def test_roster_order_is_entry_order_without_rotation():
    round, racers = new_round([105, 101, 104, 102, 103])
    generate_schedule(round, 1)
    by_entry = make_chart([r.car_number for r in racers], 4, 1)
    assert chart_by_car(round) == by_entry
    assert by_entry != make_chart(sorted(r.car_number for r in racers), 4, 1)


def test_roster_order_is_car_number_under_rotation():
    round, racers = new_round([105, 101, 104, 102, 103], rotation_schedule=True)
    generate_schedule(round, 1)
    assert chart_by_car(round) == make_chart(
        [101, 102, 103, 104, 105], 4, 1, rotation=True
    )


def test_racers_who_did_not_pass_inspection_are_left_out():
    round, _ = new_round([101, 102, 103])
    enter(round, [201, 202], passed=False)
    generate_schedule(round, 1)
    cars = {car for heat in chart_by_car(round) for car in heat}
    assert cars == {101, 102, 103, None}


def test_byes_write_no_row():
    round, _ = new_round([101, 102], lane_count=4)
    assert generate_schedule(round, 1) == 4
    assert HeatLane.objects.filter(round=round).count() == 8
    assert all(len(lanes) == 2 for lanes in chart_of(round).values())


def test_unused_lanes_are_skipped_in_lane_numbers():
    round, _ = new_round([101, 102, 103, 104], lane_count=6, unused_lane_mask=0b100001)
    generate_schedule(round, 1)
    lanes = {lane for heat in chart_of(round).values() for lane in heat}
    assert lanes == {2, 3, 4, 5}


def test_max_runs_per_car_runs_each_racer_once():
    round, _ = new_round(range(101, 106), max_runs_per_car=1)
    assert generate_schedule(round, 1) == 2
    cars = [car for heat in chart_by_car(round) for car in heat if car is not None]
    assert sorted(cars) == list(range(101, 106))


def test_no_lanes_is_nlanes():
    round, _ = new_round(lane_count=0)
    assert failure_of(lambda: generate_schedule(round, 1)).code == Failure.NLANES
    set_race(round.event, lane_count=4, unused_lane_mask=0b1111)
    assert failure_of(lambda: generate_schedule(round, 1)).code == Failure.NLANES


def test_a_round_with_results_is_results_exist():
    round, _ = new_round()
    generate_schedule(round, 1)
    HeatLane.objects.filter(round=round, heat=3, lane=2).update(finish_place=1)
    before = chart_of(round)
    failure = failure_of(lambda: generate_schedule(round, 2))
    assert failure.code == Failure.RESULTS_EXIST
    assert chart_of(round) == before


def test_one_passed_racer_is_too_few_racers():
    round, _ = new_round([101])
    enter(round, [102], passed=False)
    failure = failure_of(lambda: generate_schedule(round, 1))
    assert failure.code == Failure.TOO_FEW_RACERS
    assert failure.description == (
        "There need to be at least two racers checked in to generate a schedule."
    )


def test_beyond_the_tables_is_cant_fulfill_and_keeps_the_chart():
    round, _ = new_round(range(101, 111))
    generate_schedule(round, 6)
    before = chart_of(round)
    assert len(before) == 60

    failure = failure_of(lambda: generate_schedule(round, 7))

    assert failure.code == Failure.CANT_FULFILL
    assert chart_of(round) == before
    assert failure.description == expected_description(
        "cant-fulfill-4-lanes", "002 generate-round-3-too-many-runs"
    )


def test_seven_lanes_is_cant_fulfill_and_keeps_the_chart():
    round, _ = new_round(range(101, 121))
    generate_schedule(round, 1)
    before = chart_of(round)
    set_race(round.event, lane_count=7)

    failure = failure_of(lambda: generate_schedule(round, 1))

    assert failure.code == Failure.CANT_FULFILL
    assert chart_of(round) == before
    assert failure.description == expected_description(
        "seven-lanes", "002 generate-round-1"
    )


def test_201_racers_is_cant_fulfill():
    round, _ = new_round(range(1, 202))
    failure = failure_of(lambda: generate_schedule(round, 1))
    assert failure.code == Failure.CANT_FULFILL
    assert failure.description == expected_description(
        "more-than-200-cars", "001 generate-with-201-cars"
    )
    assert not HeatLane.objects.filter(round=round).exists()


def test_a_failed_write_keeps_the_old_chart(monkeypatch):
    round, _ = new_round()
    generate_schedule(round, 1)
    before = chart_of(round)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(HeatLane.objects, "bulk_create", broken)
    with pytest.raises(RuntimeError):
        generate_schedule(round, 2)
    assert chart_of(round) == before


def test_a_roster_change_during_the_engine_run_is_retried(monkeypatch):
    round, _ = new_round([101, 102, 103])
    real = schedule._make_chart
    calls = []

    def racer_arrives(inputs, n):
        calls.append(len(inputs.roster))
        if len(calls) == 1:
            enter(round, [104])
        return real(inputs, n)

    monkeypatch.setattr(schedule, "_make_chart", racer_arrives)
    generate_schedule(round, 1)
    assert calls == [3, 4]
    assert {car for heat in chart_by_car(round) for car in heat} == {101, 102, 103, 104}


def test_a_roster_that_keeps_changing_is_busy(monkeypatch):
    round, _ = new_round([101, 102, 103])
    generate_schedule(round, 1)
    before = chart_of(round)
    real = schedule._make_chart
    cars = iter(range(201, 300))

    def racers_keep_arriving(inputs, n):
        enter(round, [next(cars)])
        return real(inputs, n)

    monkeypatch.setattr(schedule, "_make_chart", racers_keep_arriving)
    assert failure_of(lambda: generate_schedule(round, 1)).code == Failure.BUSY
    assert chart_of(round) == before


def test_unschedule_deletes_the_chart_and_its_results():
    round, _ = new_round()
    other, _ = new_round()
    generate_schedule(round, 1)
    generate_schedule(other, 1)
    HeatLane.objects.filter(round=round, heat=1).update(finish_time=2.5)

    assert unschedule(round) == 40

    assert not HeatLane.objects.filter(round=round).exists()
    assert HeatLane.objects.filter(round=other).count() == 40


def test_both_services_run_the_chart_change_hook(monkeypatch):
    round, _ = new_round()
    seen = []
    monkeypatch.setattr(schedule, "after_chart_change", lambda r, **_: seen.append(r))
    generate_schedule(round, 1)
    unschedule(round)
    assert seen == [round, round]
    with pytest.raises(ScheduleFailure):
        generate_schedule(round, 7)
    assert seen == [round, round]


@pytest.mark.parametrize("n_times", [0, -1])
def test_fewer_than_one_run_per_lane_is_cant_fulfill_and_keeps_the_chart(n_times):
    round, _ = new_round()
    generate_schedule(round, 1)
    before = chart_of(round)

    failure = failure_of(lambda: generate_schedule(round, n_times))

    assert failure.code == Failure.CANT_FULFILL
    assert failure.description == "Choose at least one run per lane."
    assert chart_of(round) == before


def test_zero_runs_reports_the_failures_php_checks_first():
    no_lanes, _ = new_round(lane_count=0)
    assert failure_of(lambda: generate_schedule(no_lanes, 0)).code == Failure.NLANES

    raced, _ = new_round()
    generate_schedule(raced, 1)
    HeatLane.objects.filter(round=raced, heat=1, lane=1).update(finish_place=1)
    failure = failure_of(lambda: generate_schedule(raced, 0))
    assert failure.code == Failure.RESULTS_EXIST

    alone, _ = new_round([101])
    failure = failure_of(lambda: generate_schedule(alone, 0))
    assert failure.code == Failure.TOO_FEW_RACERS


@pytest.mark.parametrize("lanes", [4, 6, 8])
def test_the_owner_race_day_schedules_every_round(lanes):
    """D11 and D12: Scouts round 1 and a top-4 final, then Outlaws round 1 and
    a top-8 final that a tie grows to 9, each scheduled through
    generate_schedule with its playlist step's runs per lane, as in
    `capture/scenarios/06-race-day.sh`. An exact top-8 final without the tie
    fills 8 lanes with no byes."""
    scouts_1 = RoundFactory()
    event = scouts_1.event
    set_race(event, lane_count=lanes)
    scouts = enter(scouts_1, range(101, 111))
    outlaws_1 = RoundFactory(event=event, group__event=event)
    outlaws = enter(outlaws_1, range(201, 215))
    scouts_final = RoundFactory(event=event, group=scouts_1.group, number=2)
    enter_racers(scouts_final, [scouts[i] for i in (7, 8, 9, 0)])
    outlaws_final = RoundFactory(event=event, group=outlaws_1.group, number=2)
    enter_racers(outlaws_final, [outlaws[i] for i in (10, 5, 11, 6, 0, 12, 7, 1, 2)])
    untied_final = RoundFactory(event=event, group=outlaws_1.group, number=3)
    enter_racers(untied_final, [outlaws[i] for i in (10, 5, 11, 6, 0, 12, 7, 1)])

    steps = [
        (scouts_1, 1, 10),
        (scouts_final, 2, 4),
        (outlaws_1, 1, 14),
        (outlaws_final, 2, 9),
        (untied_final, 2, 8),
    ]
    for seq, (round, n_times, _) in enumerate(steps, start=1):
        PlaylistEntry.objects.create(
            event=event,
            seq=seq,
            group=round.group,
            round_number=round.number,
            n_times_per_lane=n_times,
        )

    for entry in PlaylistEntry.objects.filter(event=event).order_by("seq"):
        round = Round.objects.get(group=entry.group, number=entry.round_number)
        generate_schedule(round, entry.n_times_per_lane)

    for round, n_times, nracers in steps:
        padded = max(nracers, lanes)
        rows = HeatLane.objects.filter(round=round)
        assert rows.values("heat").distinct().count() == padded * n_times
        assert rows.count() == nracers * lanes * n_times
        for racer in round.roster.values_list("racer_id", flat=True):
            lanes_run = sorted(
                rows.filter(racer_id=racer).values_list("lane", flat=True)
            )
            assert lanes_run == sorted(list(range(1, lanes + 1)) * n_times)


@pytest.mark.parametrize(
    ("transcript", "exchange", "finalists"),
    [
        ("scouts", "002 rounds-between-scouts-rounds", [8, 9, 10, 1]),
        (
            "outlaws",
            "002 rounds-after-outlaws-round-1",
            [21, 16, 22, 17, 11, 23, 18, 12, 13],
        ),
    ],
)
def test_finals_match_the_php_race_day(transcript, exchange, finalists):
    """PHP enters finalists in standings order (`st-r1`, `st-r2` in the same
    transcripts). Each racer here gets its PHP id as its car number."""
    round = RoundFactory()
    set_race(round.event, lane_count=4)
    enter(round, finalists)
    generate_schedule(round, 2)
    php = read_transcript(GOLDEN_DIR / "http" / "race-day" / f"{transcript}.http")
    assert chart_by_car(round) == json.loads(php[exchange]["body"])["schedule"]
