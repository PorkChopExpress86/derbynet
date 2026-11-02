"""`set_award_settings`: the two award settings of D33, which the race
dashboard sets for a racer added late (#41)."""

import pytest

from core.tests.factories import EventFactory
from roster.services.racers import set_award_settings
from ui.tests.race import add_racers

pytestmark = pytest.mark.django_db


def test_award_settings_change_one_at_a_time():
    racer = add_racers(EventFactory(), "Wolves", 1, 101)[0]
    set_award_settings(racer, no_speed_awards=True)
    racer.refresh_from_db()
    assert (racer.exclude, racer.not_eligible_for_speed_awards) == (False, True)
    set_award_settings(racer, exclude=True, no_speed_awards=False)
    racer.refresh_from_db()
    assert (racer.exclude, racer.not_eligible_for_speed_awards) == (True, False)


# The award settings save with the racer, in one transaction (#42 review).


def _failing_awards(*args, **kwargs):
    raise RuntimeError("award settings failed")


def test_a_new_racer_and_its_award_settings_save_together(monkeypatch):
    from roster.models import Racer
    from roster.services import racers

    event = EventFactory()
    monkeypatch.setattr(racers, "_store_award_settings", _failing_awards)
    with pytest.raises(RuntimeError):
        racers.insert_new_racer(
            event, first_name="A", last_name="B", car_number=101, no_speed_awards=True
        )
    assert not Racer.objects.filter(event=event).exists()


def test_an_edit_and_its_award_settings_save_together(monkeypatch):
    from roster.services import racers

    racer = add_racers(EventFactory(), "Wolves", 1, 101)[0]
    monkeypatch.setattr(racers, "_store_award_settings", _failing_awards)
    with pytest.raises(RuntimeError):
        racers.edit_racer(
            racer, racers.Edit(first_name="Changed", no_speed_awards=True)
        )
    racer.refresh_from_db()
    assert racer.first_name == "Wolves1"


def test_add_and_edit_set_both_award_settings():
    from roster.services import racers

    event = EventFactory()
    racer = racers.insert_new_racer(
        event,
        first_name="A",
        last_name="B",
        car_number=101,
        exclude=True,
        no_speed_awards=True,
    ).racer
    racer.refresh_from_db()
    assert (racer.exclude, racer.not_eligible_for_speed_awards) == (True, True)
    racers.edit_racer(racer, racers.Edit(exclude=False, no_speed_awards=False))
    racer.refresh_from_db()
    assert (racer.exclude, racer.not_eligible_for_speed_awards) == (False, False)
