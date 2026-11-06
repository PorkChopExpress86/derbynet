"""The automatic snapshot before a purge or a snapshot import is read under
the same `RaceState` lock as the change (#48 review, D78): a racer added
while the snapshot is written either waits for the change or is in the
snapshot, never deleted unrecorded."""

import threading
import time

import pytest
from django.db import connection

from core.models import RaceState, SiteState
from core.services import new_event
from roster.models import Racer
from roster.services.racers import insert_new_racer
from transfer import backups, snapshot

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


@pytest.fixture(autouse=True)
def _data_dir(settings, tmp_path):
    settings.DATA_DIR = tmp_path


def _late_racer_while_snapshot_written(monkeypatch, event, change, late=None):
    """Run `change` in one thread; once its snapshot is written, a second
    thread adds a racer through the roster service. Returns the snapshot
    text and the racers left."""
    written = threading.Event()
    real_get = snapshot.get

    def slow_get(*args, **kwargs):
        text = real_get(*args, **kwargs)
        written.set()
        time.sleep(0.5)
        return text

    monkeypatch.setattr(snapshot, "get", slow_get)
    errors = []

    def run_change():
        try:
            change()
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            connection.close()

    def add_racer():
        try:
            written.wait(10)
            insert_new_racer(
                event, first_name="Late", last_name="Racer", partition_name="Lions"
            )
            if late is not None:
                late.append("added")
        except RaceState.DoesNotExist:
            # The import replaced the event while this waited for its lock.
            if late is not None:
                late.append("event-gone")
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=run_change), threading.Thread(target=add_racer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    return backups.snapshots(event)[0].read_text()


def test_a_racer_added_during_the_snapshot_is_never_purged_unrecorded(monkeypatch):
    event = new_event("Derby")
    insert_new_racer(event, first_name="Ava", last_name="Early", partition_name="Lions")
    saved = _late_racer_while_snapshot_written(
        monkeypatch, event, lambda: backups.purge(event, "racers")
    )
    late_kept = Racer.objects.filter(event=event, first_name="Late").exists()
    assert late_kept or '"Late"' in saved
    assert '"Ava"' in saved


def test_a_racer_added_during_an_imports_snapshot_waits_for_the_import(monkeypatch):
    event = new_event("Derby")
    insert_new_racer(event, first_name="Ava", last_name="Early", partition_name="Lions")
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n' + snapshot.get(event)
    ).encode()
    late = []
    saved = _late_racer_while_snapshot_written(
        monkeypatch, event, lambda: backups.import_snapshot(event, content), late
    )
    restored = SiteState.get().active_event
    assert '"Ava"' in saved
    # Either the racer waited and found its event replaced, or it was added
    # before the snapshot was read; never added and lost unrecorded.
    assert late == ["event-gone"] or '"Late"' in saved
    assert not Racer.objects.filter(event=restored, first_name="Late").exists() or (
        '"Late"' in saved
    )
