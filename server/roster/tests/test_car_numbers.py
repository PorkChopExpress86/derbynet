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


def test_a_full_hundred_has_no_next_number(event):
    """PHP's query finds no number and returns null."""
    lions = groups.find_or_create_partition(event.id, "Lions")
    groups.find_or_create_partition(event.id, "Tigers")
    for car in range(101, 201):
        add(event, "Lions", str(car))
    add(event, "Tigers", "201")

    assert car_numbers.next_car_number_for_partition(lions) is None


def test_base_numbers_by_partition(event):
    lions = groups.find_or_create_partition(event.id, "Lions")
    tigers = groups.find_or_create_partition(event.id, "Tigers")

    assert car_numbers.base_car_number_by_partition(event.id) == {
        lions.pk: 101,
        tigers.pk: 201,
    }
    numbering(event, "0+1")
    assert car_numbers.base_car_number_by_partition(event.id) == 1


# ----- What check-in shows as the volunteer types (#42) -----


def _racer(event, name, car, partition):
    return racers.insert_new_racer(
        event,
        first_name=name,
        last_name="Racer",
        car_number=car,
        partition_name=partition,
    ).racer


def test_the_first_racer_of_an_empty_event_is_offered_101(event):
    assert car_numbers.suggested_number(event.id, None) == 101
    assert groups.first_partition(event.id) is None


def test_each_partition_is_offered_its_next_number(event):
    _racer(event, "A", 101, "Scouts")
    _racer(event, "B", 102, "Scouts")
    siblings = groups.find_or_create_partition(event.id, "Siblings")
    scouts = groups.partition_of(event.id, str(groups.first_partition(event.id).pk))
    assert scouts.name == "Scouts"
    assert car_numbers.suggested_number(event.id, scouts) == 103
    assert car_numbers.suggested_number(event.id, siblings) == 201
    # With partitions, no partition is no suggestion: the volunteer picks one.
    assert car_numbers.suggested_number(event.id, None) is None


def test_partition_of_refuses_a_name_or_another_events_id(event):
    scouts = groups.find_or_create_partition(event.id, "Scouts")
    other = groups.find_or_create_partition(EventFactory().id, "Scouts")
    assert groups.partition_of(event.id, "Scouts") is None
    assert groups.partition_of(event.id, str(other.pk)) is None
    assert groups.partition_of(event.id, scouts.pk) == scouts


def test_full_ranges_ask_for_another(event):
    _racer(event, "A", 101, "Scouts")
    scouts = groups.first_partition(event.id)
    assert not car_numbers.ranges_full(scouts)
    car_numbers.add_range(scouts, 101, 101)
    assert car_numbers.ranges_full(scouts)
    assert car_numbers.suggested_number(event.id, scouts) is None
    assert not car_numbers.ranges_full(None)


def test_number_warnings_for_a_duplicate_and_an_outside_number(event):
    a = _racer(event, "A", 42, "Scouts")
    scouts = a.partition
    car_numbers.add_range(scouts, 1, 99)
    assert car_numbers.number_warnings(event.id, scouts, 42) == [
        "Car number 042 belongs to A Racer too."
    ]
    assert car_numbers.number_warnings(event.id, scouts, 42, racer_id=a.pk) == []
    assert car_numbers.number_warnings(event.id, scouts, 150) == [
        "Car number 150 is outside the car number ranges of Scouts"
    ]
    assert car_numbers.number_warnings(event.id, scouts, None) == []


def test_bulk_choices_list_partitions_and_groups_when_several(event):
    _racer(event, "A", 101, "Scouts")
    assert [label for _, label in racers.bulk_choices(event)] == [
        "All racers",
        "Scouts",
    ]
    _racer(event, "B", 201, "Bears")
    labels = [label for _, label in racers.bulk_choices(event)]
    assert labels[:3] == ["All racers", "Scouts", "Bears"]
    assert "Bears (group)" in labels
