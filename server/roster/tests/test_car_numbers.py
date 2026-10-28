"""Car numbering by partition (`website/inc/car-numbering.inc`, #29)."""

import pytest

from core.models import RaceSettings
from core.tests.factories import EventFactory
from roster.services import car_numbers, groups, racers

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    return EventFactory()


def numbering(event, setting):
    RaceSettings.objects.filter(event=event).update(car_numbering=setting)


def add(event, partition, car=""):
    return racers.insert_new_racer(
        event, first_name="A", last_name="B", car_number=car, partition_name=partition
    ).racer


def test_each_partition_numbers_from_its_hundred(event):
    for name in ("Lions", "Tigers"):
        groups.find_or_create_partition(event.id, name)

    assert [add(event, "Lions").car_number for _ in range(3)] == [101, 102, 103]
    assert add(event, "Tigers").car_number == 201


def test_next_car_numbers_lists_every_partition_in_order(event):
    lions = groups.find_or_create_partition(event.id, "Lions")
    tigers = groups.find_or_create_partition(event.id, "Tigers")
    add(event, "Lions", "101")
    add(event, "Lions", "102")

    assert car_numbers.next_car_numbers(event.id) == [
        {"partitionid": lions.pk, "next_carnumber": 103},
        {"partitionid": tigers.pk, "next_carnumber": 201},
    ]


def test_a_gap_below_a_later_run_is_skipped_as_in_php(event):
    lions = groups.find_or_create_partition(event.id, "Lions")
    add(event, "Lions", "102")

    # 101 is free, but the run 102 ends first, so PHP offers 103.
    assert car_numbers.next_car_numbers(event.id) == [
        {"partitionid": lions.pk, "next_carnumber": 103}
    ]
    # A new racer still gets the free lower bound.
    assert car_numbers.next_car_number_for_partition(lions) == 101


def test_multiplier_zero_shares_one_sequence(event):
    numbering(event, "0+1")
    for name in ("First", "Second"):
        groups.find_or_create_partition(event.id, name)
    add(event, "Second", "1")

    assert [
        row["next_carnumber"] for row in car_numbers.next_car_numbers(event.id)
    ] == [
        2,
        2,
    ]
    assert add(event, "First").car_number == 2


def test_test_cars_far_above_do_not_disturb_numbering(event):
    """#288 of upstream, the last part of `test-car-numbers.sh`."""
    numbering(event, "0+1")
    for car in ("901", "902", "903"):
        add(event, "test", car)

    assert [add(event, "Bears").car_number for _ in range(3)] == [1, 2, 3]


def test_a_full_hundred_runs_past_its_bound(event):
    """PHP would store a null car number here."""
    lions = groups.find_or_create_partition(event.id, "Lions")
    groups.find_or_create_partition(event.id, "Tigers")
    for car in range(101, 201):
        add(event, "Lions", str(car))
    add(event, "Tigers", "201")

    assert car_numbers.next_car_number_for_partition(lions) == 202


def test_base_numbers_by_partition(event):
    lions = groups.find_or_create_partition(event.id, "Lions")
    tigers = groups.find_or_create_partition(event.id, "Tigers")

    assert car_numbers.base_car_number_by_partition(event.id) == {
        lions.pk: 101,
        tigers.pk: 201,
    }
    numbering(event, "0+1")
    assert car_numbers.base_car_number_by_partition(event.id) == 1
