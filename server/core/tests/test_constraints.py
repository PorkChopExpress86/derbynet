"""Constraints from #12 that no `query.database.check` tag names."""

import pytest
from django.db import connection

from awards.tests.factories import BallotAwardFactory, VoteFactory
from core.models import Event, RaceSettings, RaceState, SiteState
from core.tests.db import refused
from core.tests.factories import EventFactory
from kiosks.models import ReplayStatus
from kiosks.tests.factories import KioskFactory, SceneKioskFactory
from racing.models import TimerStatus
from racing.tests.factories import RoundFactory
from roster.models import Racer
from roster.tests.factories import (
    GroupConstituentFactory,
    GroupFactory,
    PartitionFactory,
    RacerFactory,
    SubgroupFactory,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("model", [RaceSettings, RaceState, TimerStatus, ReplayStatus])
def test_an_event_has_one_row_of_each_per_event_model(model):
    event = EventFactory()
    with refused():
        model.objects.create(event=event)


def test_site_state_has_one_row():
    event = EventFactory()
    with refused():
        SiteState.objects.create(id=2, active_event=event)


def test_event_slugs_are_unique():
    EventFactory()
    with refused():
        Event.objects.create(slug=Event.objects.get().slug, name="Copy")


def test_group_names_are_unique_per_event_ignoring_ascii_case():
    event = EventFactory()
    GroupFactory(event=event, name="Bears")
    with refused():
        GroupFactory(event=event, name="bears")


def test_group_names_fold_unicode_case_only_on_postgres():
    event = EventFactory()
    GroupFactory(event=event, name="École")
    if connection.vendor == "postgresql":
        with refused():
            GroupFactory(event=event, name="école")
    else:
        GroupFactory(event=event, name="école")


def test_partition_names_are_unique_per_event():
    partition = PartitionFactory(name="Bears")
    with refused():
        PartitionFactory(subgroup=partition.subgroup, name="Bears")


def test_partition_names_are_case_sensitive_as_in_php():
    partition = PartitionFactory(name="Bears")
    PartitionFactory(subgroup=partition.subgroup, name="bears")


def test_a_constituent_joins_an_aggregate_once():
    link = GroupConstituentFactory()
    with refused():
        GroupConstituentFactory(aggregate=link.aggregate, constituent=link.constituent)


def test_a_group_has_one_round_of_each_number():
    round1 = RoundFactory(number=1)
    with refused():
        RoundFactory(group=round1.group, number=1)


def test_round_numbers_start_at_1():
    with refused():
        RoundFactory(number=0)


def test_a_scene_has_one_page_per_kiosk_name():
    page = SceneKioskFactory(kiosk_name="Main")
    with refused():
        SceneKioskFactory(scene=page.scene, kiosk_name="Main")


def test_kiosk_addresses_are_unique_per_event():
    kiosk = KioskFactory(address="10.0.0.5")
    KioskFactory(address="10.0.0.5")  # another event
    with refused():
        KioskFactory(event=kiosk.event, address="10.0.0.5")


def test_a_voter_votes_for_a_racer_in_an_award_once():
    vote = VoteFactory()
    with refused():
        VoteFactory(voter=vote.voter, award=vote.award, racer=vote.racer)


def test_an_award_has_one_ballot_entry():
    ballot = BallotAwardFactory()
    with refused():
        BallotAwardFactory(award=ballot.award)


def test_full_field_group_is_set_exactly_when_the_rule_is_group():
    settings = RaceSettings.for_event(EventFactory())
    group = GroupFactory(event=settings.event)
    settings.full_field_rule = "group"
    settings.full_field_group = group
    settings.save()

    with refused():
        RaceSettings.objects.filter(pk=settings.pk).update(full_field_group=None)
    with refused():
        RaceSettings.objects.filter(pk=settings.pk).update(full_field_rule="none")


@pytest.mark.parametrize("path", ["", "ann.jpg", "0-cropped/ann.jpg", "a.b c.png"])
def test_photo_paths_accept_a_repository_relative_shape(path):
    racer = RacerFactory()
    Racer.objects.filter(pk=racer.pk).update(headshot=path, car_photo=path)


@pytest.mark.parametrize(
    "path",
    [
        "/var/photos/ann.jpg",
        "racers/ann.jpg",
        "0-cropped/",
        "1-cropped/ann.jpg",
        "..",
        "0-cropped/..",
        "C:\\photos\\..\\x/ann.jpg",
    ],
)
@pytest.mark.parametrize("field", ["headshot", "car_photo"])
def test_photo_paths_refuse_other_shapes(path, field):
    racer = RacerFactory()
    with refused():
        Racer.objects.filter(pk=racer.pk).update(**{field: path})


def test_subgroup_names_may_repeat_as_in_php():
    subgroup = SubgroupFactory(name="Tigers")
    SubgroupFactory(group=subgroup.group, name="Tigers")
