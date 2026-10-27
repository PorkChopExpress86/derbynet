"""D27 through the result services: a rerun, a reinstate, a manual result,
a result delete, or the new timer result after a rerun, on the round that
just ended, rebuilds the waiting final from the standings in the same
transaction, until a heat of the final has a result (#20, "Race-day flow")."""

import pytest
from django.utils import timezone

from core.models import RaceState
from racing.models import HeatLane, Round
from racing.services.heats import reinstate, rerun
from racing.services.playlist import round_ended, start_playlist
from racing.services.results import (
    delete_results,
    record_manual_result,
    record_timer_result,
)
from racing.services.tests.race import fields
from racing.tests.test_race_day import race, state, two_step_playlist

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
    assert roster(final) == standings_say(final, entry, finalists_for)
    after_rerun = state_of_event(event)
    assert (after_rerun.current_round_id, after_rerun.current_heat) == (round1.id, heat)
    assert after_rerun.racing, "the rebuild leaves the rerun heat racing"

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
    before = list(HeatLane.objects.filter(round=final).values_list("id", flat=True))
    delete_results(event, final)
    assert list(HeatLane.objects.filter(round=final).values_list("id", flat=True)) == (
        before
    )


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
