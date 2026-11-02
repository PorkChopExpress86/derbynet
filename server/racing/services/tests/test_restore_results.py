"""`results.restore_results`, the Undo of the dashboard's discard (#41), and
`lanes.rounds_to_close`, which its lane sheet names (D45)."""

import pytest

from core.tests.factories import EventFactory
from racing.models import HeatLane
from racing.services import lanes, results
from roster.services.racers import set_award_settings
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


def test_award_settings_change_one_at_a_time(wolves):
    racer = wolves.roster.first().racer
    set_award_settings(racer, no_speed_awards=True)
    racer.refresh_from_db()
    assert (racer.exclude, racer.not_eligible_for_speed_awards) == (False, True)
    set_award_settings(racer, exclude=True, no_speed_awards=False)
    racer.refresh_from_db()
    assert (racer.exclude, racer.not_eligible_for_speed_awards) == (True, False)
