"""The racing groups editor's settings services (#43): the pack standings
rule, both halves under the lock, and the per-field form."""

import pytest

from core.models import FullFieldRule, RaceSettings
from core.settings_form import (
    PackStandingsRefused,
    apply_group_form,
    set_pack_standings,
)
from roster.services import groups
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    replay = Replay()
    for name in ("Lions", "Tigers"):
        replay.import_racer(name, "Racer", name)
    groups.form_groups_by_rule(replay.event, "custom")
    return replay.event


def settings(event):
    return RaceSettings.objects.get(event=event)


def post(**fields):
    return {"action": "settings.write", **fields}


def test_dont_calculate_is_refused_while_pack_trophies_exist(event):
    RaceSettings.objects.filter(event=event).update(n_pack_trophies=3)
    with pytest.raises(PackStandingsRefused):
        set_pack_standings(event, "-1")
    assert settings(event).full_field_rule == FullFieldRule.STANDARD


def test_pack_standings_take_normal_none_or_an_aggregate(event):
    RaceSettings.objects.filter(event=event).update(n_pack_trophies=0)
    set_pack_standings(event, "-1")
    assert settings(event).full_field_rule == FullFieldRule.NONE
    lions = groups.add_group(event, "Lions2")
    cats = groups.add_group(
        event,
        "Cats",
        constituents=[lions, groups.find_or_create_group(event.id, "Tigers")],
    )
    set_pack_standings(event, str(cats.pk))
    assert settings(event).full_field_group_id == cats.pk
    set_pack_standings(event, "0")
    assert settings(event).full_field_rule == FullFieldRule.STANDARD


@pytest.mark.parametrize("value", ["", "x", "7", "999999"])
def test_pack_standings_refuse_anything_but_a_choice(event, value):
    with pytest.raises(PackStandingsRefused):
        set_pack_standings(event, value)


def test_an_organic_group_is_no_pack_standings_choice(event):
    lions = groups.find_or_create_group(event.id, "Lions")
    with pytest.raises(PackStandingsRefused):
        set_pack_standings(event, str(lions.pk))


def test_pack_trophies_turn_dont_calculate_back_to_normal(event):
    RaceSettings.objects.filter(event=event).update(n_pack_trophies=0)
    set_pack_standings(event, "-1")
    assert apply_group_form(event, post(**{"n-pack-trophies": "2"})).failure is None
    found = settings(event)
    assert (found.n_pack_trophies, found.full_field_rule) == (2, "standard")


def test_zero_pack_trophies_keep_dont_calculate(event):
    RaceSettings.objects.filter(event=event).update(n_pack_trophies=0)
    set_pack_standings(event, "-1")
    apply_group_form(event, post(**{"n-pack-trophies": "0"}))
    assert settings(event).full_field_rule == FullFieldRule.NONE


def test_a_trophy_count_that_is_not_a_number_never_raises(event):
    """PHP's intval: `abc` is 0, so the pack rule reads a number."""
    RaceSettings.objects.filter(event=event).update(n_pack_trophies=0)
    set_pack_standings(event, "-1")
    result = apply_group_form(event, post(**{"n-pack-trophies": "abc"}))
    assert result.failure is None
    assert settings(event).full_field_rule == FullFieldRule.NONE


def test_a_label_and_the_switch(event):
    apply_group_form(event, post(**{"partition-label": "Patrol"}))
    apply_group_form(
        event, post(**{"do-use-subgroups": "1", "do-use-subgroups-checkbox": "1"})
    )
    found = settings(event)
    assert (found.partition_label, found.use_subgroups) == ("Patrol", True)
