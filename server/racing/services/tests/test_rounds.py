import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import RaceSettings, RaceState
from racing.models import HeatLane, PlaylistEntry, Round
from racing.services import playlist, rounds
from racing.services.rounds import (
    Failure,
    RoundFailure,
    delete_round_roster,
    prepare_new_round,
    round_counts,
    write_roster,
)
from roster.models import Group, GroupConstituent, Racer
from roster.services import groups
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db


@pytest.fixture
def replay():
    replay = Replay()
    for den in ("Lions", "Tigers"):
        for i in range(3):
            replay.import_racer(f"{den}{i}", "Racer", den)
    return replay


def group(replay, name):
    return Group.objects.get(event=replay.event, name=name)


def racers(replay, name):
    return list(Racer.objects.filter(group=group(replay, name)).order_by("pk"))


def failure_of(call, *args):
    with pytest.raises(RoundFailure) as caught:
        call(*args)
    return caught.value.code


def test_a_follow_on_round_holds_the_finalists_in_the_order_given(replay):
    lions = group(replay, "Lions")
    final = prepare_new_round(lions, 2)
    chosen = racers(replay, "Lions")[::-1][:2]
    write_roster(final, chosen)
    assert [e.racer for e in final.roster.order_by("pk")] == chosen
    assert final.number == 2 and final.group == lions


def test_preparing_an_existing_round_again_clears_its_roster(replay):
    lions = group(replay, "Lions")
    final = prepare_new_round(lions, 2)
    write_roster(final, racers(replay, "Lions"))
    assert prepare_new_round(lions, 2) == final
    assert not final.roster.exists()


def test_roster_delete_refuses_a_round_with_heats(replay):
    final = prepare_new_round(group(replay, "Lions"), 2)
    first, second = racers(replay, "Lions")[:2]
    write_roster(final, [first, second])
    HeatLane.objects.create(
        event=replay.event, round=final, heat=1, lane=1, racer=first
    )
    assert failure_of(delete_round_roster, final) == Failure.SCHEDULED


def test_roster_delete_refuses_round_1_of_an_organic_group(replay):
    first = Round.objects.get(group=group(replay, "Lions"), number=1)
    assert failure_of(delete_round_roster, first) == Failure.FIRST_ROUND


def test_deleting_the_only_round_of_an_aggregate_deletes_the_aggregate(replay):
    finals = groups.create_aggregate_group(
        replay.event.id,
        "Grand Finals",
        [group(replay, "Lions"), group(replay, "Tigers")],
    )
    RaceSettings.objects.filter(event=replay.event).update(
        full_field_rule="group", full_field_group=finals
    )
    PlaylistEntry.objects.create(
        event=replay.event, seq=1, group=finals, round_number=1
    )
    round = prepare_new_round(finals, 1)
    write_roster(round, racers(replay, "Lions"))
    delete_round_roster(round)
    assert not Group.objects.filter(pk=finals.pk).exists()
    assert not PlaylistEntry.objects.filter(event=replay.event).exists()
    assert RaceSettings.objects.get(event=replay.event).full_field_rule == "standard"


def test_a_durable_aggregate_stays_without_its_round(replay):
    finals = groups.add_group(
        replay.event, "Finals", constituents=[group(replay, "Lions")]
    )
    round = prepare_new_round(finals, 1)
    delete_round_roster(round)
    assert Group.objects.filter(pk=finals.pk).exists()
    assert not Round.objects.filter(group=finals).exists()


def test_roster_delete_refuses_the_last_round_of_a_constituent_aggregate(replay):
    """The aggregate would go, and with it a durable aggregate's link to it,
    so this is refused as `class.delete` refuses a constituent."""
    finals = groups.create_aggregate_group(
        replay.event.id, "Finals", [group(replay, "Lions")]
    )
    grand = groups.add_group(replay.event, "Grand", constituents=[finals])
    round = prepare_new_round(finals, 1)
    assert failure_of(delete_round_roster, round) == Failure.CONSTITUENT
    assert Round.objects.filter(pk=round.pk).exists()
    assert GroupConstituent.objects.filter(aggregate=grand, constituent=finals).exists()


def test_deleting_the_current_round_stops_racing(replay):
    final = prepare_new_round(group(replay, "Lions"), 2)
    RaceState.objects.filter(event=replay.event).update(
        current_round=final, current_heat=1, racing=True
    )
    delete_round_roster(final)
    state = RaceState.objects.get(event=replay.event)
    assert (state.current_round, state.current_heat, state.racing) == (
        None,
        None,
        False,
    )


def test_round_counts(replay):
    lions = racers(replay, "Lions")
    Racer.objects.filter(pk__in=[r.pk for r in lions[:2]]).update(
        passed_inspection=True
    )
    first = Round.objects.get(group=group(replay, "Lions"), number=1)
    HeatLane.objects.create(
        event=replay.event, round=first, heat=1, lane=1, racer=lions[0], finish_time=3
    )
    HeatLane.objects.create(
        event=replay.event, round=first, heat=2, lane=1, racer=lions[2]
    )
    counts = {
        (c.round.group.name, c.round.number): c for c in round_counts(replay.event)
    }
    lion = counts[("Lions", 1)]
    assert (lion.roster_size, lion.passed, lion.unscheduled, lion.departed) == (
        3,
        2,
        1,
        1,
    )
    assert (lion.heats_scheduled, lion.heats_run) == (2, 1)
    assert lion.name == "Lions, Round 1" and not lion.aggregate


def test_round_names_leave_out_a_lone_group():
    replay = Replay()
    replay.import_racer("Only", "Racer", "Pack")
    assert [c.name for c in round_counts(replay.event)] == ["Round 1"]


# ----- The RaceState lock ---------------------------------------------------


def _writers(replay):
    lions, tigers = group(replay, "Lions"), group(replay, "Tigers")
    lions_partition = lions.subgroups.get().partitions.get()
    PlaylistEntry.objects.create(event=replay.event, seq=1, group=lions, round_number=1)
    final, extra = prepare_new_round(lions, 2), prepare_new_round(lions, 4)
    final_step = PlaylistEntry.objects.create(
        event=replay.event, seq=2, group=lions, round_number=2
    )
    HeatLane.objects.create(
        event=replay.event,
        round=final,
        heat=1,
        lane=1,
        racer=racers(replay, "Lions")[0],
    )
    RaceState.objects.filter(event=replay.event).update(
        waiting_entry=final_step,
        ended_round=Round.objects.get(group=lions, number=1),
    )
    wolves = groups.add_partition(replay.event, "Wolves")
    spare = groups.add_subgroup(replay.event, lions.pk, "Spare")
    empty = groups.add_group(replay.event, "Empty")
    return {
        "write_roster": lambda: write_roster(final, racers(replay, "Lions")),
        "prepare_new_round": lambda: prepare_new_round(lions, 3),
        "make_new_round": lambda: rounds.make_new_round(lions, 5),
        "delete_round_roster": lambda: delete_round_roster(extra),
        "fill_in_missing_roster_entries": lambda: groups.fill_in_missing_roster_entries(
            replay.event.id
        ),
        "form_groups_by_rule": lambda: groups.form_groups_by_rule(
            replay.event, "one-group"
        ),
        "move_partition": lambda: groups.move_partition(
            lions_partition, groups.GroupRef("classid", tigers.pk)
        ),
        "move_subgroup": lambda: groups.move_subgroup(
            lions.subgroups.get(name="Lions"), tigers
        ),
        "rename_partition": lambda: groups.rename_partition(lions_partition, "Cubs"),
        "add_group": lambda: groups.add_group(replay.event, "Bears"),
        "start_playlist": lambda: playlist.start_playlist(
            replay.event, lambda e, r: []
        ),
        "round_ended": lambda: playlist.round_ended(
            Round.objects.get(group=lions, number=1), lambda e, r: []
        ),
        "add_partition": lambda: groups.add_partition(replay.event, "Cubs"),
        "delete_partition": lambda: groups.delete_partition(wolves),
        "add_subgroup": lambda: groups.add_subgroup(replay.event, lions.pk, "Cubs"),
        "rename_subgroup": lambda: groups.rename_subgroup(
            lions.subgroups.get(name="Lions"), "Cubs"
        ),
        "delete_subgroup": lambda: groups.delete_subgroup(spare),
        "rename_group": lambda: groups.rename_group(lions, "Cubs"),
        "delete_group": lambda: groups.delete_group(empty),
        "order_groups": lambda: groups.order_groups(replay.event, [tigers, lions]),
        "order_subgroups": lambda: groups.order_subgroups(replay.event, [spare]),
        "order_partitions": lambda: groups.order_partitions(
            replay.event, [wolves, lions_partition]
        ),
        "create_aggregate_group": lambda: groups.create_aggregate_group(
            replay.event.id, "Finals", [lions]
        ),
        "delete_rounds": lambda: groups.delete_rounds(
            replay.event.id, Round.objects.filter(pk=extra.pk)
        ),
        "delete_group_rows": lambda: groups.delete_group_rows(empty),
        "reset_full_field": lambda: groups.reset_full_field(replay.event.id, [lions]),
        "find_or_create_group": lambda: groups.find_or_create_group(
            replay.event.id, "Bears"
        ),
        "find_or_create_subgroup": lambda: groups.find_or_create_subgroup(
            lions, "Cubs"
        ),
        "find_or_create_partition": lambda: groups.find_or_create_partition(
            replay.event.id, "Cubs"
        ),
        "clean_up_empty_groups_and_subgroups": lambda: (
            groups.clean_up_empty_groups_and_subgroups(replay.event.id)
        ),
        "start_waiting_step": lambda: playlist.start_waiting_step(replay.event),
        "skip_waiting_step": lambda: playlist.skip_waiting_step(
            replay.event, lambda e, r: []
        ),
        "rebuild_waiting_round": lambda: playlist.rebuild_waiting_round(
            replay.event, lambda e, r: racers(replay, "Lions")
        ),
    }


WRITERS = [
    "write_roster",
    "prepare_new_round",
    "make_new_round",
    "delete_round_roster",
    "fill_in_missing_roster_entries",
    "form_groups_by_rule",
    "move_partition",
    "move_subgroup",
    "rename_partition",
    "add_group",
    "start_playlist",
    "round_ended",
    "add_partition",
    "delete_partition",
    "add_subgroup",
    "rename_subgroup",
    "delete_subgroup",
    "rename_group",
    "delete_group",
    "order_groups",
    "order_subgroups",
    "order_partitions",
    "create_aggregate_group",
    "delete_rounds",
    "delete_group_rows",
    "reset_full_field",
    "find_or_create_group",
    "find_or_create_subgroup",
    "find_or_create_partition",
    "clean_up_empty_groups_and_subgroups",
    "start_waiting_step",
    "skip_waiting_step",
    "rebuild_waiting_round",
]


@pytest.mark.parametrize("writer", WRITERS)
def test_every_roster_writer_takes_the_race_state_lock_before_it_writes(
    replay, monkeypatch, writer
):
    """`generate_schedule` rechecks the roster under this lock (#25)."""
    run = _writers(replay)[writer]
    locked_at = []
    with CaptureQueriesContext(connection) as queries:

        def spy(event_id):
            assert connection.in_atomic_block
            assert event_id == replay.event.id
            locked_at.append(len(queries.captured_queries))
            return RaceState.objects.get(event_id=event_id)

        for module in (groups, rounds, playlist):
            monkeypatch.setattr(module, "lock_race_state", spy)
        run()
    writes = [
        i
        for i, q in enumerate(queries.captured_queries)
        if q["sql"].split()[0].upper() in ("INSERT", "UPDATE", "DELETE")
    ]
    assert writes and locked_at and locked_at[0] <= writes[0]
