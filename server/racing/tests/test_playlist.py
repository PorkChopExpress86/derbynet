import pytest

from racing.models import PlaylistEntry
from racing.services import reorder_playlist
from racing.tests.factories import PlaylistEntryFactory
from roster.tests.factories import GroupFactory

pytestmark = pytest.mark.django_db


def _playlist(event):
    return list(
        PlaylistEntry.objects.filter(event=event)
        .order_by("seq")
        .values_list("id", "seq")
    )


def test_reversing_a_three_step_playlist_renumbers_it_in_the_new_order():
    group = GroupFactory()
    first, second, third = (
        PlaylistEntryFactory(group=group, seq=seq) for seq in (1, 2, 3)
    )

    reorder_playlist(group.event, [third.id, second.id, first.id])

    assert _playlist(group.event) == [(third.id, 1), (second.id, 2), (first.id, 3)]


def test_reorder_renumbers_a_playlist_that_holds_negative_seqs():
    group = GroupFactory()
    first, second, third = (
        PlaylistEntryFactory(group=group, seq=seq) for seq in (-1, -2, 5)
    )

    reorder_playlist(group.event, [third.id, first.id, second.id])

    assert _playlist(group.event) == [(third.id, 1), (first.id, 2), (second.id, 3)]


def test_reorder_leaves_another_events_playlist_alone():
    group, other = GroupFactory(), GroupFactory()
    mine = [PlaylistEntryFactory(group=group, seq=seq) for seq in (1, 2)]
    theirs = [PlaylistEntryFactory(group=other, seq=seq) for seq in (1, 2)]

    reorder_playlist(group.event, [mine[1].id, mine[0].id])

    assert _playlist(other.event) == [(theirs[0].id, 1), (theirs[1].id, 2)]


def test_reorder_needs_every_entry_of_the_event():
    group = GroupFactory()
    first, _ = (PlaylistEntryFactory(group=group, seq=seq) for seq in (1, 2))

    with pytest.raises(ValueError, match="every playlist entry"):
        reorder_playlist(group.event, [first.id])
