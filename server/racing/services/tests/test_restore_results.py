"""`results.discard_heat` and `results.restore_results`, the dashboard's
Discard and its Undo (#41), and `lanes.rounds_to_close`, which its lane
sheet names (D45)."""

import pytest

from core.tests.factories import EventFactory
from racing.models import HeatLane
from racing.services import lanes, results
from ui.tests.race import current, race_setup, run_heat, scheduled

pytestmark = pytest.mark.django_db


@pytest.fixture
def wolves():
    event = EventFactory()
    return race_setup(event)["Wolves"]


def times(round, heat):
    rows = HeatLane.objects.filter(round=round, heat=heat).order_by("lane")
    return [None if r.finish_time is None else float(r.finish_time) for r in rows]


def test_a_restore_brings_back_what_a_discard_cleared(wolves):
    event = wolves.event
    scheduled(wolves)
    run_heat(event, (2.1, 2.2, 2.3, 2.4))
    found = results.heat_times(event, wolves, 1)
    assert results.delete_results(event, wolves, 1) is None
    assert times(wolves, 1) == [None] * 4

    assert results.restore_results(event, wolves, 1, found) is None
    assert times(wolves, 1) == [2.1, 2.2, 2.3, 2.4]
    assert (
        HeatLane.objects.filter(round=wolves, heat=1, completed__isnull=True).count()
        == 0
    )
    assert current(event)[:2] == (wolves.pk, 2)


def test_a_restore_keeps_a_lane_that_has_a_result_again(wolves):
    event = wolves.event
    scheduled(wolves)
    run_heat(event, (2.1, 2.2, 2.3, 2.4))
    found = results.heat_times(event, wolves, 1)
    results.delete_results(event, wolves, 1)
    HeatLane.objects.filter(round=wolves, heat=1, lane=1).update(finish_time=3.5)

    results.restore_results(event, wolves, 1, found)
    assert times(wolves, 1) == [3.5, 2.2, 2.3, 2.4]


def test_rounds_to_close_lists_rounds_with_heats_left(wolves):
    event = wolves.event
    assert lanes.rounds_to_close(event) == []
    scheduled(wolves)
    assert lanes.rounds_to_close(event) == [wolves]


def test_discard_heat_returns_what_it_cleared(wolves):
    event = wolves.event
    scheduled(wolves)
    run_heat(event, (2.1, 2.2, 2.3, 2.4))
    failure, cleared = results.discard_heat(event, wolves, 1)
    assert failure is None
    assert [(t.lane, t.time) for t in cleared] == [
        (1, 2.1),
        (2, 2.2),
        (3, 2.3),
        (4, 2.4),
    ]
    assert times(wolves, 1) == [None] * 4


def final_raced(wolves):
    """Round 1 of Wolves finished, and a heat of its top 3 final raced, so
    round 1's results lock for all but the race coordinator (D32)."""
    from racing.services import rounds
    from racing.services.rounds import FollowOn

    event = wolves.event
    scheduled(wolves)
    while current(event)[2]:
        run_heat(event)
    final = rounds.new_round_roster(FollowOn(wolves), 3).round
    scheduled(final)
    run_heat(event)
    return final


def test_restore_and_discard_refuse_a_locked_round_without_control_race(wolves):
    event = wolves.event
    final_raced(wolves)
    found = results.heat_times(event, wolves, 1)
    failure, cleared = results.discard_heat(event, wolves, 1, control_race=False)
    assert failure.code == "results-locked" and cleared == []
    failure, _ = results.discard_heat(event, wolves, 1)
    assert failure is None
    refused = results.restore_results(event, wolves, 1, found, control_race=False)
    assert refused.code == "results-locked"
    assert times(wolves, 1) == [None] * len(found)


def test_restore_rebuilds_a_waiting_final(wolves, monkeypatch):
    event = wolves.event
    scheduled(wolves)
    run_heat(event, (2.1, 2.2, 2.3, 2.4))
    _, found = results.discard_heat(event, wolves, 1)
    rebuilt = []
    monkeypatch.setattr(
        results,
        "rebuild_waiting_final",
        lambda event, round_ids, ended, finalists_for: rebuilt.append(round_ids),
    )
    results.restore_results(event, wolves, 1, found)
    assert rebuilt == [[wolves.pk]]
    results.restore_results(event, wolves, 1, found)
    assert rebuilt == [[wolves.pk]], "nothing restored, nothing rebuilt"
