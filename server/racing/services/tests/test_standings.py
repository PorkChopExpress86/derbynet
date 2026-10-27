import pytest
from django.utils import timezone

from awards.services import set_partition_list
from core.models import FullFieldRule, RaceSettings, RaceState
from racing.engine.standings.eligibility import NotEligible
from racing.engine.standings.oracle import FULL_FIELD, round_key
from racing.models import HeatLane, PlaylistEntry, RosterEntry, Round
from racing.services.playlist import rebuild_waiting_round
from racing.services.rounds import (
    Failure,
    FollowOn,
    FromAggregate,
    NewAggregate,
    RoundFailure,
    new_round_roster,
    playlist_finalists,
)
from racing.services.standings import current_award, standings_snapshot
from roster.models import Group, Partition, Racer
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db

LIONS = [3.01, 3.02, 3.03, 3.04, 3.05, 3.06, 3.07]
TIGERS = [3.015, 3.025, 3.035]


@pytest.fixture
def replay():
    replay = Replay()
    RaceSettings.objects.filter(event=replay.event).update(lane_count=4)
    for den, times in (("Lions", LIONS), ("Tigers", TIGERS)):
        for i, _ in enumerate(times):
            replay.import_racer(f"{den}{i}", "Racer", den)
    Racer.objects.filter(event=replay.event).update(passed_inspection=True)
    for den, times in (("Lions", LIONS), ("Tigers", TIGERS)):
        round = first_round(replay, den)
        for lane, (racer, time) in enumerate(
            zip(racers_of(replay, den), times, strict=True), 1
        ):
            for heat in (1, 2):
                HeatLane.objects.create(
                    event=replay.event,
                    round=round,
                    heat=heat * 10 + lane,
                    lane=1,
                    racer=racer,
                    finish_time=time,
                    completed=timezone.now(),
                )
    return replay


def racers_of(replay, den):
    return list(Racer.objects.filter(group__name=den).order_by("pk"))


def first_round(replay, den):
    return Round.objects.get(group__name=den, number=1)


def roster(round):
    return [e.racer for e in RosterEntry.objects.filter(round=round).order_by("pk")]


def test_a_top_4_final_with_an_extra_racer_holds_5(replay):
    lions = racers_of(replay, "Lions")
    late = lions[6]
    made = new_round_roster(
        FollowOn(first_round(replay, "Lions")), top=4, extras=[late]
    )
    assert roster(made.round) == lions[:4] + [late]
    assert [f.racer_id for f in made.qualifiers] == [r.pk for r in lions[:4]]
    assert made.extras == (late,)


def test_an_extra_racer_who_qualifies_is_entered_once(replay):
    lions = racers_of(replay, "Lions")
    made = new_round_roster(
        FollowOn(first_round(replay, "Lions")), top=4, extras=[lions[1]]
    )
    assert roster(made.round) == lions[:4]
    assert made.extras == ()


def test_a_rebuilt_final_keeps_its_extra_racer(replay):
    """D27 and D32: a round 1 result changes during the intermission, and
    the waiting final is made again from the new standings and the same
    extra racer."""
    lions = racers_of(replay, "Lions")
    late = lions[6]
    round_1 = first_round(replay, "Lions")
    entry = PlaylistEntry.objects.create(
        event=replay.event,
        seq=2,
        group=round_1.group,
        round_number=2,
        bucket_limit=4,
    )
    finalists_for = playlist_finalists(extras={(round_1.group_id, 2): [late]})
    new_round_roster(FollowOn(round_1), top=4, extras=[late])
    RaceState.objects.filter(event=replay.event).update(
        waiting_entry=entry, ended_round=round_1
    )

    HeatLane.objects.filter(round=round_1, racer=lions[5]).update(finish_time=2.9)
    assert rebuild_waiting_round(replay.event, finalists_for)

    final = Round.objects.get(group=round_1.group, number=2)
    assert roster(final) == [lions[5], *lions[:3], late]
    assert HeatLane.objects.filter(round=final, racer=late).exists()


def test_a_playlist_final_gets_only_its_own_rounds_extras(replay):
    """D32: an extra racer allowed into a group's round 3 is not entered in
    the same group's round 2."""
    lions = racers_of(replay, "Lions")
    late = lions[6]
    group = first_round(replay, "Lions").group
    finalists_for = playlist_finalists(extras={(group.pk, 3): [late]})
    entrants = {}
    for number in (2, 3):
        entry = PlaylistEntry.objects.create(
            event=replay.event,
            seq=number,
            group=group,
            round_number=number,
            bucket_limit=2,
        )
        round = Round.objects.create(event=replay.event, group=group, number=number)
        entrants[number] = list(finalists_for(entry, round).racers)
    assert entrants == {2: lions[:2], 3: [*lions[:2], late]}


def test_a_racer_who_qualifies_from_two_groups_is_entered_once(replay):
    """An aggregate of Lions and of an aggregate drawn from Lions: the Lions
    leaders qualify from both, and each is entered once."""
    lions = racers_of(replay, "Lions")
    lions_final = new_round_roster(
        NewAggregate("Lions final", groups=[lions[0].group]), top=2
    ).round
    for lane, racer in enumerate(roster(lions_final), 1):
        HeatLane.objects.create(
            event=replay.event,
            round=lions_final,
            heat=1,
            lane=lane,
            racer=racer,
            finish_time=3.0 + lane / 100,
            completed=timezone.now(),
        )
    grand = new_round_roster(
        NewAggregate("Grand", groups=[lions[0].group, lions_final.group]),
        top=2,
        bucketed=True,
    )
    assert roster(grand.round) == lions[:2]
    assert grand.concerns == ("There are no eligible Lions final racer(s).",)


def test_a_cut_skips_a_racer_not_eligible_for_speed_awards(replay):
    """D34: the second fastest is passed over and the fifth goes in. The
    skipped racer keeps the standings rows, marked, with no place."""
    lions = racers_of(replay, "Lions")
    Racer.objects.filter(pk=lions[1].pk).update(not_eligible_for_speed_awards=True)
    made = new_round_roster(FollowOn(first_round(replay, "Lions")), top=4)
    assert roster(made.round) == [lions[0], *lions[2:5]]

    standings = standings_snapshot(replay.event).standings
    rows = [row for row in standings.rows if row.racer.id == lions[1].pk]
    assert rows and all(row.outcome == {} for row in rows)
    assert {why for row in rows for why in row.not_eligible.values()} == {
        NotEligible.SPEED_AWARDS
    }


def test_both_award_settings_show_in_the_snapshot_with_no_place(replay):
    lions = racers_of(replay, "Lions")
    Racer.objects.filter(pk=lions[0].pk).update(exclude=True)
    Racer.objects.filter(pk=lions[1].pk).update(not_eligible_for_speed_awards=True)
    standings = standings_snapshot(replay.event).standings
    marks = {
        row.racer.id: set(row.not_eligible.values())
        for row in standings.rows
        if row.racer.id in (lions[0].pk, lions[1].pk)
    }
    assert marks == {
        lions[0].pk: {NotEligible.ANY_AWARD},
        lions[1].pk: {NotEligible.SPEED_AWARDS},
    }
    ladder = standings.ladders[round_key(first_round(replay, "Lions").pk)]
    assert ladder[0] == (lions[2].pk,)


def test_an_aggregate_final_skips_a_partition_its_list_leaves_out(replay):
    """D35: the cut reads the list of the group the final is for. The pack's
    list marks the pack's ladder only, and changes no cut."""
    lions, tigers = racers_of(replay, "Lions"), racers_of(replay, "Tigers")
    lions_only = [Partition.objects.get(event=replay.event, name="Lions")]
    set_partition_list(replay.event, lions_only)
    standings = standings_snapshot(replay.event).standings
    tiger_rows = [r for r in standings.rows if r.racer.group_id == tigers[0].group_id]
    assert tiger_rows and all(
        dict(r.not_eligible) == {FULL_FIELD: NotEligible.PARTITION_NOT_LISTED}
        for r in tiger_rows
    )
    assert all(r.outcome for r in tiger_rows), "places in the group stay"

    made = new_round_roster(
        NewAggregate("Grand Final", groups=[lions[0].group, tigers[0].group]),
        top=4,
    )
    final = made.round.group
    assert set(roster(made.round)) == {*lions[:2], *tigers[:2]}, (
        "the pack's list changes no cut"
    )
    settings = RaceSettings.objects.get(event=replay.event)
    assert settings.full_field_rule == FullFieldRule.GROUP
    assert settings.full_field_group == final

    set_partition_list(replay.event, lions_only, group=final)
    assert roster(new_round_roster(FromAggregate(final), top=4).round) == lions[:4]


def test_no_eligible_racer_fails_and_changes_nothing(replay):
    Racer.objects.filter(event=replay.event).update(exclude=True)
    before = Group.objects.filter(event=replay.event).count()
    with pytest.raises(RoundFailure) as caught:
        new_round_roster(
            NewAggregate("Nobody", groups=[first_round(replay, "Lions").group]),
            top=4,
        )
    assert caught.value.code == Failure.NO_RACERS
    assert Group.objects.filter(event=replay.event).count() == before
    assert (
        RaceSettings.objects.get(event=replay.event).full_field_rule
        == FullFieldRule.STANDARD
    )


def test_an_aggregate_takes_the_top_of_each_group(replay):
    lions, tigers = racers_of(replay, "Lions"), racers_of(replay, "Tigers")
    aggregate = new_round_roster(
        NewAggregate("Finals", groups=[lions[0].group, tigers[0].group]),
        top=2,
        bucketed=True,
    ).round.group
    again = new_round_roster(FromAggregate(aggregate), top=2, bucketed=True)
    assert roster(again.round) == lions[:2] + tigers[:2]
    assert again.concerns == ()


def test_the_snapshot_follows_a_result_change(replay):
    first = standings_snapshot(replay.event)
    lane = HeatLane.objects.filter(event=replay.event).order_by("pk").last()
    HeatLane.objects.filter(pk=lane.pk).update(finish_time=2.5)
    second = standings_snapshot(replay.event)
    assert second.standings.ladders != first.standings.ladders


def test_no_caller_can_change_the_standings_another_reads(replay):
    standings = standings_snapshot(replay.event).standings
    ladder = standings.award_ladder_full_field()
    with pytest.raises((AttributeError, TypeError)):
        ladder.pop()
    with pytest.raises((AttributeError, TypeError)):
        ladder[0].append(0)
    with pytest.raises(TypeError):
        standings.catalog_counts[FULL_FIELD] = 0
    with pytest.raises(TypeError):
        standings.ladders[FULL_FIELD] = ()
    assert standings.award_ladder_full_field() == ladder


def test_the_current_award_names_its_racer(replay):
    lions = racers_of(replay, "Lions")
    RaceState.objects.filter(event=replay.event).update(current_award_key="speed-1")
    assert current_award(replay.event).racer_id == lions[0].pk
    assert current_award(replay.event).name == "Fastest in Pack"


def test_a_playlist_cut_reports_its_concerns(replay):
    round_1 = first_round(replay, "Tigers")
    entry = PlaylistEntry.objects.create(
        event=replay.event, seq=2, group=round_1.group, round_number=2, bucket_limit=4
    )
    final = Round.objects.create(event=replay.event, group=round_1.group, number=2)
    entrants = playlist_finalists()(entry, final)
    assert list(entrants.racers) == racers_of(replay, "Tigers")
    assert entrants.concerns == ("There are only 3 eligible Tigers racer(s).",)


def test_a_new_aggregate_of_nothing_fails_with_no_racers(replay):
    with pytest.raises(RoundFailure) as caught:
        new_round_roster(NewAggregate("Nobody"), top=4)
    assert caught.value.code == Failure.NO_RACERS
