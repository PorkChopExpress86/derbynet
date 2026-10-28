"""The `PWD` barcode grammar and walk-in racers (`website/inc/barcode.inc`)."""

import threading

import pytest
from django.db import connection

from core.models import ActionLog, RaceSettings
from core.services import new_event
from core.tests.factories import EventFactory
from racing.models import RosterEntry
from roster.models import Racer
from roster.services import barcode, groups, racers
from roster.services.barcode import (
    CarNumber,
    NotABarcode,
    RacerId,
    UploadOnly,
    UploadTest,
    WalkIn,
)


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("PWDid12", RacerId(12)),
        ("  PWDid12\n", RacerId(12)),
        ("PWDid12abc", RacerId(12)),
        ("PWDid", RacerId(0)),
        ("PWDwi305", WalkIn(305)),
        ("PWDwi", WalkIn(0)),
        ("PWD101", CarNumber("101")),
        ("PWDabc", CarNumber("abc")),
        ("PWDuploadtest", UploadTest()),
        ("PWDuploadonly", UploadOnly()),
        ("101", NotABarcode()),
        ("pwd101", NotABarcode()),
    ],
)
def test_the_grammar(text, parsed):
    assert barcode.parse(text) == parsed


@pytest.fixture
def event(db):
    return EventFactory()


def add(event, partition, car):
    return racers.insert_new_racer(
        event,
        first_name="F",
        last_name=f"L{car}",
        car_number=car,
        partition_name=partition,
    ).racer


def test_pwdid_finds_only_a_racer_of_the_event(event):
    racer = add(event, "Lions", 101)
    other = add(EventFactory(), "Lions", 101)

    assert barcode.resolve(event, f"PWDid{racer.pk}").racer == racer
    assert barcode.resolve(event, f"PWDid{other.pk}").racer is None


def test_pwd_carno_needs_a_unique_car_number(event):
    racer = add(event, "Lions", 101)
    add(event, "Lions", 102)
    add(event, "Tigers", 102)

    assert barcode.resolve(event, "PWD101").racer == racer
    assert barcode.resolve(event, "PWD0101").racer == racer
    assert barcode.resolve(event, "PWD102") == barcode.Resolved(None)
    assert barcode.resolve(event, "PWD999") == barcode.Resolved(None)
    assert barcode.resolve(event, "PWDuploadtest") == barcode.Resolved(None)


def test_pwdwi_finds_an_existing_car(event):
    racer = add(event, "Lions", 101)

    assert barcode.resolve(event, "PWDwi101") == barcode.Resolved(racer, False)
    assert Racer.objects.filter(event=event).count() == 1


def test_pwdwi_makes_a_walk_in_in_the_partition_of_its_hundred(event):
    for name in ("Lions", "Tigers", "Bears"):
        groups.find_or_create_partition(event.id, name)

    found = barcode.resolve(event, "PWDwi205")

    assert found.created
    walk_in = found.racer
    assert (walk_in.first_name, walk_in.last_name, walk_in.car_number) == (
        "Walk-In",
        "205",
        205,
    )
    assert walk_in.partition.name == "Tigers"
    assert RosterEntry.objects.filter(racer=walk_in, round__number=1).exists()
    assert ActionLog.objects.filter(
        event=event, request__action="racer.*create"
    ).exists()


def test_pwdwi_past_the_last_hundred_goes_to_the_last_partition(event):
    for name in ("Lions", "Tigers"):
        groups.find_or_create_partition(event.id, name)

    assert barcode.resolve(event, "PWDwi950").racer.partition.name == "Tigers"


def test_pwdwi_with_multiplier_zero_picks_the_last_partition(event):
    """C14: PHP sets `$partition`, not `$partitionid`, so the walk-in's
    partition is undefined."""
    RaceSettings.objects.filter(event=event).update(car_numbering="0+1")
    for name in ("Lions", "Tigers", "Bears"):
        groups.find_or_create_partition(event.id, name)

    walk_in = barcode.resolve(event, "PWDwi7").racer

    assert walk_in.partition.name == "Bears"
    assert walk_in.car_number == 7


def test_pwdwi_with_no_partitions_uses_default(event):
    assert barcode.resolve(event, "PWDwi5").racer.partition.name == "Default"


def _scan(event, barcode_text, barrier, errors):
    try:
        barrier.wait()
        barcode.resolve(event, barcode_text)
    except Exception as e:
        errors.append(e)
    finally:
        connection.close()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_scanners_posting_one_walk_in_make_one_racer():
    event = new_event("Derby")
    groups.find_or_create_partition(event.id, "Lions")
    errors = []
    for car in range(101, 106):
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=_scan, args=(event, f"PWDwi{car}", barrier, errors))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert errors == []
    cars = list(Racer.objects.filter(event=event).values_list("car_number", flat=True))
    assert sorted(cars) == [101, 102, 103, 104, 105]


def test_pwdwi_with_multiplier_zero_picks_the_highest_partition_id(event):
    """PHP means `COALESCE(MAX(partitionid), 1)`, which a reorder does not
    change."""
    RaceSettings.objects.filter(event=event).update(car_numbering="0+1")
    lions = groups.find_or_create_partition(event.id, "Lions")
    tigers = groups.find_or_create_partition(event.id, "Tigers")
    groups.order_partitions(event, [tigers, lions])

    assert barcode.resolve(event, "PWDwi7").racer.partition == tigers
