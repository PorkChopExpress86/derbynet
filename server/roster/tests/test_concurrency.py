import threading

import pytest
from django.db import connection

from core.services import new_event
from roster.models import Group, Partition
from roster.services import groups

ATTEMPTS = 10


def _find_or_create(event, name, barrier, errors):
    try:
        barrier.wait()
        groups.find_or_create_partition(event.id, name)
    except Exception as e:
        errors.append(e)
    finally:
        connection.close()


# serialized_rollback restores the seeded award types after the flush.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_writers_find_or_create_one_partition():
    """Without the `RaceState` lock both writers miss the partition. With the
    same name the second insert breaks the unique name; with names that differ
    only by case nothing in the database stops a duplicate (D31). SQLite
    serializes writers anyway."""
    if connection.vendor == "sqlite":
        pytest.skip("SQLite allows one writer at a time")
    event = new_event("Derby")
    errors = []
    for attempt in range(ATTEMPTS):
        name = f"Cubs {attempt}"
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(
                target=_find_or_create, args=(event, each, barrier, errors)
            )
            for each in (name, name if attempt % 2 else name.upper())
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert errors == []
    assert Partition.objects.filter(event=event).count() == ATTEMPTS
    assert Group.objects.filter(event=event).count() == ATTEMPTS
