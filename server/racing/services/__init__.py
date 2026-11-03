from django.db import transaction

from core.services import lock_race_state
from racing.models import PlaylistEntry


def reorder_playlist(event, entry_ids):
    """Renumber the event's playlist so `entry_ids[i]` gets `seq = i + 1`,
    under the `RaceState` lock that every playlist writer takes."""
    with transaction.atomic():
        lock_race_state(event.id)
        renumber_playlist(event, entry_ids)


def renumber_playlist(event, entry_ids):
    """`reorder_playlist` for a caller that holds the lock already.

    `seq` is unique per event, and SQLite has no deferrable unique constraint,
    so every entry first moves to a seq below `-max(abs(seq))`, which no entry
    holds even when an imported seq is already negative. The second pass then
    never meets a seq that is still taken.
    """
    entries = {
        entry.id: entry
        for entry in PlaylistEntry.objects.select_for_update().filter(event=event)
    }
    if sorted(entry_ids) != sorted(entries):
        raise ValueError("entry_ids must list every playlist entry of the event once")

    offset = max((abs(entry.seq) for entry in entries.values()), default=0)
    for position, entry_id in enumerate(entry_ids, start=1):
        PlaylistEntry.objects.filter(id=entry_id).update(seq=-(offset + position))
    for position, entry_id in enumerate(entry_ids, start=1):
        PlaylistEntry.objects.filter(id=entry_id).update(seq=position)
