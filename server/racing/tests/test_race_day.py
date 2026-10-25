"""The owner's race day (D11, D12) through the playlist and its operator
gate.

`http/race-day/` records the day on PHP: Scouts race round 1 and a top 4
final, then Outlaws race round 1 and a top 8 final, all queued in one
playlist. Finalists come from PHP's recorded round 1 ladders until #26
chooses them. Results stand in for the timer, which #27 ports.
"""

import json

import pytest
from django.utils import timezone

from core.models import RaceSettings, RaceState
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HeatLane, PlaylistEntry, Round
from racing.services.playlist import (
    Failure,
    PlaylistFailure,
    rebuild_waiting_round,
    round_ended,
    start_playlist,
    start_waiting_step,
)
from racing.tests.test_round_scenarios import Rounds

pytestmark = pytest.mark.django_db

RACE_DAY = GOLDEN_DIR / "http" / "race-day"


def recorded(fixture, exchange):
    return json.loads(read_transcript(RACE_DAY / f"{fixture}.http")[exchange]["body"])


# PHP's round 1 ladders: Scouts are round 1, Outlaws round 2.
LADDERS = recorded("outlaws", "005 standings-after-outlaws")["standings"]


def top(ladder, n):
    """The first `n` racers of a ladder, and anyone tied with the last."""
    chosen = []
    for tied in ladder:
        if len(chosen) >= n:
            break
        chosen += tied
    return chosen


def race_day(scouts_final, outlaws_final):
    """The day's roster and playlist, as `capture/scenarios/06-race-day.sh`
    sets it up."""
    replay = Rounds()
    replay.post("action=settings.write&unused-lane-mask=0&n-lanes=4")
    for i in range(1, 11):
        replay.post(
            f"action=racer.import&firstname=Scout{i}&lastname=Racer"
            f"&carnumber={100 + i}&partition=Scouts"
        )
    csv = GOLDEN_DIR.parents[2] / "testing" / "data" / "outlaw-class.csv"
    for line in csv.read_text().splitlines():
        if line.strip():
            first, last, car, partition = (f.strip('"') for f in line.split(","))
            replay.post(
                f"action=racer.import&firstname={first}&lastname={last}"
                f"&carnumber={car}&partition={partition}"
            )
    replay.post("action=racer.bulk&who=all&what=checkin")
    scouts, outlaws = replay.group("Scouts"), replay.group("Outlaw")
    for seq, (group, number, limit, runs) in enumerate(
        [
            (scouts, 1, 0, 1),
            (scouts, 2, scouts_final, 2),
            (outlaws, 1, 0, 1),
            (outlaws, 2, outlaws_final, 2),
        ],
        1,
    ):
        PlaylistEntry.objects.create(
            event=replay.event,
            seq=seq,
            group=group,
            round_number=number,
            bucket_limit=limit,
            n_times_per_lane=runs,
        )
    return replay


def finalists_from_ladders(replay):
    """The racers PHP's standings put in each final, in standings order."""

    def finalists_for(entry, round):
        first_round = Round.objects.get(group=entry.group, number=1)
        ladder = LADDERS[f"st-r{replay.php('round', first_round)}"]
        return [replay.get("racer", r) for r in top(ladder, entry.bucket_limit)]

    return finalists_for


def race(round):
    """Results on every lane, then racing off, as #27's advance leaves the
    race when it finds the round complete."""
    for lane in HeatLane.objects.filter(round=round):
        lane.finish_time = 2.5 + lane.lane / 10
        lane.completed = timezone.now()
        lane.save()
    RaceState.objects.filter(event_id=round.event_id).update(racing=False)


def state(replay):
    return RaceState.objects.get(event=replay.event)


def chart(replay, round):
    """The chart as PHP racer ids per heat, as the `schedule` poll gives it."""
    heats = {}
    for lane in HeatLane.objects.filter(round=round).order_by("heat", "lane"):
        heats.setdefault(lane.heat, []).append(replay.php("racer", lane.racer))
    return [heats[h] for h in sorted(heats)]


def rounds_of(php_reply):
    return [
        {k: v for k, v in r.items() if k not in ("adjustments", "next-round")}
        for r in php_reply["rounds"]
    ]


def assert_current(replay, php_reply):
    current, now = php_reply["current-heat"], state(replay)
    assert replay.php("round", now.current_round) == current["roundid"]
    assert now.current_heat == current["heat"]
    assert now.racing == current["now_racing"]


def test_the_recorded_day_with_a_top_4_and_a_top_8_final():
    """Each round ends in an intermission with the next round scheduled and
    its first heat selected, as PHP leaves it, and one call starts it."""
    replay = race_day(4, 8)
    finalists_for = finalists_from_ladders(replay)
    replay.ids.sync()

    round = start_playlist(replay.event, finalists_for)
    replay.ids.sync()
    assert_current(replay, recorded("playlist-queue", "006 rounds-after-start"))

    steps = [
        ("scouts", "002 rounds-between-scouts-rounds"),
        ("scouts", "004 rounds-after-scouts-final"),
        ("outlaws", "002 rounds-after-outlaws-round-1"),
    ]
    entries = list(PlaylistEntry.objects.filter(event=replay.event).order_by("seq"))
    for (fixture, exchange), waiting in zip(steps, entries[1:], strict=True):
        race(round)
        round_ended(round, finalists_for)
        replay.ids.sync()
        php = recorded(fixture, exchange)
        assert_current(replay, php)
        now = state(replay)
        assert (now.waiting_entry, now.ended_round) == (waiting, round)
        teed_up = Round.objects.get(group=waiting.group, number=waiting.round_number)
        php_rows = {(r["class"], r["round"]): r["roster_size"] for r in php["rounds"]}
        assert (
            teed_up.roster.count()
            == php_rows[(waiting.group.name, waiting.round_number)]
        )
        if "schedule" in php:
            assert chart(replay, teed_up) == php["schedule"]

        assert start_waiting_step(replay.event) == teed_up
        now = state(replay)
        assert now.racing and now.waiting_entry is None and now.ended_round is None
        assert now.current_round == teed_up and now.current_heat == 1
        round = teed_up

    race(round)
    round_ended(round, finalists_for)
    now = state(replay)
    assert not now.racing and now.waiting_entry is None
    assert replay.rounds() == rounds_of(
        recorded("outlaws", "004 rounds-after-outlaws-final")
    )


@pytest.mark.parametrize("scouts_final, outlaws_final", [(4, 8), (8, 4)])
def test_each_final_holds_the_top_racers_and_ties(scouts_final, outlaws_final):
    replay = race_day(scouts_final, outlaws_final)
    finalists_for = finalists_from_ladders(replay)
    replay.ids.sync()
    round = start_playlist(replay.event, finalists_for)
    intermissions = 0
    while True:
        race(round)
        round_ended(round, finalists_for)
        if state(replay).waiting_entry is None:
            break
        intermissions += 1
        waiting = state(replay).waiting_entry
        teed_up = Round.objects.get(group=waiting.group, number=waiting.round_number)
        if waiting.round_number == 2:
            first = Round.objects.get(group=waiting.group, number=1)
            ladder = LADDERS[f"st-r{replay.php('round', first)}"]
            php_ids = [replay.php("racer", e.racer) for e in teed_up.roster.all()]
            assert sorted(php_ids) == sorted(top(ladder, waiting.bucket_limit))
        assert not state(replay).racing
        round = start_waiting_step(replay.event)
        assert round == teed_up and state(replay).racing
    assert intermissions == 3
    assert {
        (r.group.name, r.number) for r in Round.objects.filter(event=replay.event)
    } == {
        ("Scouts", 1),
        ("Scouts", 2),
        ("Outlaw", 1),
        ("Outlaw", 2),
    }


def two_step_playlist(waits, continue_racing):
    replay = race_day(4, 8)
    entries = list(PlaylistEntry.objects.filter(event=replay.event).order_by("seq"))
    PlaylistEntry.objects.filter(pk=entries[0].pk).update(
        continue_racing=continue_racing
    )
    PlaylistEntry.objects.filter(pk=entries[1].pk).update(waits_for_operator=waits)
    replay.ids.sync()
    return replay, finalists_from_ladders(replay)


def test_a_step_that_does_not_wait_starts_when_the_round_ends():
    replay, finalists_for = two_step_playlist(waits=False, continue_racing=True)
    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    now = state(replay)
    assert now.racing and now.waiting_entry is None
    assert now.current_round.number == 2 and now.current_heat == 1


def test_a_step_that_does_not_wait_leaves_racing_off_without_continue_racing():
    """As PHP does: the next round is selected and racing stays off, with no
    intermission to end."""
    replay, finalists_for = two_step_playlist(waits=False, continue_racing=False)
    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    now = state(replay)
    assert not now.racing and now.waiting_entry is None
    assert now.current_round.number == 2


def test_a_waiting_step_ignores_continue_racing():
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=True)
    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    now = state(replay)
    assert not now.racing and now.waiting_entry is not None


def test_a_final_with_fewer_than_2_racers_keeps_the_intermission_open():
    """D28: no chart is made, and the waiting step cannot start."""
    replay, _ = two_step_playlist(waits=True, continue_racing=False)
    one = [replay.racer(car=108)]

    def finalists_for(entry, round):
        return one

    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    with pytest.raises(PlaylistFailure) as caught:
        start_waiting_step(replay.event)
    assert caught.value.code == Failure.NO_RACERS
    assert state(replay).waiting_entry is not None and not state(replay).racing


def test_start_waiting_step_without_an_intermission_fails():
    replay = race_day(4, 8)
    with pytest.raises(PlaylistFailure) as caught:
        start_waiting_step(replay.event)
    assert caught.value.code == Failure.NO_INTERMISSION


def test_a_changed_result_rebuilds_the_waiting_final_until_it_races():
    """D27."""
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=False)
    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    final = state(replay).waiting_entry
    final_round = Round.objects.get(group=final.group, number=2)
    first_four = list(final_round.roster.values_list("racer_id", flat=True))

    others = [replay.racer(car=c) for c in (101, 102, 103)]
    assert rebuild_waiting_round(replay.event, lambda entry, r: others)
    assert sorted(final_round.roster.values_list("racer_id", flat=True)) == sorted(
        r.pk for r in others
    )
    assert HeatLane.objects.filter(round=final_round).exists()
    assert first_four != [r.pk for r in others]

    race(final_round)
    assert not rebuild_waiting_round(replay.event, finalists_for)


def test_the_playlist_does_nothing_under_the_master_schedule():
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=True)
    round = start_playlist(replay.event, finalists_for)
    RaceSettings.objects.filter(event=replay.event).update(use_master_sched=True)
    race(round)
    round_ended(round, finalists_for)
    assert state(replay).waiting_entry is None
    assert not Round.objects.filter(group=round.group, number=2).exists()
