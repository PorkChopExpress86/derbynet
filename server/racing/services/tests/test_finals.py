"""Allowed racers in a final, and finalist suggestions after a locked round
1 result changes (#103, owner decisions D32, D33, D34).

Scouts cars 101 to 108 pass check-in and race round 1 on 4 lanes, with
car 101 fastest and car 108 slowest. Car 109 is registered, misses round
1, and passes check-in afterwards. The playlist then races a top 4 final.
"""

import pytest
from django.utils import timezone

from core.models import RaceSettings, RaceState
from racing.models import HAS_RESULT, FinalRequest, HeatLane, PlaylistEntry, Round
from racing.services import finals
from racing.services.finals import (
    Failure,
    FinalsFailure,
    add_to_final,
    allow_final_request,
    decline_final_request,
    finalist_suggestions,
    poll_finals,
    remove_from_final,
    request_final_place,
)
from racing.services.heats import select_heat
from racing.services.playlist import (
    rebuild_waiting_round,
    round_ended,
    start_playlist,
    start_waiting_step,
)
from racing.services.reschedule import reschedule
from racing.services.results import Outcome, record_manual_result
from racing.services.rounds import (
    FollowOn,
    NewAggregate,
    new_round_roster,
    playlist_finalists,
    round_adjustments,
)
from racing.services.schedule import generate_schedule
from racing.services.tests.race import fields
from roster.models import Partition, Racer, Subgroup
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db

CARS = range(101, 109)
LATE = 109


def time_of(car: int) -> float:
    return 2.0 + (car - 100) / 100


class Day:
    """The scouts race, with the playlist of round 1 and a top 4 final."""

    def __init__(self):
        self.replay = Replay()
        self.event = self.replay.event
        self.replay.post("action=settings.write&unused-lane-mask=0&n-lanes=4")
        for car in [*CARS, LATE]:
            self.replay.import_racer(f"Scout{car}", "Racer", "Scouts", str(car))
        Racer.objects.filter(event=self.event, car_number__in=CARS).update(
            passed_inspection=True
        )
        self.round_1 = Round.objects.get(event=self.event, number=1)
        self.group = self.round_1.group
        for seq, (number, limit) in enumerate([(1, 0), (2, 4)], 1):
            PlaylistEntry.objects.create(
                event=self.event,
                seq=seq,
                group=self.group,
                round_number=number,
                bucket_limit=limit,
            )

    def racer(self, car: int) -> Racer:
        return Racer.objects.get(event=self.event, car_number=car)

    def cars(self, racers) -> list[int]:
        return [r.car_number for r in racers]

    def start(self) -> Round:
        return start_playlist(self.event, playlist_finalists())

    def race(self, round: Round, heats=None):
        for lane in HeatLane.objects.filter(round=round).select_related("racer"):
            if heats is None or lane.heat in heats:
                lane.finish_time = time_of(lane.racer.car_number)
                lane.completed = timezone.now()
                lane.save()
        RaceState.objects.filter(event=self.event).update(racing=False)

    def race_round_1(self) -> Round:
        """Round 1 raced and ended, so the final waits in the intermission
        with its chart made and its heat 1 selected."""
        round_1 = self.start()
        self.race(round_1)
        round_ended(round_1, playlist_finalists())
        return self.final()

    def final(self) -> Round:
        return Round.objects.get(group=self.group, number=2)

    def check_in_late(self) -> Racer:
        Racer.objects.filter(event=self.event, car_number=LATE).update(
            passed_inspection=True
        )
        return self.racer(LATE)

    def roster(self, round: Round) -> list[int]:
        return [e.racer.car_number for e in round.roster.order_by("id")]

    def chart(self, round: Round) -> dict[int, dict[int, int]]:
        chart: dict[int, dict[int, int]] = {}
        for heat, lane, car in HeatLane.objects.filter(round=round).values_list(
            "heat", "lane", "racer__car_number"
        ):
            chart.setdefault(heat, {})[lane] = car
        return chart

    def allowed_late_racer(self) -> Racer:
        late = self.check_in_late()
        allow_final_request(request_final_place(late))
        return self.racer(LATE)


@pytest.fixture
def day():
    return Day()


# ----- the request -----


def test_a_racer_who_checks_in_after_round_1_requests_a_place(day):
    day.race_round_1()
    late = day.check_in_late()
    request = request_final_place(late)
    assert request.state == FinalRequest.State.REQUESTED
    assert (request.racer, request.group) == (late, day.group)
    assert day.racer(LATE).not_eligible_for_speed_awards
    assert not day.racer(LATE).exclude


def test_a_racer_whose_round_1_has_unraced_heats_is_a_late_racer_instead(day):
    round_1 = day.start()
    day.race(round_1, heats={1})
    late = day.check_in_late()
    with pytest.raises(FinalsFailure) as caught:
        request_final_place(late)
    assert caught.value.code == Failure.LATE_RACER
    assert not FinalRequest.objects.exists()
    assert not day.racer(LATE).not_eligible_for_speed_awards
    assert round_adjustments(round_1).unscheduled == (late.pk,)


def test_a_racer_who_has_not_passed_check_in_cannot_request(day):
    day.race_round_1()
    with pytest.raises(FinalsFailure) as caught:
        request_final_place(day.racer(LATE))
    assert caught.value.code == Failure.NOT_CHECKED_IN
    assert not FinalRequest.objects.exists()


def test_a_racer_with_a_round_1_result_cannot_request(day):
    day.race_round_1()
    with pytest.raises(FinalsFailure) as caught:
        request_final_place(day.racer(105))
    assert caught.value.code == Failure.RACED_ROUND_1
    assert not day.racer(105).not_eligible_for_speed_awards


def test_a_racer_cannot_request_before_round_1_is_scheduled(day):
    late = day.check_in_late()
    with pytest.raises(FinalsFailure) as caught:
        request_final_place(late)
    assert caught.value.code == Failure.NOT_STARTED


def test_a_second_request_returns_the_first(day):
    day.race_round_1()
    late = day.check_in_late()
    first = request_final_place(late)
    decline_final_request(first)
    again = request_final_place(late)
    assert again.pk == first.pk
    assert again.state == FinalRequest.State.DECLINED
    assert FinalRequest.objects.count() == 1


# ----- allow and decline -----


@pytest.mark.parametrize("decision", ["allow", "decline"])
def test_a_top_4_final_holds_the_allowed_racer_as_an_extra(day, decision):
    """`roster.new` for the final reads the allowed racers itself."""
    round_1 = day.start()
    day.race(round_1)
    request = request_final_place(day.check_in_late())
    if decision == "allow":
        allow_final_request(request)
    else:
        decline_final_request(request)
    made = new_round_roster(FollowOn(round_1), top=4)
    expected = [101, 102, 103, 104] + ([LATE] if decision == "allow" else [])
    assert day.roster(made.round) == expected
    assert [f.racer_id for f in made.qualifiers] == [
        day.racer(c).pk for c in (101, 102, 103, 104)
    ]


def test_the_playlist_activation_enters_a_racer_allowed_before_the_final_exists(day):
    round_1 = day.start()
    day.race(round_1)
    day.allowed_late_racer()
    assert not Round.objects.filter(group=day.group, number=2).exists()
    round_ended(round_1, playlist_finalists())
    final = day.final()
    assert day.roster(final) == [101, 102, 103, 104, LATE]
    assert HeatLane.objects.filter(round=final, racer__car_number=LATE).exists()


def test_a_rebuilt_waiting_final_keeps_the_allowed_racer(day):
    """D27: a round 1 result changes during the intermission, and the
    waiting final is made again with the allowed racer still in it."""
    final = day.race_round_1()
    day.allowed_late_racer()
    HeatLane.objects.filter(round=day.round_1, racer__car_number=106).update(
        finish_time=1.9
    )
    assert rebuild_waiting_round(day.event, playlist_finalists())
    assert day.roster(final) == [106, 101, 102, 103, LATE]
    assert HeatLane.objects.filter(round=final, racer__car_number=LATE).exists()


def test_allowing_into_a_final_with_a_chart_adds_a_late_racer(day):
    final = day.race_round_1()
    chart = day.chart(final)
    late = day.allowed_late_racer()
    assert day.roster(final) == [101, 102, 103, 104, LATE]
    assert day.chart(final) == chart
    assert round_adjustments(final).unscheduled == (late.pk,)

    preview = reschedule(final, late.pk, dry_run=True)
    assert str(late.pk) in " ".join(line or "" for line in preview.chart).split()
    assert day.chart(final) == chart


def test_add_to_schedule_keeps_the_running_and_staged_heats(day):
    """D34: heat 1 of the final runs and heat 2 is staged."""
    final = day.race_round_1()
    start_waiting_step(day.event)
    state = RaceState.objects.get(event=day.event)
    assert (state.current_round, state.current_heat, state.racing) == (final, 1, True)
    before = day.chart(final)
    late = day.allowed_late_racer()
    reschedule(final, late.pk)
    after = day.chart(final)
    assert (after[1], after[2]) == (before[1], before[2])
    assert any(LATE in heat.values() for heat in after.values())


def test_an_allowed_racer_joins_only_the_final(day):
    """A round after the final takes its racers from the final's standings,
    where the allowed racer cannot qualify (D34)."""
    final = raced_final(day)
    day.race(final)
    made = new_round_roster(FollowOn(final), top=2)
    assert LATE not in day.roster(made.round)


def test_declining_an_allowed_request_takes_the_racer_off_the_final(day):
    final = day.race_round_1()
    day.allowed_late_racer()
    request = FinalRequest.objects.get()
    decline_final_request(request)
    assert day.roster(final) == [101, 102, 103, 104]
    request.refresh_from_db()
    assert request.state == FinalRequest.State.DECLINED


def test_allowing_twice_enters_the_racer_once(day):
    final = day.race_round_1()
    day.allowed_late_racer()
    allow_final_request(FinalRequest.objects.get())
    assert day.roster(final) == [101, 102, 103, 104, LATE]


# ----- suggestions -----


def raced_final(day) -> Round:
    """The final with an allowed racer fitted in and heat 1 raced."""
    final = day.race_round_1()
    late = day.allowed_late_racer()
    reschedule(final, late.pk)
    start_waiting_step(day.event)
    day.race(final, heats={1})
    return final


def coordinator_changes_time(day, car: int, time: float):
    """The race coordinator selects each of the car's round 1 heats and
    types a new time for it, while round 1's results are locked."""
    for lane in HeatLane.objects.filter(round=day.round_1, racer__car_number=car):
        assert select_heat(day.event, round_id=day.round_1.pk, heat=lane.heat) is None
        posted = fields(f"lane{lane.lane}={time}")
        locked = record_manual_result(day.event, posted, control_race=False)
        assert locked.outcome is Outcome.RESULTS_LOCKED
        assert record_manual_result(day.event, posted).outcome is Outcome.RECORDED


def test_a_changed_round_1_result_suggests_one_racer_in_and_one_out(day):
    final = raced_final(day)
    roster, chart = day.roster(final), day.chart(final)
    coordinator_changes_time(day, 105, 2.035)
    suggested = finalist_suggestions(final)
    assert day.cars(suggested.add) == [105]
    assert day.cars(suggested.remove) == [104]
    assert (day.roster(final), day.chart(final)) == (roster, chart)


def test_no_change_suggests_nothing_and_the_allowed_racer_is_never_named(day):
    final = raced_final(day)
    suggested = finalist_suggestions(final)
    assert (suggested.add, suggested.remove) == ((), ())


def test_a_racer_added_from_a_suggestion_stays_eligible_for_speed_awards(day):
    final = raced_final(day)
    coordinator_changes_time(day, 105, 2.035)
    add_to_final(day.racer(105), final)
    assert 105 in day.roster(final)
    assert not HeatLane.objects.filter(round=final, racer__car_number=105).exists()
    assert not day.racer(105).not_eligible_for_speed_awards
    assert day.racer(LATE).not_eligible_for_speed_awards
    suggested = finalist_suggestions(final)
    assert (day.cars(suggested.add), day.cars(suggested.remove)) == ([], [104])

    removed = day.racer(104)
    remove_from_final(removed, final)
    assert 104 not in day.roster(final)
    unraced = HeatLane.objects.filter(round=final, racer=removed).exclude(HAS_RESULT)
    if unraced.exists():
        assert removed.pk in round_adjustments(final).departed
    assert finalist_suggestions(final) == finals.Suggestions(final, (), ())


def test_removing_an_allowed_racer_declines_the_request(day):
    """Otherwise the next rebuild of the final would enter the racer again."""
    final = day.race_round_1()
    late = day.allowed_late_racer()
    remove_from_final(late, final)
    assert FinalRequest.objects.get().state == FinalRequest.State.DECLINED
    assert day.roster(final) == [101, 102, 103, 104]


def test_add_and_remove_refuse_racers_on_or_off_the_roster(day):
    final = day.race_round_1()
    with pytest.raises(FinalsFailure) as caught:
        add_to_final(day.racer(101), final)
    assert caught.value.code == Failure.ON_ROSTER
    with pytest.raises(FinalsFailure) as caught:
        remove_from_final(day.racer(108), final)
    assert caught.value.code == Failure.NOT_ON_ROSTER
    with pytest.raises(FinalsFailure) as caught:
        add_to_final(day.racer(101), day.round_1)
    assert caught.value.code == Failure.NOT_A_FINAL


def test_add_to_final_refuses_a_racer_of_another_group(day):
    final = day.race_round_1()
    other = day.replay.import_racer("Outlaw", "Racer", "Outlaws", "201")
    with pytest.raises(FinalsFailure) as caught:
        add_to_final(other, final)
    assert caught.value.code == Failure.WRONG_GROUP


def test_the_cut_skips_a_racer_not_eligible_for_speed_awards(day):
    """D34: the second-fastest racer cannot win speed awards, so the top 4
    are the first, third, fourth and fifth racers, and nothing is
    suggested."""
    Racer.objects.filter(event=day.event, car_number=102).update(
        not_eligible_for_speed_awards=True
    )
    final = day.race_round_1()
    assert day.roster(final) == [101, 103, 104, 105]
    start_waiting_step(day.event)
    day.race(final, heats={1})
    suggested = finalist_suggestions(final)
    assert (suggested.add, suggested.remove) == ((), ())


def test_a_suggestion_never_names_a_racer_with_an_award_setting(day):
    final = raced_final(day)
    coordinator_changes_time(day, 105, 2.035)
    Racer.objects.filter(event=day.event, car_number=105).update(exclude=True)
    Racer.objects.filter(event=day.event, car_number=103).update(
        not_eligible_for_speed_awards=True
    )
    suggested = finalist_suggestions(final)
    named = {*day.cars(suggested.add), *day.cars(suggested.remove)}
    assert named.isdisjoint({103, 105, LATE})


def test_an_allowed_racer_made_eligible_is_still_not_a_qualifier(day):
    """D33: the race coordinator can clear an allowed racer's speed award
    setting. The racer has no round 1 result, so the cut never picks the
    racer, and a suggestion must not take the racer out."""
    final = raced_final(day)
    Racer.objects.filter(event=day.event, car_number=LATE).update(
        not_eligible_for_speed_awards=False
    )
    suggested = finalist_suggestions(final)
    assert (suggested.add, suggested.remove) == ((), ())


def test_suggestions_read_the_round_before_the_final_after_it_finishes(day):
    """Once the final finishes it is the group's highest finished round,
    and its own times must not pick its racers."""
    final = raced_final(day)
    coordinator_changes_time(day, 105, 2.035)
    day.race(final)
    suggested = finalist_suggestions(final)
    assert day.cars(suggested.add) == [105]
    assert day.cars(suggested.remove) == [104]


# ----- the poll -----


def test_the_poll_lists_open_requests_and_the_suggestions_of_raced_finals(day):
    final = day.race_round_1()
    late = day.check_in_late()
    request = request_final_place(late)
    assert poll_finals(day.event) == {
        "final-requests": [
            {
                "requestid": request.pk,
                "racerid": late.pk,
                "classid": day.group.pk,
                "state": "requested",
            }
        ],
        "finalist-suggestions": [],
    }
    allow_final_request(request)
    reschedule(final, late.pk)
    start_waiting_step(day.event)
    day.race(final, heats={1})
    coordinator_changes_time(day, 105, 2.035)
    assert poll_finals(day.event) == {
        "final-requests": [],
        "finalist-suggestions": [
            {
                "roundid": final.pk,
                "classid": day.group.pk,
                "add": [day.racer(105).pk],
                "remove": [day.racer(104).pk],
            }
        ],
    }


def test_writers_take_the_race_state_lock(day, monkeypatch):
    """Every writer of the roster or a racer's award setting locks first."""
    final = day.race_round_1()
    late = day.check_in_late()
    locked = []
    real = finals.lock_race_state

    def spy(event_id):
        locked.append(event_id)
        return real(event_id)

    monkeypatch.setattr(finals, "lock_race_state", spy)
    request = request_final_place(late)
    allow_final_request(request)
    decline_final_request(request)
    add_to_final(day.racer(108), final)
    remove_from_final(day.racer(108), final)
    assert locked == [day.event.pk] * 5


def hand_built_final(day) -> Round:
    """Round 1 raced, then a top 4 final built by hand with `roster.new`,
    with no playlist step, and its heat 1 raced."""
    round_1 = day.start()
    day.race(round_1)
    PlaylistEntry.objects.filter(event=day.event).delete()
    final = new_round_roster(FollowOn(round_1), top=4).round
    generate_schedule(final, 1)
    day.race(final, heats={1})
    return final


def test_a_final_built_by_hand_keeps_its_cut_for_suggestions(day):
    """D43."""
    final = hand_built_final(day)
    final.refresh_from_db()
    assert (final.cut_top, final.cut_bucketed) == (4, False)
    coordinator_changes_time(day, 105, 2.035)
    suggested = finalist_suggestions(final)
    assert (day.cars(suggested.add), day.cars(suggested.remove)) == ([105], [104])
    assert poll_finals(day.event)["finalist-suggestions"] == [
        {
            "roundid": final.pk,
            "classid": day.group.pk,
            "add": [day.racer(105).pk],
            "remove": [day.racer(104).pk],
        }
    ]


def test_a_playlist_final_keeps_the_cut_it_was_built_with(day):
    """D43: the final keeps its number even if its playlist step changes
    afterwards."""
    final = raced_final(day)
    final.refresh_from_db()
    assert (final.cut_top, final.cut_bucketed) == (4, False)
    PlaylistEntry.objects.filter(event=day.event, round_number=2).update(bucket_limit=5)
    suggested = finalist_suggestions(final)
    assert (suggested.add, suggested.remove) == ((), ())


def test_a_final_with_no_stored_cut_is_reported_by_the_poll(day):
    """A round made before D43, as an import makes, has no stored cut. The
    poll says so instead of leaving the final out."""
    final = hand_built_final(day)
    Round.objects.filter(pk=final.pk).update(cut_top=None)
    with pytest.raises(FinalsFailure) as caught:
        finalist_suggestions(final)
    assert caught.value.code == Failure.NO_CUT
    suggested = finalist_suggestions(final, top=4)
    assert (suggested.add, suggested.remove) == ((), ())
    assert poll_finals(day.event)["finalist-suggestions"] == [
        {
            "roundid": final.pk,
            "classid": day.group.pk,
            "failure": "no-cut",
            "add": [],
            "remove": [],
        }
    ]


# ----- a combined final (D44) -----


def test_an_allowed_racer_goes_into_a_combined_final_drawn_from_the_group(day):
    round_1 = day.start()
    day.race(round_1)
    PlaylistEntry.objects.filter(event=day.event).delete()
    day.allowed_late_racer()
    made = new_round_roster(NewAggregate("Grand", groups=[day.group]), top=4)
    assert day.roster(made.round) == [101, 102, 103, 104, LATE]
    assert made.extras == (day.racer(LATE),)


def test_an_allowed_racer_goes_into_a_combined_final_drawn_from_subgroups(day):
    round_1 = day.start()
    day.race(round_1)
    PlaylistEntry.objects.filter(event=day.event).delete()
    day.allowed_late_racer()
    subgroup = day.racer(LATE).subgroup
    made = new_round_roster(NewAggregate("Grand", subgroups=[subgroup]), top=4)
    assert made.extras == (day.racer(LATE),)
    assert LATE in day.roster(made.round)


def second_subgroup(day, car: int):
    """Move the car into a second subgroup of the Scouts group."""
    subgroup = Subgroup.objects.create(
        event=day.event, group=day.group, name="Bears", sort_order=2
    )
    partition = Partition.objects.create(
        event=day.event, subgroup=subgroup, name="Bears", sort_order=2
    )
    Racer.objects.filter(event=day.event, car_number=car).update(
        subgroup=subgroup, partition=partition
    )
    return subgroup


@pytest.mark.parametrize("draws_from", ["its subgroup", "another subgroup"])
def test_a_combined_final_drawn_from_subgroups_takes_only_their_racers(day, draws_from):
    """D44: the racer's group races in the combined round only through the
    subgroups it draws from."""
    round_1 = day.start()
    day.race(round_1)
    PlaylistEntry.objects.filter(event=day.event).delete()
    RaceSettings.objects.filter(event=day.event).update(use_subgroups=True)
    others = day.racer(101).subgroup
    bears = second_subgroup(day, LATE)
    day.allowed_late_racer()
    subgroup = bears if draws_from == "its subgroup" else others
    made = new_round_roster(NewAggregate("Grand", subgroups=[subgroup]), top=4)
    assert (LATE in day.roster(made.round)) == (draws_from == "its subgroup")
    if draws_from == "another subgroup":
        with pytest.raises(FinalsFailure) as caught:
            add_to_final(day.racer(LATE), made.round)
        assert caught.value.code == Failure.WRONG_GROUP


def test_a_racer_moved_to_another_group_is_not_entered_in_its_final(day):
    """Review finding 1: the request names the old group, so the new
    group's final does not take the racer either. The racer is allowed
    before any final exists, so the request is on no round yet."""
    round_1 = day.start()
    day.race(round_1)
    day.allowed_late_racer()
    move_to_another_group(day, LATE)
    webelos = day.racer(LATE).group
    entry = PlaylistEntry.objects.create(
        event=day.event, seq=3, group=webelos, round_number=2, bucket_limit=4
    )
    final = Round.objects.create(event=day.event, group=webelos, number=2)
    entrants = playlist_finalists()(entry, final)
    assert day.racer(LATE) not in entrants.racers


def test_allowing_into_a_combined_final_with_a_roster_adds_the_racer(day):
    round_1 = day.start()
    day.race(round_1)
    PlaylistEntry.objects.filter(event=day.event).delete()
    grand = new_round_roster(NewAggregate("Grand", groups=[day.group]), top=4).round
    generate_schedule(grand, 1)
    late = day.allowed_late_racer()
    assert day.roster(grand) == [101, 102, 103, 104, LATE]
    assert round_adjustments(grand).unscheduled == (late.pk,)
    day.race(grand, heats={1})
    suggested = finalist_suggestions(grand)
    assert (suggested.add, suggested.remove) == ((), ())


def test_an_allowed_racer_joins_only_the_next_round_of_the_group(day):
    """The group's own final came first, so a combined round built later
    does not take the allowed racer again."""
    day.race_round_1()
    day.allowed_late_racer()
    made = new_round_roster(NewAggregate("Grand", groups=[day.group]), top=4)
    assert LATE not in day.roster(made.round)


# ----- requests that no longer apply -----


def move_to_another_group(day, car: int):
    other = day.replay.import_racer("Webelo", "Racer", "Webelos", "301")
    Racer.objects.filter(event=day.event, car_number=car).update(
        group=other.group, subgroup=other.subgroup, partition=other.partition
    )


def test_a_racer_moved_to_another_group_leaves_the_old_groups_final(day):
    """Review finding 1: the request names the old group."""
    final = day.race_round_1()
    day.allowed_late_racer()
    move_to_another_group(day, LATE)
    HeatLane.objects.filter(round=day.round_1, racer__car_number=106).update(
        finish_time=1.9
    )
    assert rebuild_waiting_round(day.event, playlist_finalists())
    assert LATE not in day.roster(final)


def finished_final(day) -> Round:
    """Round 1 and the whole final raced."""
    final = day.race_round_1()
    start_waiting_step(day.event)
    day.race(final)
    return final


def test_a_racer_allowed_after_the_final_has_run_goes_into_the_combined_round(day):
    """D44: a finished final is not the next round the group races in."""
    final = finished_final(day)
    grand = new_round_roster(NewAggregate("Grand", groups=[day.group]), top=2).round
    day.allowed_late_racer()
    assert LATE in day.roster(grand)
    assert LATE not in day.roster(final)


def test_an_allow_after_the_final_has_run_waits_for_the_next_round(day):
    final = finished_final(day)
    day.allowed_late_racer()
    assert LATE not in day.roster(final)
    grand = new_round_roster(NewAggregate("Grand", groups=[day.group]), top=2)
    assert day.roster(grand.round) == [101, 102, LATE]


def test_a_racer_who_raced_the_final_as_allowed_is_not_entered_again(day):
    """An allowed request is spent on the first round it puts the racer
    in, so a combined round built after the final does not take the racer."""
    final = raced_final(day)
    day.race(final)
    grand = new_round_roster(NewAggregate("Grand", groups=[day.group]), top=2)
    assert day.roster(grand.round) == [101, 102]
    assert FinalRequest.objects.get().placed_round == final


def test_a_rebuild_keeps_the_racer_bound_to_the_round(day):
    """D27: the rebuild clears the waiting final's roster, and the request
    still names the final, so the racer comes back."""
    final = day.race_round_1()
    day.allowed_late_racer()
    assert FinalRequest.objects.get().placed_round == final
    HeatLane.objects.filter(round=day.round_1, racer__car_number=106).update(
        finish_time=1.9
    )
    assert rebuild_waiting_round(day.event, playlist_finalists())
    assert LATE in day.roster(final)
    assert FinalRequest.objects.get().placed_round == final


@pytest.mark.parametrize("first", ["final", "combined round"])
def test_a_racer_allowed_before_any_final_joins_the_round_built_first(day, first):
    round_1 = day.start()
    day.race(round_1)
    PlaylistEntry.objects.filter(event=day.event).delete()
    day.allowed_late_racer()
    builds = [
        lambda: new_round_roster(FollowOn(round_1), top=4).round,
        lambda: (
            new_round_roster(NewAggregate("Grand", groups=[day.group]), top=2).round
        ),
    ]
    if first == "combined round":
        builds.reverse()
    made_first, made_second = (build() for build in builds)
    assert LATE in day.roster(made_first)
    assert LATE not in day.roster(made_second)
    assert FinalRequest.objects.get().placed_round == made_first


def test_an_allow_with_two_rounds_waiting_goes_into_the_one_made_first(day):
    """D44: both the final and a combined round exist, neither raced."""
    round_1 = day.start()
    day.race(round_1)
    PlaylistEntry.objects.filter(event=day.event).delete()
    final = new_round_roster(FollowOn(round_1), top=4).round
    grand = new_round_roster(NewAggregate("Grand", groups=[day.group]), top=2).round
    day.allowed_late_racer()
    assert LATE in day.roster(final)
    assert LATE not in day.roster(grand)


def test_declining_unbinds_so_a_new_allow_finds_the_next_round(day):
    final = day.race_round_1()
    day.allowed_late_racer()
    request = decline_final_request(FinalRequest.objects.get())
    assert request.placed_round is None
    allow_final_request(request)
    assert LATE in day.roster(final)
    assert FinalRequest.objects.get().placed_round == final


def test_an_allowed_racer_in_a_finished_final_is_still_not_a_qualifier(day):
    """The finished final is no longer the racer's next round, and the
    racer is still there as an allowed racer, eligible or not (D33)."""
    final = raced_final(day)
    day.race(final)
    Racer.objects.filter(event=day.event, car_number=LATE).update(
        not_eligible_for_speed_awards=False
    )
    suggested = finalist_suggestions(final)
    assert (suggested.add, suggested.remove) == ((), ())


def test_removing_an_allowed_racer_from_a_finished_final_declines_it(day):
    final = raced_final(day)
    day.race(final)
    remove_from_final(day.racer(LATE), final)
    assert FinalRequest.objects.get().state == FinalRequest.State.DECLINED


def test_the_poll_leaves_out_a_request_that_no_longer_applies(day):
    day.race_round_1()
    request_final_place(day.check_in_late())
    move_to_another_group(day, LATE)
    assert poll_finals(day.event)["final-requests"] == []


def test_allowing_a_racer_moved_to_another_group_is_refused(day):
    final = day.race_round_1()
    request = request_final_place(day.check_in_late())
    move_to_another_group(day, LATE)
    with pytest.raises(FinalsFailure) as caught:
        allow_final_request(request)
    assert caught.value.code == Failure.WRONG_GROUP
    assert LATE not in day.roster(final)
    assert FinalRequest.objects.get().state == FinalRequest.State.REQUESTED


def test_a_reverted_check_in_leaves_the_final_at_the_next_rebuild(day):
    """Review finding 3, D36: a reverted check-in departs the racer."""
    final = day.race_round_1()
    day.allowed_late_racer()
    Racer.objects.filter(event=day.event, car_number=LATE).update(
        passed_inspection=False
    )
    HeatLane.objects.filter(round=day.round_1, racer__car_number=106).update(
        finish_time=1.9
    )
    assert rebuild_waiting_round(day.event, playlist_finalists())
    assert LATE not in day.roster(final)


def test_allowing_a_racer_whose_check_in_was_reverted_is_refused(day):
    final = day.race_round_1()
    request = request_final_place(day.check_in_late())
    Racer.objects.filter(event=day.event, car_number=LATE).update(
        passed_inspection=False
    )
    with pytest.raises(FinalsFailure) as caught:
        allow_final_request(request)
    assert caught.value.code == Failure.NOT_CHECKED_IN
    assert LATE not in day.roster(final)


def test_the_poll_lists_a_declined_request_so_it_can_still_be_allowed(day):
    """Review finding 5."""
    final = day.race_round_1()
    late = day.check_in_late()
    request = decline_final_request(request_final_place(late))
    assert poll_finals(day.event)["final-requests"] == [
        {
            "requestid": request.pk,
            "racerid": late.pk,
            "classid": day.group.pk,
            "state": "declined",
        }
    ]
    allow_final_request(request)
    assert LATE in day.roster(final)
    assert poll_finals(day.event)["final-requests"] == []


def test_requests_are_per_event(day):
    other = Day()
    other.race_round_1()
    day.race_round_1()
    request_final_place(other.check_in_late())
    assert poll_finals(day.event)["final-requests"] == []
    assert RaceSettings.objects.count() == 2
