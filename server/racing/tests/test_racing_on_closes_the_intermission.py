"""Racing never runs with the intermission open (#514). When the playlist
turns racing on for a step that does not wait, the intermission closes, and
the final races on as a normal round (D136).

No page reaches the state these tests build. The dashboard's Next and
`heat.select` go through `select_heat`, whose `Select` row closes the
intermission before the playlist runs. Only `heats.advance`, which only
tests call, ends a round with the intermission still open."""

import pytest

from racing.models import HeatLane, PlaylistEntry, Round
from racing.services.heats import advance, select_heat
from racing.services.playlist import set_step_waits, start_playlist
from racing.services.results import record_manual_result
from racing.tests.test_race_day import one_finalist, two_step_playlist
from racing.tests.test_result_changes_rebuild_the_final import (
    fast,
    last_heat,
    race_by_timer,
    state_of_event,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def switched_off_during_the_intermission():
    """Scouts round 1 ends with a cut too short for a final, so the final
    waits with no chart and round 1's last heat stays current (D28). A
    manual result on that heat, with the cut put right, builds the final
    (D27). Then the step's "Wait for operator" switch goes off. The step
    before it says `continue_racing`."""
    replay, finalists_for = two_step_playlist(waits=True, continue_racing=True)
    event = replay.event
    short = one_finalist(replay)
    round1 = start_playlist(event, short)
    assert (round1.group.name, round1.number) == ("Scouts", 1)
    race_by_timer(event, round1, short)
    entry = PlaylistEntry.objects.get(group=round1.group, round_number=2)
    final = Round.objects.get(group=round1.group, number=2)
    heat = last_heat(round1)
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert now.current_round_id == round1.id
    assert now.current_heat == heat
    assert not now.racing
    assert not HeatLane.objects.filter(round=final).exists()
    record_manual_result(event, fast(round1, heat), finalists_for=finalists_for)
    assert HeatLane.objects.filter(round=final).exists()
    set_step_waits(event, entry.id, False)
    now = state_of_event(event)
    assert now.waiting_entry_id == entry.id
    assert now.ended_round_id == round1.id
    assert now.current_round_id == round1.id
    assert now.current_heat == heat
    return event, final, finalists_for


def test_the_playlist_turning_racing_on_closes_the_intermission(
    switched_off_during_the_intermission,
):
    """`heats.advance` to Next on round 1's last heat ends the round again
    with the intermission open. The final no longer waits, so the playlist
    selects its first heat and turns racing on."""
    event, final, finalists_for = switched_off_during_the_intermission
    advance(event, "next", report=False, finalists_for=finalists_for)
    now = state_of_event(event)
    assert now.racing
    assert now.current_round_id == final.id
    assert now.current_heat == 1
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None


def test_next_from_a_page_closes_the_intermission_before_the_playlist_runs(
    switched_off_during_the_intermission,
):
    """What the dashboard's Next and `heat.select` do from the same state."""
    event, final, finalists_for = switched_off_during_the_intermission
    select_heat(event, heat="next", finalists_for=finalists_for)
    now = state_of_event(event)
    assert now.racing
    assert now.current_round_id == final.id
    assert now.current_heat == 1
    assert now.waiting_entry_id is None
    assert now.ended_round_id is None
