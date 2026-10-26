"""The owner's race day (D11, D12) through the playlist and its operator
gate.

`http/race-day/` records the day on PHP: Scouts race round 1 and a top 4
final, then Outlaws race round 1 and a top 8 final, all queued in one
playlist. `playlist_finalists` chooses each final from the standings
(#26). Results stand in for the timer, which #27 ports, with the times
`capture/scenarios/06-race-day.sh` sent.
"""

import json

import pytest
from django.utils import timezone

from core.models import RaceSettings, RaceState
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HeatLane, PlaylistEntry, Round
from racing.services import schedule
from racing.services.playlist import (
    Failure,
    PlaylistFailure,
    rebuild_waiting_round,
    round_ended,
    skip_waiting_step,
    start_playlist,
    start_waiting_step,
)
from racing.services.rounds import (
    Entrants,
    delete_round_roster,
    playlist_finalists,
    prepare_new_round,
)
from racing.services.schedule import generate_schedule, unschedule
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


# The salt `race_round` in `06-race-day.sh` races each round with, and the
# times its timer sends for the first heat of the day.
SALT = {("Scouts", 1): 0, ("Scouts", 2): 7, ("Outlaw", 1): 3, ("Outlaw", 2): 5}
FIRST_HEAT = {1: 2.411, 2: 2.522, 3: 2.633, 4: 2.744}


def race(round):
    """Results on every lane, then racing off, as #27's advance leaves the
    race when it finds the round complete. A time depends on car number,
    lane and the round's salt, as `race_round` makes it."""
    key = (round.group.name, round.number)
    for lane in HeatLane.objects.filter(round=round).select_related("racer"):
        if key == ("Scouts", 1) and lane.heat == 1:
            lane.finish_time = FIRST_HEAT[lane.lane]
        else:
            car, salt = lane.racer.car_number, SALT[key]
            lane.finish_time = (2000 + (car * 37 + lane.lane * 11 + salt) % 1000) / 1000
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
    finalists_for = playlist_finalists()
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
    finalists_for = playlist_finalists()
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
    return replay, playlist_finalists()


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
    finalists_for = one_finalist(replay)

    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    with pytest.raises(PlaylistFailure) as caught:
        start_waiting_step(replay.event)
    assert caught.value.code == schedule.Failure.TOO_FEW_RACERS
    assert caught.value.description == "The final needs at least 2 racers."
    assert state(replay).waiting_entry is not None and not state(replay).racing


@pytest.mark.parametrize(
    "remove, description",
    [
        ("delete the round", "The waiting round has no racers in its roster."),
        ("clear the roster", "The final needs at least 2 racers."),
    ],
)
def test_a_final_with_no_racers_says_why(remove, description):
    """D28 covers a final with zero racers. A round deleted during the
    intermission is remade empty, which is not a final short of racers."""
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=False)
    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    final = Round.objects.get(group=round.group, number=2)
    unschedule(final)
    if remove == "delete the round":
        delete_round_roster(final)
    else:
        prepare_new_round(final.group, final.number)
    with pytest.raises(PlaylistFailure) as caught:
        start_waiting_step(replay.event)
    assert caught.value.code == schedule.Failure.TOO_FEW_RACERS
    assert caught.value.description == description
    assert state(replay).waiting_entry is not None and not state(replay).racing
    assert Round.objects.filter(group=round.group, number=2).exists() == (
        remove == "clear the roster"
    )


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
    assert rebuild_waiting_round(replay.event, lambda entry, r: Entrants(tuple(others)))
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


def test_starting_the_playlist_closes_an_open_intermission():
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=False)
    round = start_playlist(replay.event, finalists_for)
    race(round)
    round_ended(round, finalists_for)
    assert state(replay).waiting_entry is not None
    start_playlist(replay.event, finalists_for)
    now = state(replay)
    assert now.racing and now.waiting_entry is None and now.ended_round is None


def test_the_intermission_names_the_round_that_ended_when_a_raced_step_is_skipped():
    """The next step's round is already raced, so the step after it waits.
    `ended_round` is still the round that just ended."""
    replay = race_day(4, 8)
    finalists_for = playlist_finalists()
    replay.ids.sync()
    scouts = start_playlist(replay.event, finalists_for)
    outlaws = Round.objects.get(group__name="Outlaw", number=1)
    generate_schedule(outlaws, 1)
    race(outlaws)
    PlaylistEntry.objects.filter(event=replay.event, seq=2).update(seq=5)
    race(scouts)
    round_ended(scouts, finalists_for)
    now = state(replay)
    assert (
        now.waiting_entry.round_number == 2 and now.waiting_entry.group.name == "Outlaw"
    )
    assert now.ended_round == scouts


def test_a_changed_result_leaves_a_waiting_round_of_another_group_alone():
    """D27 rebuilds a final from the round that fed it. Outlaw round 1 waits
    after the Scouts final, and no Scouts result picks its racers."""
    replay = race_day(4, 8)
    finalists_for = playlist_finalists()
    replay.ids.sync()
    scouts = start_playlist(replay.event, finalists_for)
    race(scouts)
    round_ended(scouts, finalists_for)
    final = start_waiting_step(replay.event)
    race(final)
    round_ended(final, finalists_for)
    outlaws = Round.objects.get(group__name="Outlaw", number=1)
    assert state(replay).waiting_entry.group == outlaws.group
    racers, heats = outlaws.roster.count(), chart(replay, outlaws)
    assert not rebuild_waiting_round(replay.event, finalists_for)
    assert (outlaws.roster.count(), chart(replay, outlaws)) == (racers, heats)
    assert racers == 14 and heats


def one_finalist(replay):
    one = [replay.racer(car=108)]
    return lambda entry, round: Entrants(tuple(one))


def test_skipping_a_final_with_fewer_than_2_racers_tees_up_the_next_step():
    """D28: the coordinator skips the final and the next step waits."""
    replay, _ = two_step_playlist(waits=True, continue_racing=False)
    finalists_for = one_finalist(replay)
    scouts = start_playlist(replay.event, finalists_for)
    race(scouts)
    round_ended(scouts, finalists_for)
    outlaws = skip_waiting_step(replay.event, finalists_for)
    now = state(replay)
    assert outlaws.group.name == "Outlaw" and outlaws.number == 1
    assert (now.waiting_entry.group, now.waiting_entry.round_number) == (
        outlaws.group,
        1,
    )
    assert now.ended_round == scouts and not now.racing
    assert now.current_round == outlaws and now.current_heat == 1
    assert start_waiting_step(replay.event) == outlaws and state(replay).racing


def test_skipping_the_last_step_finishes_the_playlist():
    replay, _ = two_step_playlist(waits=True, continue_racing=False)
    PlaylistEntry.objects.filter(event=replay.event, seq__gt=2).delete()
    finalists_for = one_finalist(replay)
    scouts = start_playlist(replay.event, finalists_for)
    race(scouts)
    round_ended(scouts, finalists_for)
    assert skip_waiting_step(replay.event, finalists_for) is None
    now = state(replay)
    assert now.waiting_entry is None and now.ended_round is None
    assert not now.racing


def test_skipping_into_a_step_that_does_not_wait_starts_it():
    replay, _ = two_step_playlist(waits=True, continue_racing=False)
    PlaylistEntry.objects.filter(event=replay.event, seq=3).update(
        waits_for_operator=False
    )
    finalists_for = one_finalist(replay)
    scouts = start_playlist(replay.event, finalists_for)
    race(scouts)
    round_ended(scouts, finalists_for)
    outlaws = skip_waiting_step(replay.event, finalists_for)
    now = state(replay)
    assert now.waiting_entry is None and now.racing
    assert now.current_round == outlaws


def test_skip_waiting_step_without_an_intermission_fails():
    replay = race_day(4, 8)
    with pytest.raises(PlaylistFailure) as caught:
        skip_waiting_step(replay.event, playlist_finalists())
    assert caught.value.code == Failure.NO_INTERMISSION


@pytest.mark.parametrize(
    "change, code, description",
    [
        ("no runs", schedule.Failure.CANT_FULFILL, "Choose at least one run per lane."),
        (
            "too many runs",
            schedule.Failure.CANT_FULFILL,
            "Cannot schedule 50 runs per lane for 4 racers on 4 lanes; the most is",
        ),
        (
            "no lanes",
            schedule.Failure.NLANES,
            "Number of lanes hasn't been determined.",
        ),
        ("masked", schedule.Failure.NLANES, "Number of lanes hasn't been determined."),
    ],
)
def test_a_waiting_step_with_no_chart_says_why(change, code, description):
    """`start_waiting_step` raises the failure `generate_schedule` raises."""
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=False)
    scouts = start_playlist(replay.event, finalists_for)
    final = PlaylistEntry.objects.filter(event=replay.event, seq=2)
    settings = RaceSettings.objects.filter(event=replay.event)
    if change == "no runs":
        final.update(n_times_per_lane=0)
    elif change == "too many runs":
        final.update(n_times_per_lane=50)
    elif change == "no lanes":
        settings.update(lane_count=0)
    else:
        settings.update(unused_lane_mask=0b1111)
    race(scouts)
    round_ended(scouts, finalists_for)
    with pytest.raises(PlaylistFailure) as caught:
        start_waiting_step(replay.event)
    assert caught.value.code == code
    assert caught.value.description.startswith(description)
    assert state(replay).waiting_entry is not None and not state(replay).racing


def test_an_extra_racer_joins_only_the_final_they_were_allowed_into():
    """D32: extras belong to one step's round, so two finals in one playlist
    keep their own."""
    replay = race_day(4, 8)
    scouts, outlaws = replay.group("Scouts"), replay.group("Outlaw")
    extra = replay.racer(car=110)
    finalists_for = playlist_finalists(extras={(scouts.pk, 2): [extra]})
    replay.ids.sync()
    round = start_playlist(replay.event, finalists_for)
    while True:
        race(round)
        round_ended(round, finalists_for)
        if state(replay).waiting_entry is None:
            break
        round = start_waiting_step(replay.event)
    finals = {
        group: set(
            Round.objects.get(group=group, number=2).roster.values_list(
                "racer_id", flat=True
            )
        )
        for group in (scouts, outlaws)
    }
    assert extra.pk in finals[scouts]
    assert extra.pk not in finals[outlaws]
