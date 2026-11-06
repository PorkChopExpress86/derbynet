"""The weigh-in service (#70, D8, D54): grams stored, the partition's unit
at the edge, the refusals, and the over-the-maximum warning."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from core.tests.factories import EventFactory
from roster.models import Partition, Racer
from roster.services import weigh_in
from roster.services.racers import insert_new_racer
from roster.services.weigh_in import (
    Code,
    Entry,
    WeighInFailure,
    record_weigh_in,
    set_partition_weigh_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    return EventFactory()


def racer_in(event, partition_name, mode, unit="", max_weight=None):
    racer = insert_new_racer(
        event, first_name="Sam", last_name="Weigh", partition_name=partition_name
    ).racer
    set_partition_weigh_in(racer.partition, mode, unit, max_weight)
    return Racer.objects.select_related("partition").get(pk=racer.pk)


@pytest.mark.parametrize(
    ("unit", "typed", "grams", "shown"),
    [
        ("oz", "5.0", Decimal("141.75"), "5.0 oz"),
        ("g", "141.75", Decimal("141.75"), "141.75 g"),
        ("lb", "0.3125", Decimal("141.75"), "0.3125 lb"),
        ("oz", "4.98", Decimal("141.18"), "4.98 oz"),
        ("g", "142", Decimal("142.00"), "142.0 g"),
    ],
)
def test_a_weight_is_stored_in_grams_and_shown_in_the_partitions_unit(
    event, unit, typed, grams, shown
):
    racer = racer_in(event, "Outlaws", "recorded", unit)
    recorded = record_weigh_in(racer, weight=typed)
    racer.refresh_from_db()
    assert racer.weight_grams == grams == recorded.weight_grams
    assert racer.weigh_in_passed is True and racer.weighed_at is not None
    assert weigh_in.status(racer).weight == shown
    assert recorded.warning == ""


def test_a_new_unit_changes_only_how_a_stored_weight_shows(event):
    racer = racer_in(event, "Outlaws", "recorded", "oz")
    record_weigh_in(racer, weight="5")
    set_partition_weigh_in(racer.partition, "recorded", "g")
    racer = Racer.objects.select_related("partition").get(pk=racer.pk)
    assert racer.weight_grams == Decimal("141.75")
    assert weigh_in.status(racer).weight == "141.75 g"


def test_a_weight_in_another_unit_is_converted(event):
    racer = racer_in(event, "Outlaws", "recorded", "oz")
    record_weigh_in(racer, weight="141.75", unit="g")
    racer.refresh_from_db()
    assert racer.weight_grams == Decimal("141.75")


def test_a_pass_fail_partition_refuses_a_weight(event):
    racer = racer_in(event, "Scouts", "pass_fail")
    with pytest.raises(WeighInFailure) as refused:
        record_weigh_in(racer, passed=True, weight="5")
    assert refused.value.code == Code.WEIGHT_NOT_ASKED
    with pytest.raises(WeighInFailure) as refused:
        record_weigh_in(racer)
    assert refused.value.code == Code.PASS_NEEDED
    racer.refresh_from_db()
    assert (racer.weigh_in_passed, racer.weight_grams, racer.weighed_at) == (
        None,
        None,
        None,
    )


def test_a_recorded_partition_refuses_a_missing_weight(event):
    racer = racer_in(event, "Outlaws", "recorded", "oz")
    for missing in (None, ""):
        with pytest.raises(WeighInFailure) as refused:
            record_weigh_in(racer, weight=missing)
        assert refused.value.code == Code.WEIGHT_NEEDED
    with pytest.raises(WeighInFailure) as refused:
        record_weigh_in(racer, passed=True)
    assert refused.value.code == Code.PASS_NOT_ASKED
    racer.refresh_from_db()
    assert racer.weighed_at is None


@pytest.mark.parametrize("typed", ["abc", "0", "-1", "nan", "inf", "1e9"])
def test_a_weight_that_is_no_positive_number_is_refused(event, typed):
    racer = racer_in(event, "Outlaws", "recorded", "g")
    with pytest.raises(WeighInFailure) as refused:
        record_weigh_in(racer, weight=typed)
    assert refused.value.code == Code.BAD_WEIGHT


def test_a_partition_with_no_weigh_in_refuses_one(event):
    racer = racer_in(event, "Tigers", "none")
    with pytest.raises(WeighInFailure) as refused:
        record_weigh_in(racer, passed=True)
    assert refused.value.code == Code.NO_WEIGH_IN
    assert weigh_in.status(racer) is None


def test_pass_and_fail_are_recorded_with_no_weight(event):
    racer = racer_in(event, "Scouts", "pass_fail")
    assert record_weigh_in(racer, passed=False).passed is False
    racer.refresh_from_db()
    status = weigh_in.status(racer)
    assert (status.weighed, status.failed, racer.weight_grams) == (True, True, None)
    record_weigh_in(racer, passed=True)
    racer.refresh_from_db()
    assert weigh_in.status(racer).failed is False


def test_a_weight_over_the_maximum_saves_and_warns(event):
    racer = racer_in(event, "Outlaws", "recorded", "oz", max_weight="5.0")
    recorded = record_weigh_in(racer, weight="5.2")
    assert recorded.warning == "5.2 oz is over the 5.0 oz maximum for Outlaws."
    assert recorded.passed is False
    racer.refresh_from_db()
    assert racer.weight_grams == Decimal("147.42")
    status = weigh_in.status(racer)
    assert (status.over, status.failed, status.maximum) == (True, True, "5.0 oz")

    at_the_maximum = record_weigh_in(racer, weight="5.0")
    assert (at_the_maximum.warning, at_the_maximum.passed) == ("", True)


def test_a_new_racer_can_be_weighed_in_the_same_transaction(event):
    partition = racer_in(event, "Outlaws", "recorded", "oz", "5").partition
    new = insert_new_racer(
        event,
        first_name="Nora",
        last_name="Heavy",
        partition=partition,
        weigh_in=Entry(weight="5.5"),
    )
    new.racer.refresh_from_db()
    assert new.racer.weight_grams == Decimal("155.92")
    assert new.warnings == ["5.5 oz is over the 5.0 oz maximum for Outlaws."]


def test_a_refused_weigh_in_adds_no_racer(event):
    partition = racer_in(event, "Scouts", "pass_fail").partition
    before = Racer.objects.count()
    with pytest.raises(WeighInFailure):
        insert_new_racer(
            event,
            first_name="Nora",
            last_name="Heavy",
            partition=partition,
            weigh_in=Entry(weight="5"),
        )
    assert Racer.objects.count() == before


def test_the_partition_setting_needs_a_unit_for_a_weight(event):
    partition = racer_in(event, "Outlaws", "none").partition
    for mode, unit, maximum in (
        ("recorded", "", None),
        ("pass_fail", "", "5"),
    ):
        with pytest.raises(WeighInFailure) as refused:
            set_partition_weigh_in(partition, mode, unit, maximum)
        assert refused.value.code == Code.UNIT_NEEDED
    with pytest.raises(WeighInFailure) as refused:
        set_partition_weigh_in(partition, "class", "oz")
    assert refused.value.code == Code.BAD_MODE
    with pytest.raises(WeighInFailure) as refused:
        set_partition_weigh_in(partition, "recorded", "kg")
    assert refused.value.code == Code.BAD_UNIT
    set_partition_weigh_in(partition, "pass_fail", "oz", "5")
    partition.refresh_from_db()
    assert (partition.weigh_in_mode, partition.max_weight_grams) == (
        "pass_fail",
        Decimal("141.75"),
    )


def test_the_database_refuses_a_recorded_partition_with_no_unit(event):
    partition = racer_in(event, "Outlaws", "none").partition
    with pytest.raises(IntegrityError), transaction.atomic():
        Partition.objects.filter(pk=partition.pk).update(weigh_in_mode="recorded")


def test_a_new_maximum_recomputes_the_stored_pass(event):
    """Review of #70: the stored pass follows the partition's maximum."""
    racer = racer_in(event, "Outlaws", "recorded", "oz", max_weight="5")
    other = insert_new_racer(
        event, first_name="Ann", last_name="Light", partition=racer.partition
    ).racer
    record_weigh_in(racer, weight="5.2")
    record_weigh_in(other, weight="4.8")
    set_partition_weigh_in(racer.partition, "recorded", "oz", "6")
    racer.refresh_from_db()
    assert racer.weigh_in_passed is True
    set_partition_weigh_in(racer.partition, "recorded", "oz", "5")
    racer.refresh_from_db()
    other.refresh_from_db()
    assert (racer.weigh_in_passed, other.weigh_in_passed) == (False, True)
    set_partition_weigh_in(racer.partition, "recorded", "oz")
    racer.refresh_from_db()
    assert racer.weigh_in_passed is True
    assert racer.weight_grams == Decimal("147.42")


def test_a_new_mode_clears_the_partitions_weigh_ins(event):
    """Review of #70 (a D8 default the owner may correct): a weigh-in made
    under one mode means nothing under another, so a mode change clears it.
    A new unit or maximum keeps it."""
    racer = racer_in(event, "Outlaws", "recorded", "oz", max_weight="5")
    elsewhere = racer_in(event, "Scouts", "pass_fail")
    record_weigh_in(racer, weight="5.2")
    record_weigh_in(elsewhere, passed=True)
    set_partition_weigh_in(racer.partition, "recorded", "g", "150")
    racer.refresh_from_db()
    assert racer.weight_grams == Decimal("147.42")
    set_partition_weigh_in(racer.partition, "pass_fail", "oz", "5")
    racer = Racer.objects.select_related("partition").get(pk=racer.pk)
    assert (racer.weigh_in_passed, racer.weight_grams, racer.weighed_at) == (
        None,
        None,
        None,
    )
    assert weigh_in.status(racer).weighed is False
    elsewhere.refresh_from_db()
    assert elsewhere.weigh_in_passed is True
