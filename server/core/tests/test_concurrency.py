import threading

import pytest
from django.db import connection, transaction
from django.db.models import F

from core.models import RaceState
from core.services import new_event

UPDATES_PER_THREAD = 100


def _count_refused_results(event, barrier, errors):
    try:
        barrier.wait()
        for _ in range(UPDATES_PER_THREAD):
            with transaction.atomic():
                RaceState.objects.filter(event=event).update(
                    refused_results=F("refused_results") + 1
                )
    except Exception as e:
        errors.append(e)
    finally:
        connection.close()


# serialized_rollback restores the seeded award types after the flush.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_writers_never_lose_a_race_state_update():
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode")
            assert cursor.fetchone()[0] == "wal"
        assert connection.settings_dict["NAME"] != ":memory:"
    event = new_event("Derby")
    barrier = threading.Barrier(2)
    errors = []
    threads = [
        threading.Thread(target=_count_refused_results, args=(event, barrier, errors))
        for _ in range(2)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert RaceState.for_event(event).refused_results == 2 * UPDATES_PER_THREAD
