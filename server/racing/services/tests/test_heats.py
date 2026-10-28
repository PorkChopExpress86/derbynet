import json
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import RaceSettings
from kiosks.models import Kiosk, Scene
from racing.engine.heat_state import Code
from racing.engine.results import DID_NOT_FINISH
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import EmptiedHeat, HeatLane, PlaylistEntry, Round
from racing.services import playlist
from racing.services.heats import (
    LastHeat,
    advance,
    cancel_running_round,
    load_chart,
    reinstate,
    rerun,
    select_heat,
)
from racing.services.master import master_index
from racing.services.results import (
    CURRENT,
    Outcome,
    delete_results,
    record_manual_result,
    record_timer_result,
)
from racing.services.schedule import generate_schedule, unschedule
from racing.services.standings import standings_snapshot
from racing.services.tests.race import (
    connect_replay,
    current,
    drain,
    fields,
    lanes,
    setup_race,
    state,
)
from roster.models import Group

pytestmark = pytest.mark.django_db

# The times of `testing/test-basic-racing.sh`'s heat 2 of White's Wolves,
# the heat `http/suite/basic-racing.http` reruns and reinstates.
REINSTATED = "lane1=2.9945&lane2=3.4571&lane3=2.1867&lane4=2.3447"


@pytest.fixture
def race():
    replay, round = setup_race()
    select_heat(replay.event, round_id=round.id, now_racing=True)
    return replay.event, round


def finish(event, body):
    return record_timer_result(event, fields(body))


def test_rerun_and_reinstate_bring_back_times_and_places_in_time_racing(race):
    """Q4: PHP brings back only the times, as the deviation records."""
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    finish(event, REINSTATED)
    before = lanes(round, 2)
    assert [place for _, place in before.values()] == [3, 4, 1, 2]

    assert rerun(event, "last") is None
    assert lanes(round, 2) == {lane: (None, None) for lane in (1, 2, 3, 4)}
    assert LastHeat.kind(state(event).last_heat) == "recoverable"
    assert current(event) == (round.id, 2, True)

    assert reinstate(event) is None
    assert lanes(round, 2) == before
    assert LastHeat.kind(state(event).last_heat) == "none"
    assert current(event) == (round.id, 2, False)

    exchange = "004 heat.reinstate"
    expected = read_transcript(GOLDEN_DIR / "deviations" / "Q4" / "basic-racing.http")
    php = read_transcript(GOLDEN_DIR / "http" / "suite" / "basic-racing.http")
    django = [
        (r["lane"], r["finishtime"], r["finishplace"])
        for r in json.loads(expected[exchange]["body"])["racers"]
    ]
    assert django == [(lane, *round_trip(t, p)) for lane, (t, p) in before.items()]
    assert {r["finishplace"] for r in json.loads(php[exchange]["body"])["racers"]} == {
        ""
    }


def round_trip(time, place):
    """PHP shows a time with `finish-formatting`, `%5.3f`."""
    return float(f"{time:5.3f}"), place


def test_rerun_and_reinstate_bring_back_places_in_points_racing(race):
    event, round = race
    RaceSettings.objects.filter(event=event).update(use_points=True)
    finish(event, "place1=2&place2=1&place3=4&place4=3")
    before = lanes(round, 1)
    rerun(event, "last")
    reinstate(event)
    assert (
        lanes(round, 1)
        == before
        == {
            1: (None, 2),
            2: (None, 1),
            3: (None, 4),
            4: (None, 3),
        }
    )


def test_a_rerun_of_the_last_heat_of_another_round_names_its_own_group():
    """C57 in #64: PHP keeps the new round's `ClassID`, so its `START` names
    no class and no round."""
    event, wolves, bears = two_round_master_race()
    RaceSettings.objects.filter(event=event).update(use_master_sched=False)
    select_heat(event, round_id=wolves.id, now_racing=True)
    finish(event, "lane1=3.1&lane2=3.2")
    select_heat(event, round_id=bears.id, heat=1)
    connect_replay(event)
    rerun(event, "last")
    start = "START Wolves_Round1_Heat01"
    assert drain(event) == ["CANCEL", start, start]
    assert state(event).current_group_id == wolves.group_id


def test_a_racing_advance_into_another_round_starts_that_rounds_heat():
    """C58 in #64: under the master schedule PHP's `START` for the next heat
    keeps the class and round of the heat that just ran."""
    event, wolves, bears = two_round_master_race()
    select_heat(event, heat="next-up", now_racing=True)
    assert current(event) == (wolves.id, 1, True)
    connect_replay(event)
    finish(event, "lane1=3.1&lane2=3.2")
    assert current(event) == (bears.id, 1, True)
    assert drain(event) == ["REPLAY 4000 2 50", "CANCEL", "START Bears_Round1_Heat01"]


def test_a_rerun_heat_keeps_only_its_new_results(race):
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    rerun(event, "last")
    assert finish(event, "lane1=2.4&lane2=2.3&lane3=2.2&lane4=2.1").outcome is (
        Outcome.RECORDED
    )
    assert lanes(round, 1) == {1: (2.4, 4), 2: (2.3, 3), 3: (2.2, 2), 4: (2.1, 1)}
    best = {
        row.racer.id: row.summary.best
        for row in standings_snapshot(event).standings.rows
        if row.round.id == round.id
    }
    for lane in HeatLane.objects.filter(round=round, heat=1):
        assert best[lane.racer_id] == lane.finish_time
    assert current(event) == (round.id, 2, True)


def test_every_result_write_sets_completed_and_every_clear_empties_it(race):
    """D39's release of an emptied heat reads `completed`."""
    event, round = race
    started = timezone.now()
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    record_manual_result(event, fields("lane1=3.5&lane2=3.6&lane3=3.7&lane4=3.8"))
    for heat in (1, 2):
        stamps = HeatLane.objects.filter(round=round, heat=heat)
        assert all(s >= started for s in stamps.values_list("completed", flat=True))
    rerun(event, "last")
    assert not HeatLane.objects.filter(round=round, heat=2, completed__isnull=False)
    reinstate(event)
    assert not HeatLane.objects.filter(round=round, heat=2, completed__isnull=True)
    delete_results(event, round, 1)
    assert not HeatLane.objects.filter(round=round, heat=1, completed__isnull=False)
    delete_results(event)
    assert not HeatLane.objects.filter(event=event, completed__isnull=False)


def test_result_delete_of_the_current_heat_reads_it_under_the_lock(race):
    """`result.delete` with `roundid=current&heat=current` clears the heat
    that is current when the lock is held, and only that heat."""
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    finish(event, "lane1=3.5&lane2=3.6&lane3=3.7&lane4=3.8")
    select_heat(event, round_id=round.id, heat=2)

    assert delete_results(event, CURRENT, CURRENT) is None

    assert all(t is None for t, _ in lanes(round, 2).values())
    assert all(t is not None for t, _ in lanes(round, 1).values())


def test_result_delete_of_the_current_round_with_none_clears_nothing(race):
    """PHP matches `roundid = -1`, which names no round."""
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    cancel_running_round(event)

    assert delete_results(event, CURRENT) is None

    assert all(t is not None for t, _ in lanes(round, 1).values())


def test_a_partial_timer_result_finishes_the_heat_with_did_not_finish_times(race):
    """D42: a car the timer gives no time did not finish and places last.
    Every lane gets `completed`."""
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2")
    assert lanes(round, 1) == {
        1: (3.1, 1),
        2: (3.2, 2),
        3: (DID_NOT_FINISH, 3),
        4: (DID_NOT_FINISH, 3),
    }
    assert (
        HeatLane.objects.filter(round=round, heat=1, completed__isnull=True).count()
        == 0
    )


def test_a_manual_result_for_a_heat_that_moved_writes_nothing(race):
    """D19."""
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    shown = (round.id, 1)
    recorded = record_manual_result(event, fields("lane1=2.0&lane2=2.1"), shown)
    assert recorded.outcome is Outcome.WRONG_HEAT
    assert lanes(round, 2) == {lane: (None, None) for lane in (1, 2, 3, 4)}
    assert lanes(round, 1)[1] == (3.1, 1)


def test_a_manual_result_leaves_an_unposted_lane_without_a_result(race):
    """D42 is the timer's rule only; the race coordinator types what they
    mean."""
    event, round = race
    record_manual_result(event, fields("lane1=3.1&lane2=---"))
    assert lanes(round, 1) == {
        1: (3.1, None),
        2: (None, None),
        3: (None, None),
        4: (None, None),
    }


def test_a_legacy_manual_result_goes_to_the_current_heat(race):
    event, round = race
    record_manual_result(event, fields("lane1=2.0&lane2=2.1&lane3=2.2&lane4=2.3"))
    assert lanes(round, 1)[4] == (2.3, 4)
    assert current(event) == (round.id, 2, True)


def test_a_manual_result_with_racing_off_does_not_advance(race):
    event, round = race
    select_heat(event, now_racing=False)
    record_manual_result(event, fields("lane1=2.0&lane2=2.1&lane3=2.2&lane4=2.3"))
    assert current(event) == (round.id, 1, False)


def test_select_of_a_missing_heat_stops_racing_and_fails(race):
    event, round = race
    failure = select_heat(event, round_id=round.id, heat=9, now_racing=True)
    assert failure.code is Code.NO_SUCH_HEAT
    assert current(event) == (round.id, 1, False)


def test_racing_on_for_a_raced_heat_fails_with_the_round_buttons_wording(race):
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    failure = select_heat(event, round_id=round.id, now_racing=True)
    assert failure.code is Code.CANT_RACE
    assert "first heat in this group" in failure.description
    select_heat(event, heat=1)
    failure = select_heat(event, now_racing=True)
    assert "valid current heat" in failure.description


def test_cancel_clears_the_current_heat_and_its_group(race):
    event, _ = race
    cancel_running_round(event)
    now = state(event)
    assert (now.current_round, now.current_heat, now.current_group, now.racing) == (
        None,
        None,
        None,
        False,
    )


def test_unscheduling_the_current_round_cancels_it(race):
    event, round = race
    unschedule(round)
    assert current(event) == (None, None, False)


def test_under_the_master_schedule_a_new_chart_moves_to_the_next_up_heat():
    replay, round = setup_race()
    event = replay.event
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    generate_schedule(round, 1)
    assert current(event) == (round.id, 1, False)


def test_a_reschedule_never_follows_with_next_up():
    """Q6: PHP's reschedule never reaches its advance."""
    replay, round = setup_race()
    event = replay.event
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    from racing.services.schedule import after_chart_change

    after_chart_change(round, advance=False)
    assert current(event) == (None, None, False)


# ----- the results lock (D32) -----


@pytest.fixture
def raced_final():
    """Scouts round 1 raced, and a final of it with one heat raced."""
    replay, round1 = setup_race(partition="Scouts")
    event = replay.event
    select_heat(event, round_id=round1.id, now_racing=True)
    for _ in range(6):
        finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    final = Round.objects.create(event=event, group=round1.group, number=2)
    for lane in HeatLane.objects.filter(round=round1, heat__in=(1, 2)):
        HeatLane.objects.get_or_create(
            event=event, round=final, heat=lane.heat, lane=lane.lane, racer=lane.racer
        )
    select_heat(event, round_id=final.id, now_racing=True)
    return event, round1, final


def final_chart(final):
    return sorted(
        HeatLane.objects.filter(round=final).values_list("heat", "lane", "racer_id")
    )


@pytest.mark.parametrize(
    "change",
    ["manual", "delete", "rerun", "reinstate"],
)
def test_round_1_results_lock_once_a_heat_of_the_final_has_run(raced_final, change):
    event, round1, final = raced_final
    chart = final_chart(final)

    def attempt(control_race):
        if change == "manual":
            select_heat(event, round_id=round1.id, heat=2)
            recorded = record_manual_result(
                event, fields("lane1=2.5"), control_race=control_race
            )
            return recorded.failure
        if change == "delete":
            return delete_results(event, round1, 3, control_race=control_race)
        if change == "rerun":
            select_heat(event, round_id=round1.id, heat=4)
            return rerun(event, "current", control_race=control_race)
        heats_state = state(event)
        heats_state.last_heat = LastHeat.saved((round1.id, 5), [])
        heats_state.save()
        return reinstate(event, control_race=control_race)

    assert attempt(control_race=False) is None
    assert attempt(control_race=True) is None

    HeatLane.objects.filter(round=final, heat=1).update(
        finish_time=3.0, completed=timezone.now()
    )
    before = lanes(round1, 2), lanes(round1, 3), lanes(round1, 4)
    failure = attempt(control_race=False)
    assert failure is not None and failure.code is Code.RESULTS_LOCKED
    assert (lanes(round1, 2), lanes(round1, 3), lanes(round1, 4)) == before
    assert attempt(control_race=True) is None
    assert final_chart(final) == chart


def test_a_combined_round_locks_the_rounds_it_was_drawn_from():
    """D41: a round of an aggregate of Den2 and Den3 locks their rounds'
    results once one of its heats has a result."""
    replay, den2 = setup_race(racers=4, partition="Den2", first_car=201)
    event = replay.event
    for car in (301, 302, 303, 304):
        replay.post(
            f"action=racer.import&firstname=F{car}&lastname=L{car}"
            f"&carnumber={car}&partition=Den3"
        )
    den3 = Round.objects.get(event=event, group__name="Den3", number=1)
    ids = [replay.ids.of("class", r.group) for r in (den2, den3)]
    replay.post(
        f"action=class.add&name=TwoThree&constituent_{ids[0]}&constituent_{ids[1]}"
    )
    two_three = Round.objects.create(
        event=event, group=Group.objects.get(event=event, name="TwoThree"), number=1
    )
    racer = HeatLane.objects.filter(round=den2).first().racer
    HeatLane.objects.create(event=event, round=two_three, heat=1, lane=1, racer=racer)
    for round in (den2, den3):
        assert delete_results(event, round, control_race=False) is None
    HeatLane.objects.filter(round=two_three).update(
        finish_time=3.0, completed=timezone.now()
    )
    for round in (den2, den3):
        failure = delete_results(event, round, control_race=False)
        assert failure.code is Code.RESULTS_LOCKED
        assert delete_results(event, round) is None


# ----- emptied heats (D39, D40) -----


def empty_heat(round, heat):
    """A heat whose cars all left, as a reschedule leaves it (D39)."""
    numbers = HeatLane.objects.filter(round=round, heat=heat)
    master = numbers.values_list("master_heat", flat=True).first() or 0
    numbers.delete()
    return EmptiedHeat.objects.create(
        event=round.event, round=round, heat=heat, master_heat=master
    )


def test_next_lands_on_a_held_emptied_heat_and_the_crew_skips_it(race):
    event, round = race
    empty_heat(round, 2)
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    assert current(event) == (round.id, 2, False)
    assert advance(event, "next") is None
    assert current(event) == (round.id, 3, False)


def two_round_master_race():
    """Two groups' round 1 under the master schedule."""
    replay, wolves = setup_race(racers=4, lanes=2)
    for car in range(301, 305):
        replay.post(
            f"action=racer.import&firstname=F{car}&lastname=L{car}"
            f"&carnumber={car}&partition=Bears"
        )
    replay.post("action=racer.bulk&who=all&what=checkin")
    event = replay.event
    bears = Round.objects.get(event=event, group__name="Bears", number=1)
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    generate_schedule(wolves, 1)
    generate_schedule(bears, 1)
    return event, wolves, bears


def test_an_emptied_last_heat_is_let_go_once_the_crew_moves_to_another_round():
    """D40: the Wolves' last heat emptied after every other Wolves heat ran,
    so under the master schedule each chart change's next-up would land on
    it again."""
    event, wolves, bears = two_round_master_race()
    last = HeatLane.objects.filter(round=wolves).order_by("-heat").first().heat
    for heat in range(1, last):
        select_heat(event, round_id=wolves.id, heat=heat)
        record_manual_result(event, fields("lane1=3.1&lane2=3.2"))
    select_heat(event, round_id=wolves.id, heat=last)
    empty_heat(wolves, last)
    select_heat(event, round_id=bears.id, heat=1)
    assert not EmptiedHeat.objects.filter(round=wolves).exists()
    generate_schedule(bears, 1)
    assert current(event)[0] == bears.id
    assert advance(event, "next-up") is None
    assert current(event)[0] == bears.id


def test_the_heat_state_machine_reads_the_master_index_with_its_emptied_heats():
    event, wolves, bears = two_round_master_race()
    empty_heat(wolves, 2)
    chart = load_chart(event.id, use_master_sched=True)
    assert chart.master_index() == master_index(event)
    assert (wolves.id, 2) in {(r, h) for _, r, h in chart.emptied}


def staged_emptied_heat():
    """Wolves and Bears interleaved: W1 B1 W2 B2 W3 raced, B3 running, and
    the staged W4 emptied by a reschedule (D39)."""
    event, wolves, bears = two_round_master_race()
    select_heat(event, round_id=wolves.id, heat=1, now_racing=True)
    for _ in range(5):
        record_timer_result(event, fields("lane1=3.1&lane2=3.2"))
    assert current(event) == (bears.id, 3, True)
    empty_heat(wolves, 4)
    return event, wolves, bears


@pytest.mark.parametrize("crew", ["rerun", "race-again", "chart-change"])
def test_the_staged_emptied_heat_stays_while_the_running_heat_stays(crew):
    """D40 lets an emptied heat go only when the current heat moves to
    another round, and D39 never lets the staged heat go."""
    event, wolves, bears = staged_emptied_heat()
    if crew == "rerun":
        assert rerun(event, "current") is None
    elif crew == "race-again":
        select_heat(event, round_id=bears.id, heat=3, now_racing=True)
    else:
        # What a chart change does under the master schedule.
        advance(event, "next-up")
        assert current(event) == (bears.id, 3, True)
    assert EmptiedHeat.objects.filter(round=wolves, heat=4).exists()
    record_timer_result(event, fields("lane1=3.1&lane2=3.2"))
    assert current(event)[:2] == (wolves.id, 4)
    assert advance(event, "next") is None


def test_an_emptied_heat_stays_until_the_crew_moves_to_another_round():
    """D40: a rerun of the running heat is no move, even when the emptied
    heat is not staged."""
    event, wolves, bears = two_round_master_race()
    RaceSettings.objects.filter(event=event).update(use_master_sched=False)
    select_heat(event, round_id=bears.id, now_racing=True)
    empty_heat(wolves, 4)
    assert rerun(event, "current") is None
    assert EmptiedHeat.objects.filter(round=wolves, heat=4).exists()
    select_heat(event, round_id=wolves.id, heat=1)
    select_heat(event, round_id=bears.id, heat=1)
    assert not EmptiedHeat.objects.filter(round=wolves, heat=4).exists()


def test_the_staged_emptied_heat_stays_when_the_crew_moves_onto_its_round():
    event, wolves, bears = staged_emptied_heat()
    select_heat(event, round_id=wolves.id, heat=3)
    assert EmptiedHeat.objects.filter(round=wolves, heat=4).exists()


def test_a_move_to_another_round_keeps_the_heat_staged_after_it():
    """Bears 4, the last heat, empties while it runs. The crew goes back to
    Wolves 3, and Bears 4 is the next heat on the track again."""
    event, wolves, bears = two_round_master_race()
    select_heat(event, round_id=wolves.id, heat=1, now_racing=True)
    for _ in range(7):
        record_timer_result(event, fields("lane1=3.1&lane2=3.2"))
    assert current(event) == (bears.id, 4, True)
    empty_heat(bears, 4)
    select_heat(event, round_id=wolves.id, heat=3)
    assert EmptiedHeat.objects.filter(round=bears, heat=4).exists()


def test_an_emptied_heat_with_heats_after_it_is_kept_when_the_crew_moves_on():
    event, wolves, bears = two_round_master_race()
    empty_heat(wolves, 1)
    select_heat(event, round_id=bears.id, heat=1)
    assert EmptiedHeat.objects.filter(round=wolves, heat=1).exists()


def test_an_emptied_last_heat_is_kept_while_its_round_is_current(race):
    event, round = race
    last = HeatLane.objects.filter(round=round).order_by("-heat").first().heat
    empty_heat(round, last)
    select_heat(event, round_id=round.id, heat=2)
    assert EmptiedHeat.objects.filter(round=round, heat=last).exists()


# ----- the playlist's pending scene -----


def test_a_finished_round_with_a_scene_shows_it_10_seconds_later(race, monkeypatch):
    event, round = race
    awards = Scene.objects.get(event=event, name="Awards")
    PlaylistEntry.objects.create(
        event=event, seq=1, group=round.group, round_number=1, scene_at_finish=awards
    )
    Kiosk.objects.create(event=event, address="K1", name="Main", page="welcome")
    frozen = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: frozen)
    for _ in range(6):
        finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    assert state(event).playlist_scene_id == awards.id
    assert state(event).playlist_scene_at == frozen + timedelta(seconds=10)

    assert not playlist.apply_due_scene(event, frozen + timedelta(seconds=10))
    assert Kiosk.objects.get(address="K1").page == "welcome"
    assert playlist.apply_due_scene(event, frozen + timedelta(seconds=10.001))
    assert Kiosk.objects.get(address="K1").page == "kiosks/award-presentations.kiosk"
    assert state(event).current_scene_id == awards.id
    assert state(event).playlist_scene_at is None


def test_racing_on_applies_the_racing_scene(race):
    event, round = race
    racing_scene = Scene.objects.get(event=event, name="Racing")
    RaceSettings.objects.filter(event=event).update(racing_scene=racing_scene)
    Kiosk.objects.create(event=event, address="K1", name="Aux1", page="welcome")
    select_heat(event, now_racing=False)
    select_heat(event, now_racing=True)
    assert Kiosk.objects.get(address="K1").page == "kiosks/ondeck.kiosk"
    assert state(event).current_scene_id == racing_scene.id


def test_an_imported_php_last_heat_reinstates_its_one_column(race):
    event, round = race
    finish(event, "lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    rerun(event, "last")
    now = state(event)
    now.last_heat = {"round_id": round.id, "heat": 1, "results": [3.1, None, 3.3]}
    now.save()
    reinstate(event)
    assert lanes(round, 1) == {
        1: (3.1, None),
        2: (None, None),
        3: (3.3, None),
        4: (None, None),
    }
