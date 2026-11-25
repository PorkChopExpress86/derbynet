"""D136: a result on any heat of the waiting final, from the timer or by
hand, closes the intermission, and the final then races on as a normal
round. A rerun closes it too. Skip never passes a final that has a result
(#501). D143: a result `result.import` writes closes it too (#519)."""

import pytest
from django.utils import timezone

from racing.models import HAS_RESULT, HeatLane, PlaylistEntry
from racing.services.heats import advance, reinstate, rerun, select_heat, set_racing
from racing.services.playlist import (
    Failure,
    PlaylistFailure,
    round_ended,
    set_step_waits,
    skip_waiting_step,
    start_playlist,
    start_waiting_step,
)
from racing.services.result_import import import_result
from racing.services.results import (
    discard_heat,
    record_manual_result,
    record_timer_result,
    restore_results,
)
from racing.services.schedule import generate_schedule
from racing.tests.test_race_day import one_finalist, race, two_step_playlist
from racing.tests.test_result_changes_rebuild_the_final import (
    fast,
    last_heat,
    roster,
    slow,
    state_of_event,
    timed,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def waiting_final():
    """Scouts round 1 raced through the timer, and its final, of more than
    one heat, waiting for the operator with its first heat current."""
    event, round1, final, entry, finalists_for = timed(waits=True)
    assert state_of_event(event).waiting_entry_id == entry.id
    assert len(heats_of(final)) > 1
    return event, round1, final, entry, finalists_for


def heats_of(round):
    return sorted(
        set(HeatLane.objects.filter(round=round).values_list("heat", flat=True))
    )


def raced_heats(round):
    lanes = HeatLane.objects.filter(round=round).filter(HAS_RESULT)
    return sorted(set(lanes.values_list("heat", flat=True)))


def assert_skip_refused(event, finalists_for):
    with pytest.raises(PlaylistFailure) as caught:
        skip_waiting_step(event, finalists_for)
    assert caught.value.code == Failure.NO_INTERMISSION


def test_a_manual_result_on_the_waiting_final_closes_the_intermission(waiting_final):
    """Skip then has no intermission to end, so the final's other heats stay
    the next to race."""
    event, round1, final, entry, finalists_for = waiting_final
    recorded = record_manual_result(event, slow(final, 1), finalists_for=finalists_for)
    assert recorded.failure is None
    assert raced_heats(final) == [1]
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None

    assert_skip_refused(event, finalists_for)
    now = state_of_event(event)
    assert now.current_round_id == final.id
    assert now.current_heat == 1
    assert raced_heats(final) == [1]


def test_a_timer_result_on_the_waiting_final_closes_the_intermission(waiting_final):
    """A rerun, the usual way to race a heat again, closes the intermission
    itself, so racing is set on here with the intermission open. The final
    races on at its next heat."""
    event, round1, final, entry, finalists_for = waiting_final
    now = state_of_event(event)
    now.racing = True
    now.save()
    record_timer_result(event, slow(final, 1), finalists_for=finalists_for)
    assert raced_heats(final) == [1]
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
    assert now.current_round_id == final.id
    assert now.current_heat == 2
    assert now.racing


def select_last_heat_of(event, round1):
    """Make the ended round's last heat current with the intermission open,
    as the tests of D27 do: a `heat.select` would close it."""
    heat = last_heat(round1)
    now = state_of_event(event)
    now.current_round_id, now.current_heat = round1.id, heat
    now.save()
    return heat


def test_a_manual_result_on_the_ended_round_keeps_the_intermission_open(waiting_final):
    """D27's change to the round that ended is no result on the final."""
    event, round1, final, entry, finalists_for = waiting_final
    heat = select_last_heat_of(event, round1)
    record_manual_result(event, fast(round1, heat), finalists_for=finalists_for)
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert now.ended_round_id == round1.id


def test_a_step_switched_not_to_wait_keeps_its_intermission_until_a_result(
    waiting_final,
):
    """D136 closes the intermission on a result on the final, not on the
    step's "Wait for operator" switch. With the switch off and no result on
    the final, a manual result on the ended round still rebuilds the final
    (D27)."""
    event, round1, final, entry, finalists_for = waiting_final
    before = roster(final)
    set_step_waits(event, entry.id, False)
    heat = select_last_heat_of(event, round1)
    record_manual_result(event, fast(round1, heat), finalists_for=finalists_for)
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert now.ended_round_id == round1.id
    assert roster(final) != before
    assert roster(final) == sorted(r.pk for r in finalists_for(entry, final).racers)


@pytest.mark.parametrize(
    "which, in_final",
    [("current", True), ("last", False)],
)
def test_a_rerun_closes_the_intermission(waiting_final, which, in_final):
    """`current` is the final's first heat, and `last` is round 1's last
    heat, which the timer wrote."""
    event, round1, final, entry, finalists_for = waiting_final
    assert round1.number == 1
    target = (final.id, 1) if in_final else (round1.id, last_heat(round1))
    assert rerun(event, which, finalists_for=finalists_for) is None
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
    assert now.current_round_id == target[0]
    assert now.current_heat == target[1]
    assert now.racing


def race_first_final_heat(event, final, finalists_for):
    assert start_waiting_step(event) == final
    record_timer_result(event, slow(final, 1), finalists_for=finalists_for)
    assert raced_heats(final) == [1]


def end_round_1_again(event, round1, finalists_for):
    """Rerun round 1's last heat with the times it had, so the round ends
    again with the same finalists."""
    assert round1.number == 1
    heat = last_heat(round1)
    assert select_heat(event, round_id=round1.id, heat=heat) is None
    assert rerun(event, "current", finalists_for=finalists_for) is None
    record_timer_result(event, slow(round1, heat), finalists_for=finalists_for)


def test_a_final_that_has_a_result_does_not_wait_again(waiting_final):
    """Round 1 ends again after the final's first heat, with no result
    written after it ends. The final is the next step and races on as a
    normal round: its next heat is current, racing stays off as the step
    before it does not say `continue_racing`, and no intermission opens for
    Skip to end."""
    event, round1, final, entry, finalists_for = waiting_final
    assert round1.number == 1
    race_first_final_heat(event, final, finalists_for)
    assert set_racing(event, False) is None
    round_ended(round1, finalists_for)
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
    assert now.current_round_id == final.id
    assert now.current_heat == 2
    assert not now.racing
    assert_skip_refused(event, finalists_for)
    assert raced_heats(final) == [1]


def test_next_from_the_ended_round_does_not_make_a_raced_final_wait(waiting_final):
    """Next on round 1's last heat finds the round complete and ends it
    again with no result written."""
    event, round1, final, entry, finalists_for = waiting_final
    assert round1.number == 1
    race_first_final_heat(event, final, finalists_for)
    assert select_heat(event, round_id=round1.id, heat=last_heat(round1)) is None
    advance(event, "next", report=False, finalists_for=finalists_for)
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
    assert now.current_round_id == final.id
    assert now.current_heat == 2
    assert not now.racing


def test_a_final_that_has_a_result_goes_on_racing_when_the_step_continues():
    """A step that does not wait starts with racing on when the step before
    it says `continue_racing`, and so does a final that has a result."""
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=True)
    event = replay.event
    round1 = start_playlist(event, finalists_for)
    race(round1)
    round_ended(round1, finalists_for)
    entry = state_of_event(event).waiting_entry
    final = entry.group.rounds.get(number=2)
    race_first_final_heat(event, final, finalists_for)
    assert set_racing(event, False) is None
    round_ended(round1, finalists_for)
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
    assert now.current_round_id == final.id
    assert now.current_heat == 2
    assert now.racing


def test_a_reinstate_onto_the_waiting_final_closes_the_intermission(waiting_final):
    """The final's first heat is rerun, which clears it, and round 1 ends
    again from its last heat's Next, so the final waits again with no
    result. The reinstate brings that heat's result back."""
    event, round1, final, entry, finalists_for = waiting_final
    assert round1.number == 1
    race_first_final_heat(event, final, finalists_for)
    assert rerun(event, "last", finalists_for=finalists_for) is None
    assert raced_heats(final) == []
    assert select_heat(event, round_id=round1.id, heat=last_heat(round1)) is None
    advance(event, "next", report=False, finalists_for=finalists_for)
    assert state_of_event(event).waiting_entry_id == entry.id

    assert reinstate(event, finalists_for=finalists_for) is None
    assert raced_heats(final) == [1]
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None


def test_undoing_a_discard_on_the_waiting_final_closes_the_intermission(waiting_final):
    """The final's first heat is discarded and round 1 ends again, so the
    final waits again with no result. Undo writes the result back."""
    event, round1, final, entry, finalists_for = waiting_final
    race_first_final_heat(event, final, finalists_for)
    failure, cleared = discard_heat(event, final, 1, finalists_for=finalists_for)
    assert failure is None
    assert raced_heats(final) == []
    racers = roster(final)
    end_round_1_again(event, round1, finalists_for)
    assert state_of_event(event).waiting_entry_id == entry.id
    assert roster(final) == racers

    assert (
        restore_results(event, final, 1, cleared, finalists_for=finalists_for) is None
    )
    assert raced_heats(final) == [1]
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None


def test_skip_does_not_stop_at_a_next_step_that_has_a_result():
    """D136: "Skip never passes a final that has a result". The step after
    a skipped final waits too, and its round has a heat raced. Were the
    intermission to move to it, the next Skip would pass a round that has a
    result. So it starts as a step that does not wait: its next heat is
    current with racing on."""
    replay, _ = two_step_playlist(waits=True, continue_racing=False)
    event = replay.event
    PlaylistEntry.objects.filter(event=event, seq=3).update(waits_for_operator=True)
    finalists_for = one_finalist(replay)
    scouts = start_playlist(event, finalists_for)
    race(scouts)
    round_ended(scouts, finalists_for)
    next_step = PlaylistEntry.objects.get(event=event, seq=3)
    outlaws = next_step.group.rounds.get(number=1)
    generate_schedule(outlaws, 1)
    HeatLane.objects.filter(round=outlaws, heat=1).update(
        finish_time=3.0, completed=timezone.now()
    )
    assert len(heats_of(outlaws)) > 1
    assert raced_heats(outlaws) == [1]

    assert skip_waiting_step(event, finalists_for) == outlaws
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
    assert now.current_round_id == outlaws.id
    assert now.current_heat == 2
    assert now.racing


def import_line(event, round, heat, lane, finishtime="3.1", finishplace="1"):
    """A `result.import` line for a racer of the round's group. A lane the
    chart already holds refuses the line (`heatlane_one_car_per_lane`), so
    the line names a free one."""
    racer = HeatLane.objects.filter(round=round).first().racer
    assert not HeatLane.objects.filter(round=round, heat=heat, lane=lane).exists()
    fields = {
        "class": round.group.name,
        "round": str(round.number),
        "heat": str(heat),
        "lane": str(lane),
        "carnumber": str(racer.car_number),
        "firstname": racer.first_name,
        "lastname": racer.last_name,
        "finishtime": finishtime,
        "finishplace": finishplace,
    }
    assert import_result(event, fields) is None


@pytest.mark.parametrize("heat, lane", [(1, 5), (9, 1)])
def test_an_imported_result_on_the_waiting_final_closes_the_intermission(
    waiting_final, heat, lane
):
    """D143: D136 covers a result `result.import` writes, on the final's
    first heat or on a heat its chart lacks. The current heat and racing
    stay as they are, and Skip then has no intermission to end."""
    event, round1, final, entry, finalists_for = waiting_final
    import_line(event, final, heat, lane)
    assert raced_heats(final) == [heat]
    now = state_of_event(event)
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
    assert now.current_round_id == final.id
    assert now.current_heat == 1
    assert not now.racing
    assert_skip_refused(event, finalists_for)


def test_an_imported_line_with_no_result_keeps_the_intermission_open(waiting_final):
    """A line with no time and no place writes no result (`HAS_RESULT`)."""
    event, round1, final, entry, finalists_for = waiting_final
    import_line(event, final, 1, 5, finishtime="", finishplace="")
    assert raced_heats(final) == []
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert now.ended_round_id == round1.id


def test_an_imported_result_on_the_ended_round_keeps_the_intermission_open(
    waiting_final,
):
    """A result on the round that ended is no result on the final."""
    event, round1, final, entry, finalists_for = waiting_final
    import_line(event, round1, last_heat(round1) + 1, 1)
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert now.ended_round_id == round1.id
