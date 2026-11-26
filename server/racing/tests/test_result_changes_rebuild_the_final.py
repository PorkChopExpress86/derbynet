"""D27 through the result services: a reinstate, a manual result or a
result delete on the round that just ended rebuilds the waiting final from
the standings in the same transaction, until a heat of the final has a
result (#20, "Race-day flow"). A rerun closes the intermission (D136), so
the final is rebuilt when the rerun's timer result ends the round again.

D135: the round ending again rebuilds it too, so a rerun made while the
intermission was closed does not leave the old finalists waiting (#500).

D141: with the intermission closed, a delete, a discard, its undo, a
result written by hand or an imported line on the round that feeds the
waiting final rebuilds it too, while it has no heat run (#511)."""

import pytest
from django.utils import timezone

from core.models import RaceState
from racing.models import HAS_RESULT, HeatLane, PlaylistEntry, RosterEntry, Round
from racing.services.heats import reinstate, rerun, select_heat, set_racing
from racing.services.playlist import round_ended, start_playlist, start_waiting_step
from racing.services.result_import import import_result
from racing.services.results import (
    delete_results,
    discard_heat,
    record_manual_result,
    record_timer_result,
    restore_results,
)
from racing.services.rounds import delete_round_roster
from racing.services.schedule import unschedule
from racing.services.tests.race import fields
from racing.tests.test_race_day import race, state, two_step_playlist
from roster.models import Racer

pytestmark = pytest.mark.django_db


@pytest.fixture
def intermission():
    """Scouts round 1 raced, its top 4 final waiting for the operator."""
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=False)
    round1 = start_playlist(replay.event, finalists_for)
    race(round1)
    round_ended(round1, finalists_for)
    entry = state(replay).waiting_entry
    final = Round.objects.get(group=entry.group, number=2)
    return replay.event, round1, final, entry, finalists_for


def state_of_event(event):
    return RaceState.objects.get(event=event)


def roster(final):
    return sorted(final.roster.values_list("racer_id", flat=True))


def standings_say(final, entry, finalists_for):
    return sorted(r.pk for r in finalists_for(entry, final).racers)


def last_heat(round1):
    return HeatLane.objects.filter(round=round1).order_by("-heat").first().heat


def fast(round1, heat):
    """Times that put every car of the heat in front."""
    lanes = HeatLane.objects.filter(round=round1, heat=heat).order_by("lane")
    return fields("&".join(f"lane{lane.lane}=1.0{lane.lane}" for lane in lanes))


def test_a_rerun_and_its_new_timer_result_rebuild_the_final(intermission):
    event, round1, final, entry, finalists_for = intermission
    before = roster(final)
    heat = last_heat(round1)
    now = state_of_event(event)
    now.last_heat = {"round_id": round1.id, "heat": heat, "results": None}
    now.save()
    assert rerun(event, "last") is None
    after_rerun = state_of_event(event)
    assert after_rerun.waiting_entry_id is None
    assert roster(final) == before
    assert after_rerun.current_round_id == round1.id
    assert after_rerun.current_heat == heat
    assert after_rerun.racing

    record_timer_result(event, fast(round1, heat))
    assert roster(final) == standings_say(final, entry, finalists_for)
    assert roster(final) != before
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert (now.current_round_id, now.current_heat, now.racing) == (final.id, 1, False)


def test_a_reinstate_rebuilds_the_final(intermission):
    event, round1, final, entry, finalists_for = intermission
    heat = last_heat(round1)
    HeatLane.objects.filter(round=round1, heat=heat).update(
        finish_time=9.9, completed=timezone.now()
    )
    now = state_of_event(event)
    now.last_heat = {
        "round_id": round1.id,
        "heat": heat,
        "results": None,
        "lanes": [
            {"lane": lane.lane, "time": 1.0 + lane.lane / 100, "place": lane.lane}
            for lane in HeatLane.objects.filter(round=round1, heat=heat)
        ],
    }
    now.save()
    before = roster(final)
    assert reinstate(event) is None
    assert roster(final) == standings_say(final, entry, finalists_for) != before


def test_a_result_delete_rebuilds_the_final(intermission):
    event, round1, final, entry, finalists_for = intermission
    before = roster(final)
    assert delete_results(event, round1) is None
    assert roster(final) == standings_say(final, entry, finalists_for) != before


def test_a_manual_result_on_the_ended_round_rebuilds_the_final(intermission):
    """The legacy form writes the current heat, which a `heat.select` would
    move, closing the intermission. Here the current heat is the ended
    round's last heat while the intermission stays open."""
    event, round1, final, entry, finalists_for = intermission
    before = roster(final)
    heat = last_heat(round1)
    now = state_of_event(event)
    now.current_round_id, now.current_heat = round1.id, heat
    now.save()
    record_manual_result(event, fast(round1, heat))
    assert roster(final) == standings_say(final, entry, finalists_for) != before


def test_a_change_makes_a_final_deleted_during_the_intermission_again(intermission):
    event, round1, final, entry, finalists_for = intermission
    unschedule(final)
    delete_round_roster(final)
    assert not Round.objects.filter(group=entry.group, number=2).exists()
    heat = last_heat(round1)
    now = state_of_event(event)
    now.current_round_id, now.current_heat = round1.id, heat
    now.save()
    record_manual_result(event, fast(round1, heat))
    remade = Round.objects.get(group=entry.group, number=2)
    assert roster(remade) == standings_say(remade, entry, finalists_for) != []
    assert HeatLane.objects.filter(round=remade).exists()


def test_a_change_after_the_final_has_raced_keeps_its_roster(intermission):
    event, round1, final, entry, finalists_for = intermission
    before = roster(final)
    HeatLane.objects.filter(round=final, heat=1).update(
        finish_time=3.0, completed=timezone.now()
    )
    delete_results(event, round1)
    assert roster(final) == before


def test_a_change_to_another_round_keeps_the_final(intermission):
    event, round1, final, entry, finalists_for = intermission
    before = sorted(HeatLane.objects.filter(round=final).values_list("id", flat=True))
    delete_results(event, final)
    assert sorted(
        HeatLane.objects.filter(round=final).values_list("id", flat=True)
    ) == (before)


def test_a_change_that_keeps_the_finalists_keeps_the_final_chart(intermission):
    """A reinstate of the results the heat already has changes no finalist,
    so the final's chart is not made again."""
    event, round1, final, entry, finalists_for = intermission
    chart = sorted(HeatLane.objects.filter(round=final).values_list("id", flat=True))
    heat = last_heat(round1)
    now = state_of_event(event)
    now.last_heat = {
        "round_id": round1.id,
        "heat": heat,
        "results": None,
        "lanes": [
            {"lane": lane, "time": time, "place": place}
            for lane, time, place in HeatLane.objects.filter(
                round=round1, heat=heat
            ).values_list("lane", "finish_time", "finish_place")
        ],
    }
    now.save()
    before = roster(final)
    assert reinstate(event) is None
    assert roster(final) == before == standings_say(final, entry, finalists_for)
    assert (
        sorted(HeatLane.objects.filter(round=final).values_list("id", flat=True))
        == chart
    )


def test_a_reordered_cut_rebuilds_the_final_in_its_new_order(intermission):
    """The same finalists in another order make another chart, as a fresh
    activation would, so the roster follows the cut's order."""
    event, round1, final, entry, finalists_for = intermission
    fastest = [r.pk for r in finalists_for(entry, final).racers][-1]
    for lane in HeatLane.objects.filter(round=round1, racer_id=fastest).order_by(
        "heat"
    ):
        now = state_of_event(event)
        now.current_round_id, now.current_heat = round1.id, lane.heat
        now.save()
        record_manual_result(event, fields(f"lane{lane.lane}=1.001"))
    cut = [r.pk for r in finalists_for(entry, final).racers]
    assert sorted(cut) == roster(final) and cut[0] == fastest
    assert (
        list(
            RosterEntry.objects.filter(round=final)
            .order_by("id")
            .values_list("racer_id", flat=True)
        )
        == cut
    )


# ----- the round ends again (D135) -----


def slow(round, heat):
    """Times that depend on the car, so no heat decides the cut alone."""
    lanes = HeatLane.objects.filter(round=round, heat=heat).select_related("racer")
    return fields(
        "&".join(
            f"lane{lane.lane}="
            f"{(2000 + (lane.racer.car_number * 37 + lane.lane * 11) % 1000) / 1000}"
            for lane in lanes.order_by("lane")
        )
    )


def race_by_timer(event, round, finalists_for):
    """Every heat of the current round from the timer, so its last result
    ends the round as race day does."""
    heats = HeatLane.objects.filter(round=round).values_list("heat", flat=True)
    for heat in sorted(set(heats)):
        assert state_of_event(event).current_heat == heat
        record_timer_result(event, slow(round, heat), finalists_for=finalists_for)


def timed(waits):
    replay, finalists_for = two_step_playlist(waits=waits, continue_racing=False)
    event = replay.event
    round1 = start_playlist(event, finalists_for)
    assert (round1.group.name, round1.number) == ("Scouts", 1)
    race_by_timer(event, round1, finalists_for)
    final = Round.objects.get(group=round1.group, number=2)
    now = state_of_event(event)
    assert (now.current_round_id, now.current_heat, now.racing) == (final.id, 1, False)
    entry = PlaylistEntry.objects.get(group=final.group, round_number=2)
    return event, round1, final, entry, finalists_for


@pytest.fixture
def timed_intermission():
    """Scouts round 1 raced through the timer, whose last result opens the
    intermission before the final."""
    made = timed(waits=True)
    assert state_of_event(made[0]).waiting_entry_id == made[3].id
    return made


def chart_ids(round):
    return sorted(HeatLane.objects.filter(round=round).values_list("id", flat=True))


def test_a_rerun_made_while_the_intermission_was_closed_rebuilds_the_final(
    timed_intermission,
):
    """#500: selecting the final closes the intermission, so the rerun and
    its timer result change round 1 with no intermission open. The round
    ends again, the intermission opens again, and the final holds the
    racers the standings pick now."""
    event, round1, final, entry, finalists_for = timed_intermission
    before = roster(final)
    assert select_heat(event, round_id=final.id, heat=1) is None
    assert state_of_event(event).waiting_entry_id is None
    assert rerun(event, "last") is None
    heat = last_heat(round1)
    now = state_of_event(event)
    assert (now.current_round_id, now.current_heat, now.racing) == (
        round1.id,
        heat,
        True,
    )

    record_timer_result(event, fast(round1, heat), finalists_for=finalists_for)
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert (now.current_round_id, now.current_heat, now.racing) == (final.id, 1, False)
    assert roster(final) == standings_say(final, entry, finalists_for) != before
    assert set(
        HeatLane.objects.filter(round=final).values_list("racer_id", flat=True)
    ) == set(roster(final))


def test_a_round_that_ends_again_with_the_same_finalists_keeps_the_final_chart(
    timed_intermission,
):
    event, round1, final, entry, finalists_for = timed_intermission
    before, chart = roster(final), chart_ids(final)
    assert select_heat(event, round_id=final.id, heat=1) is None
    assert rerun(event, "last") is None
    record_timer_result(
        event, slow(round1, last_heat(round1)), finalists_for=finalists_for
    )
    assert state_of_event(event).waiting_entry_id == entry.id
    assert roster(final) == before == standings_say(final, entry, finalists_for)
    assert chart_ids(final) == chart


def test_a_round_that_ends_again_keeps_a_final_that_has_a_result(timed_intermission):
    """The rebuild is for a final with no heat run. Round 1 ends again
    after the final's first heat, and the final keeps its racers and that
    heat's results."""
    event, round1, final, entry, finalists_for = timed_intermission
    before, chart = roster(final), chart_ids(final)
    assert start_waiting_step(event) == final
    record_timer_result(event, slow(final, 1), finalists_for=finalists_for)
    raced = HeatLane.objects.filter(round=final).filter(HAS_RESULT)
    unraced = HeatLane.objects.filter(round=final).exclude(HAS_RESULT)
    assert raced.exists()
    assert unraced.exists()
    results = sorted(raced.values_list("id", "finish_time"))

    heat = last_heat(round1)
    assert select_heat(event, round_id=round1.id, heat=heat) is None
    assert rerun(event, "current") is None
    record_timer_result(event, fast(round1, heat), finalists_for=finalists_for)
    assert standings_say(final, entry, finalists_for) != before
    assert roster(final) == before
    assert chart_ids(final) == chart
    assert sorted(raced.values_list("id", "finish_time")) == results


def test_a_final_that_does_not_wait_keeps_its_roster_as_php_does():
    """D135 rebuilds a waiting final. A step that does not wait keeps the
    roster it has, as PHP's playlist writes a roster only for a round with
    none (`website/inc/playlist.inc:85`)."""
    event, round1, final, entry, finalists_for = timed(waits=False)
    assert state_of_event(event).waiting_entry_id is None
    before = roster(final)
    assert rerun(event, "last") is None
    record_timer_result(
        event, fast(round1, last_heat(round1)), finalists_for=finalists_for
    )
    assert roster(final) == before != standings_say(final, entry, finalists_for)


# ----- a change with the intermission closed (D141) -----


@pytest.fixture
def closed_intermission(timed_intermission):
    """The coordinator selected the final's first heat, which closes the
    intermission (#511)."""
    event, round1, final, entry, finalists_for = timed_intermission
    assert select_heat(event, round_id=final.id, heat=1) is None
    assert state_of_event(event).waiting_entry_id is None
    return timed_intermission


def select_round_1_heat(event, round1, heat):
    """The legacy form writes the current heat, so a manual result on Round 1
    goes through `heat.select` first, with racing off."""
    assert select_heat(event, round_id=round1.id, heat=heat) is None
    assert_current(event, round1.id, heat, racing=False)


def assert_current(event, round_id, heat, *, racing):
    now = state_of_event(event)
    assert now.current_round_id == round_id
    assert now.current_heat == heat
    assert now.racing is racing


def assert_rebuilt(event, final, entry, finalists_for, before, round_id, heat):
    """The final holds the racers the standings pick now, its chart races
    them, the intermission stays closed and racing is off."""
    assert roster(final) == standings_say(final, entry, finalists_for) != before
    assert set(
        HeatLane.objects.filter(round=final).values_list("racer_id", flat=True)
    ) == set(roster(final))
    assert state_of_event(event).waiting_entry_id is None
    assert_current(event, round_id, heat, racing=False)


def faster_line(round1, racer):
    """A `result.import` line onto a heat Round 1's chart lacks, with a time
    no car beat, which puts `racer` in the cut."""
    return {
        "class": round1.group.name,
        "round": "1",
        "heat": str(last_heat(round1) + 1),
        "lane": "1",
        "carnumber": str(racer.car_number),
        "firstname": racer.first_name,
        "lastname": racer.last_name,
        "finishtime": "0.5",
        "finishplace": "1",
    }


def outside_the_cut(round1, finalists):
    return (
        HeatLane.objects.filter(round=round1)
        .exclude(racer_id__in=finalists)
        .select_related("racer")
        .first()
        .racer
    )


def test_a_delete_with_the_intermission_closed_rebuilds_the_final(
    closed_intermission,
):
    """A Round 1 heat with no result leaves the cut with no racer, so the
    final is made again with no roster and no chart, and its first heat
    stops being current, as D27 makes it with the intermission open
    (`test_a_result_delete_rebuilds_the_final`)."""
    event, round1, final, entry, finalists_for = closed_intermission
    before = roster(final)
    assert delete_results(event, round1, 1, finalists_for=finalists_for) is None
    assert standings_say(final, entry, finalists_for) == []
    assert_rebuilt(event, final, entry, finalists_for, before, None, None)


def test_a_discard_with_the_intermission_closed_rebuilds_the_final(
    closed_intermission,
):
    event, round1, final, entry, finalists_for = closed_intermission
    before = roster(final)
    failure, cleared = discard_heat(event, round1, 1, finalists_for=finalists_for)
    assert failure is None and cleared
    assert standings_say(final, entry, finalists_for) == []
    assert_rebuilt(event, final, entry, finalists_for, before, None, None)


def test_undoing_a_discard_with_the_intermission_closed_rebuilds_the_final(
    closed_intermission,
):
    """Undo brings back the standings the final was first made from, and
    the final with them."""
    event, round1, final, entry, finalists_for = closed_intermission
    before = roster(final)
    failure, cleared = discard_heat(event, round1, 1, finalists_for=finalists_for)
    assert failure is None
    assert roster(final) == []
    assert (
        restore_results(event, round1, 1, cleared, finalists_for=finalists_for) is None
    )
    assert roster(final) == standings_say(final, entry, finalists_for) == before
    assert set(
        HeatLane.objects.filter(round=final).values_list("racer_id", flat=True)
    ) == set(before)
    assert_current(event, final.id, 1, racing=False)


def test_a_manual_result_with_the_intermission_closed_rebuilds_the_final(
    closed_intermission,
):
    event, round1, final, entry, finalists_for = closed_intermission
    before = roster(final)
    heat = last_heat(round1)
    select_round_1_heat(event, round1, heat)
    record_manual_result(event, fast(round1, heat), finalists_for=finalists_for)
    assert_rebuilt(event, final, entry, finalists_for, before, round1.id, heat)


def test_a_manual_result_after_a_rerun_and_racing_off_rebuilds_the_final(
    closed_intermission,
):
    """#501's path: a rerun of Round 1's last heat, racing turned off, and a
    result written by hand, which never ends the round."""
    event, round1, final, entry, finalists_for = closed_intermission
    before = roster(final)
    heat = last_heat(round1)
    select_round_1_heat(event, round1, heat)
    assert rerun(event, "current") is None
    assert set_racing(event, False) is None
    record_manual_result(event, fast(round1, heat), finalists_for=finalists_for)
    assert_rebuilt(event, final, entry, finalists_for, before, round1.id, heat)


def test_an_imported_result_on_round_1_rebuilds_the_final(closed_intermission):
    event, round1, final, entry, finalists_for = closed_intermission
    before = roster(final)
    outside = outside_the_cut(round1, before)
    assert import_result(event, faster_line(round1, outside)) is None
    assert outside.pk in standings_say(final, entry, finalists_for)
    assert_rebuilt(event, final, entry, finalists_for, before, final.id, 1)


def test_a_change_while_the_final_races_rebuilds_it_with_racing_off(
    timed_intermission,
):
    """Start turns racing on for the final's first heat, which has no
    result yet. The final's chart is made again under it, so racing goes
    off and its first heat is current again."""
    event, round1, final, entry, finalists_for = timed_intermission
    before = roster(final)
    assert start_waiting_step(event) == final
    assert_current(event, final.id, 1, racing=True)
    outside = outside_the_cut(round1, before)
    assert import_result(event, faster_line(round1, outside)) is None
    assert_rebuilt(event, final, entry, finalists_for, before, final.id, 1)


def test_a_change_with_the_intermission_closed_keeps_a_final_that_has_a_result(
    timed_intermission,
):
    """The rebuild is for a final with no heat run (D136)."""
    event, round1, final, entry, finalists_for = timed_intermission
    before, chart = roster(final), chart_ids(final)
    assert start_waiting_step(event) == final
    record_timer_result(event, slow(final, 1), finalists_for=finalists_for)
    assert HeatLane.objects.filter(round=final).filter(HAS_RESULT).exists()
    heat = last_heat(round1)
    select_round_1_heat(event, round1, heat)
    record_manual_result(event, fast(round1, heat), finalists_for=finalists_for)
    assert standings_say(final, entry, finalists_for) != before
    assert roster(final) == before
    assert chart_ids(final) == chart


def test_a_delete_of_every_result_rebuilds_the_final(closed_intermission):
    """`result.delete` with no round clears every round, the final's too."""
    event, round1, final, entry, finalists_for = closed_intermission
    before = roster(final)
    assert delete_results(event, finalists_for=finalists_for) is None
    assert_rebuilt(event, final, entry, finalists_for, before, None, None)


def test_a_delete_of_every_result_rebuilds_the_final_while_it_waits(intermission):
    event, round1, final, entry, finalists_for = intermission
    before = roster(final)
    assert delete_results(event, finalists_for=finalists_for) is None
    assert roster(final) == standings_say(final, entry, finalists_for) != before
    assert state_of_event(event).waiting_entry_id == entry.id


def test_a_change_to_another_round_after_a_rerun_keeps_the_final(
    closed_intermission,
):
    """A rerun rebuilds nothing, and a change to the final's own results
    rebuilds no final from Round 1's standings."""
    event, round1, final, entry, finalists_for = closed_intermission
    before, chart = roster(final), chart_ids(final)
    select_round_1_heat(event, round1, last_heat(round1))
    assert rerun(event, "current") is None
    assert roster(final) == before
    assert delete_results(event, final, finalists_for=finalists_for) is None
    assert standings_say(final, entry, finalists_for) != before
    assert roster(final) == before
    assert chart_ids(final) == chart


def test_a_change_to_the_final_keeps_the_next_groups_round(closed_intermission):
    """The step after the final is Outlaw round 1, which takes no racer
    from the final, so the racer taken off its roster by hand stays off."""
    event, round1, final, entry, finalists_for = closed_intermission
    outlaw = Round.objects.get(group__name="Outlaw", number=1)
    RosterEntry.objects.filter(round=outlaw).order_by("id").first().delete()
    kept = roster(outlaw)
    after = PlaylistEntry.objects.filter(seq__gt=entry.seq).order_by("seq").first()
    assert after.group_id == outlaw.group_id
    assert after.round_number == 1
    chart = chart_ids(outlaw)
    assert delete_results(event, final, finalists_for=finalists_for) is None
    assert roster(outlaw) == kept
    assert chart_ids(outlaw) == chart


@pytest.mark.parametrize("number", [2, 3])
def test_an_imported_line_on_a_round_no_step_feeds_from_keeps_the_final(
    closed_intermission, number
):
    """Outlaw round 2 is the playlist's last step, and Outlaw round 3 has
    no step. The import makes the round."""
    event, round1, final, entry, finalists_for = closed_intermission
    before, chart = roster(final), chart_ids(final)
    steps = PlaylistEntry.objects.filter(event=event, group__name="Outlaw")
    last = PlaylistEntry.objects.filter(event=event).order_by("seq").last()
    assert last.group.name == "Outlaw"
    assert last.round_number == 2
    assert not steps.filter(round_number=3).exists()
    racer = Racer.objects.filter(group__name="Outlaw").first()
    line = {
        **faster_line(round1, racer),
        "class": "Outlaw",
        "round": str(number),
        "heat": "1",
    }
    assert import_result(event, line) is None
    assert Round.objects.filter(group__name="Outlaw", number=number).exists()
    assert roster(final) == before
    assert chart_ids(final) == chart


def test_a_change_with_the_intermission_closed_keeps_a_final_that_does_not_wait():
    """D141 rebuilds a waiting final. A step that does not wait keeps its
    roster, as PHP does (`website/inc/playlist.inc:85`)."""
    event, round1, final, entry, finalists_for = timed(waits=False)
    before = roster(final)
    assert delete_results(event, round1, 1, finalists_for=finalists_for) is None
    assert roster(final) == before != standings_say(final, entry, finalists_for)


def test_a_change_before_round_1_ends_makes_no_final():
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=False)
    event = replay.event
    round1 = start_playlist(event, finalists_for)
    record_timer_result(event, slow(round1, 1), finalists_for=finalists_for)
    assert delete_results(event, round1, 1, finalists_for=finalists_for) is None
    assert not Round.objects.filter(group=round1.group, number=2).exists()
