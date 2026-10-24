from django.db import transaction

from racing.models import PlaylistEntry


@transaction.atomic
def reorder_playlist(event, entry_ids):
    """Renumber the event's playlist so `entry_ids[i]` gets `seq = i + 1`.

    `seq` is unique per event, and SQLite has no deferrable unique constraint,
    so every entry first moves to a negative seq that no entry holds. The
    second pass then never meets a seq that is still taken.
    """
    entries = {
        entry.id: entry
        for entry in PlaylistEntry.objects.select_for_update().filter(event=event)
    }
    if sorted(entry_ids) != sorted(entries):
        raise ValueError("entry_ids must list every playlist entry of the event once")

    for position, entry_id in enumerate(entry_ids, start=1):
        PlaylistEntry.objects.filter(id=entry_id).update(seq=-position)
    for position, entry_id in enumerate(entry_ids, start=1):
        PlaylistEntry.objects.filter(id=entry_id).update(seq=position)
