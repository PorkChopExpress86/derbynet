import pytest

from racing.models import AuditEvent
from racing.services.heats import reinstate, rerun, select_heat
from racing.services.results import record_manual_result, record_timer_result
from racing.services.tests.race import fields, setup_race

pytestmark = pytest.mark.django_db

Action = AuditEvent.Action


@pytest.fixture
def race():
    replay, round = setup_race()
    select_heat(replay.event, round_id=round.id, now_racing=True)
    return replay.event, round


def audited(event):
    return [
        (e.action, e.round_id, e.heat, e.result_of_id)
        for e in AuditEvent.objects.filter(event=event).order_by("id")
    ]


def test_a_timer_result_is_audited_with_its_lanes_as_posted(race):
    event, round = race
    record_timer_result(event, fields("lane1=3.1&lane2=---&place3=2"))
    (row,) = AuditEvent.objects.filter(event=event)
    assert (row.action, row.round_id, row.heat) == (
        Action.HEAT_RESULT_FROM_TIMER,
        round.id,
        1,
    )
    assert row.detail == {"non-numeric": {"lane2": "---"}}
    assert sorted(
        row.lane_results.values_list("lane", "finish_time", "finish_place")
    ) == [(1, 3.1, None), (2, None, None), (3, None, 2)]


def test_a_refused_timer_result_is_audited_without_a_heat(race):
    event, _ = race
    select_heat(event, now_racing=False)
    record_timer_result(event, fields("lane1=3.1"))
    assert audited(event) == [(Action.TIMER_RESULT_REFUSED, None, None, None)]


def test_discards_and_reinstates_point_at_the_result_they_change(race):
    event, round = race
    record_timer_result(event, fields("lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4"))
    rerun(event, "last")
    reinstate(event)
    select_heat(event, heat=1)
    rerun(event, "current")
    finish = AuditEvent.objects.get(event=event, action=Action.HEAT_RESULT_FROM_TIMER)
    assert audited(event) == [
        (Action.HEAT_RESULT_FROM_TIMER, round.id, 1, None),
        (Action.HEAT_RESULT_DISCARDED, round.id, 1, finish.id),
        (Action.HEAT_RESULT_REINSTATED, round.id, 1, finish.id),
        (Action.HEAT_RESULT_DISCARDED, round.id, 1, finish.id),
    ]


def test_a_manual_result_discards_the_one_it_replaces(race):
    event, round = race
    select_heat(event, now_racing=False)
    record_manual_result(event, fields("lane1=3.1"))
    record_manual_result(event, fields("lane1=3.0"))
    first = AuditEvent.objects.filter(event=event).order_by("id").first()
    assert audited(event) == [
        (Action.HEAT_MANUALLY_ENTERED, round.id, 1, None),
        (Action.HEAT_RESULT_DISCARDED, round.id, 1, first.id),
        (Action.HEAT_MANUALLY_ENTERED, round.id, 1, None),
    ]
