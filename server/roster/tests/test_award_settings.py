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
