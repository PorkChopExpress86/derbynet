"""Adding a car to a round in progress from check-in (D36, #29)."""

import pytest

from accounts.permissions import Perm, granted_for
from core.models import RaceState
from legacy_api.registry import REGISTRY
from racing.models import HAS_RESULT, HeatLane, RosterEntry
from racing.services.tests.race import setup_race
from roster.models import Racer
from roster.services import checkin, racers
from roster.services.checkin import (
    RESCHEDULE_PERMISSION,
    CheckInFailure,
    add_to_round_in_progress,
    leave_out_of_round_in_progress,
    round_in_progress_for,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def race():
    """Wolves round 1 of six cars on four lanes, scheduled."""
    replay, round = setup_race(racers=6, lanes=4)
    return replay.event, round


def run_first_heat(round):
    HeatLane.objects.filter(round=round, heat=1).update(finish_time=2.5)


def late_racer(event, car):
    racer = racers.insert_new_racer(
        event,
        first_name="Late",
        last_name=str(car),
        car_number=car,
        partition_name="Wolves",
    ).racer
    racers.pass_racer(racer, True)
    return Racer.objects.get(pk=racer.pk)


def bits(client, role, password):
    client.post(
        "/action.php", {"action": "role.login", "name": role, "password": password}
    )
    return granted_for(client.session)


def crew(client):
    return bits(client, "RaceCrew", "murphy")


def coordinator(client):
    return bits(client, "RaceCoordinator", "doyourbest")


def heats_of(racer, round):
    return HeatLane.objects.filter(round=round, racer=racer).count()


def test_no_round_is_in_progress_before_it_starts(race):
    event, round = race
    racer = late_racer(event, 201)

    assert round_in_progress_for(racer) is None


def test_a_raced_heat_or_the_current_round_starts_it(race):
    event, round = race
    racer = late_racer(event, 201)

    RaceState.objects.filter(event=event).update(current_round=round, current_heat=1)
    assert round_in_progress_for(racer) == round
    RaceState.objects.filter(event=event).update(current_round=None)
    run_first_heat(round)
    assert round_in_progress_for(racer) == round


def test_a_racer_with_heats_or_a_finished_round_gets_no_question(race):
    event, round = race
    run_first_heat(round)
    scheduled = Racer.objects.filter(event=event, heat_lanes__round=round).first()

    assert round_in_progress_for(scheduled) is None
    HeatLane.objects.filter(round=round).update(finish_time=2.5)
    assert round_in_progress_for(late_racer(event, 201)) is None


def test_the_race_crew_adds_a_car_that_just_passed(client, race):
    event, round = race
    run_first_heat(round)
    racer = late_racer(event, 201)
    raced = list(HeatLane.objects.filter(round=round).filter(HAS_RESULT).values())

    added = add_to_round_in_progress(racer, crew(client))

    assert added.round == round
    assert heats_of(racer, round) > 0
    assert list(HeatLane.objects.filter(round=round).filter(HAS_RESULT).values()) == (
        raced
    )


def test_a_second_add_is_refused(client, race):
    event, round = race
    run_first_heat(round)
    racer = late_racer(event, 201)
    add_to_round_in_progress(racer, crew(client))

    with pytest.raises(CheckInFailure) as refused:
        add_to_round_in_progress(racer, crew(client))

    assert refused.value.code == checkin.Failure.ALREADY_SCHEDULED


def test_a_racer_not_checked_in_is_refused(client, race):
    event, round = race
    run_first_heat(round)
    racer = late_racer(event, 201)
    racers.pass_racer(racer, False)

    with pytest.raises(CheckInFailure) as refused:
        add_to_round_in_progress(racer, crew(client))

    assert refused.value.code == checkin.Failure.NOT_CHECKED_IN


def test_after_no_only_the_race_coordinator_adds_the_car(client, race):
    event, round = race
    run_first_heat(round)
    racer = late_racer(event, 201)
    crew_bits = crew(client)

    assert leave_out_of_round_in_progress(racer, crew_bits) == round
    assert RosterEntry.objects.get(round=round, racer=racer).left_out_at is not None
    with pytest.raises(CheckInFailure) as refused:
        add_to_round_in_progress(racer, crew_bits)
    assert refused.value.code == checkin.Failure.NOT_AUTHORIZED
    assert heats_of(racer, round) == 0

    add_to_round_in_progress(racer, coordinator(client))

    assert heats_of(racer, round) > 0


def test_schedule_reschedule_stays_with_the_race_coordinator(client, race):
    """D16: the race crew's check-in Yes is the only reschedule it may do.
    `schedule.reschedule` itself is registered by #31."""
    assert not crew(client) & RESCHEDULE_PERMISSION
    assert coordinator(client) & RESCHEDULE_PERMISSION
    assert RESCHEDULE_PERMISSION == Perm.CONTROL_RACE
    endpoint = REGISTRY.get("action", "schedule.reschedule")
    if endpoint is not None:
        assert endpoint.permission == RESCHEDULE_PERMISSION


def test_a_session_without_check_in_cannot_answer(race):
    event, round = race
    run_first_heat(round)
    racer = late_racer(event, 201)

    with pytest.raises(CheckInFailure):
        add_to_round_in_progress(racer, int(Perm.PHOTO_UPLOAD))
    with pytest.raises(CheckInFailure):
        leave_out_of_round_in_progress(racer, int(Perm.PHOTO_UPLOAD))
