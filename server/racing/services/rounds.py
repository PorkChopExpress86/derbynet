"""Rounds and rosters (#25): follow-on and aggregate rounds, and the round
counts the coordinator page shows.

Ported from `make_new_round` and `write_roster_entries` in
`website/inc/roster.inc`, `prepare_new_round` in
`website/ajax/action.roster.new.inc`, `website/ajax/action.roster.delete.inc`,
`maybe_clean_up_aggregate_class_for_round` in `website/inc/aggregate_round.inc`
and `all_rounds_with_counts` in `website/inc/rounds.inc`. Choosing finalists
reads the standings, so it belongs to #26; `write_roster` takes them as
input. Writers take the `RaceState` lock first, as `generate_schedule`
needs.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from django.db import transaction
from django.db.models.functions import Lower

from core.services import lock_race_state
from racing.engine.groups.aggregates import Aggregate, ready_aggregates
from racing.models import HAS_RESULT, HeatLane, RosterEntry, Round
from roster.models import Group, GroupConstituent, GroupConstituentSubgroup, Racer
from roster.services.groups import (
    delete_group_rows,
    delete_rounds,
    is_aggregate,
    reset_full_field,
)


class Failure(StrEnum):
    SCHEDULED = "scheduled"
    FIRST_ROUND = "first_round"
    CONSTITUENT = "constituent"


class RoundFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def make_new_round(group: Group, number: int) -> Round:
    with transaction.atomic():
        lock_race_state(group.event_id)
        return Round.objects.create(event_id=group.event_id, group=group, number=number)


def prepare_new_round(group: Group, number: int) -> Round:
    """Round `number` of `group` with an empty roster: the existing round
    with its roster cleared, or a new round."""
    with transaction.atomic():
        lock_race_state(group.event_id)
        round = Round.objects.filter(group=group, number=number).first()
        if round is None:
            return make_new_round(group, number)
        RosterEntry.objects.filter(round=round).delete()
        return round


def write_roster(round: Round, finalists: Iterable[Racer]) -> None:
    """Enter the finalists in the round, in the order given, which is the
    order the schedule takes them in."""
    with transaction.atomic():
        lock_race_state(round.event_id)
        RosterEntry.objects.bulk_create(
            RosterEntry(event_id=round.event_id, round=round, racer=racer)
            for racer in finalists
        )


def delete_round_roster(round: Round) -> None:
    """`roster.delete`: delete a round that has no heats, and its roster.

    Round 1 of an organic group stays. An aggregate group that is not
    durable goes with its last round, along with its playlist steps, unless
    another aggregate draws from it; then the round stays and this fails
    with `constituent`, as `class.delete` does. A full-field rule that names
    a group goes when the group's last round does. If the round is the
    current one, racing stops and no heat is current.
    """
    with transaction.atomic():
        lock_race_state(round.event_id)
        if HeatLane.objects.filter(round=round).exists():
            raise RoundFailure(
                Failure.SCHEDULED,
                "Heats are scheduled for this round; remove before proceeding",
            )
        aggregate = is_aggregate(round.group_id)
        if round.number == 1 and not aggregate:
            raise RoundFailure(
                Failure.FIRST_ROUND, "Can't remove a first round of an organic class."
            )
        group = Group.objects.get(pk=round.group_id)
        last = not group.rounds.exclude(pk=round.pk).exists()
        goes = last and aggregate and not group.durable
        if goes and GroupConstituent.objects.filter(constituent=group).exists():
            raise RoundFailure(
                Failure.CONSTITUENT, "Unable to delete a constituent class"
            )
        delete_rounds(round.event_id, Round.objects.filter(pk=round.pk))
        if last:
            reset_full_field(round.event_id, [group])
            if goes:
                delete_group_rows(group)


@dataclass(frozen=True)
class RoundCounts:
    """One row of `all_rounds_with_counts`."""

    round: Round
    name: str
    aggregate: bool
    roster_size: int
    passed: int
    unscheduled: int
    departed: int
    heats_scheduled: int
    heats_run: int


def round_counts(event) -> list[RoundCounts]:
    """Every round of the event, by group sort order, group name and round
    number, with its roster and chart counts.

    `unscheduled` counts passed racers in the roster with no heat.
    `departed` counts racers in unraced heats who are no longer passed and in
    the roster. A heat is run once one of its lanes has a result.
    """
    rounds = list(
        Round.objects.filter(event=event)
        .select_related("group")
        .order_by("group__sort_order", Lower("group__name"), "number")
    )
    aggregates = set(
        GroupConstituent.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    ) | set(
        GroupConstituentSubgroup.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    )
    with_group = Group.objects.filter(event=event).count() > 1

    roster: dict[int, dict[int, bool]] = {}
    for round_id, racer_id, passed in RosterEntry.objects.filter(
        event=event
    ).values_list("round_id", "racer_id", "racer__passed_inspection"):
        roster.setdefault(round_id, {})[racer_id] = passed

    charted: dict[int, set[int]] = {}
    heats: dict[int, set[int]] = {}
    run: dict[int, set[int]] = {}
    unraced: dict[int, set[int]] = {}
    lanes = HeatLane.objects.filter(event=event).values_list(
        "round_id", "heat", "racer_id", "finish_time", "finish_place"
    )
    for round_id, heat, racer_id, time, place in lanes:
        charted.setdefault(round_id, set()).add(racer_id)
        heats.setdefault(round_id, set()).add(heat)
        if time is not None or place is not None:
            run.setdefault(round_id, set()).add(heat)
        else:
            unraced.setdefault(round_id, set()).add(racer_id)

    counts = []
    for round in rounds:
        entered = roster.get(round.id, {})
        passed = {racer for racer, ok in entered.items() if ok}
        name = f"Round {round.number}"
        counts.append(
            RoundCounts(
                round=round,
                name=f"{round.group.name}, {name}" if with_group else name,
                aggregate=round.group_id in aggregates,
                roster_size=len(entered),
                passed=len(passed),
                unscheduled=len(passed - charted.get(round.id, set())),
                departed=len(unraced.get(round.id, set()) - passed),
                heats_scheduled=len(heats.get(round.id, ())),
                heats_run=len(run.get(round.id, ())),
            )
        )
    return counts


def round_is_complete(round: Round) -> bool:
    """No heat lane of the round lacks a result. True for a round with no
    chart, as `round_is_completed` in `playlist.inc` is. PHP tests
    `completed`; this uses the one result predicate of Q11."""
    return not HeatLane.objects.filter(round=round).exclude(HAS_RESULT).exists()


def ready_aggregate_groups(event) -> list[Group]:
    """The `ready-aggregate` list of `poll.coordinator`: aggregate groups with
    no round whose constituents have no unraced heat, in group sort order.
    PHP tests `completed` for an unraced heat; this uses the result
    predicate of Q11."""
    aggregates = list(
        Group.objects.filter(event=event, constituent_links__isnull=False)
        .distinct()
        .order_by("sort_order", "id")
    )
    links: dict[int, list[int]] = {}
    for aggregate_id, constituent_id in (
        GroupConstituent.objects.filter(event=event)
        .order_by("id")
        .values_list("aggregate_id", "constituent_id")
    ):
        links.setdefault(aggregate_id, []).append(constituent_id)
    graph = [Aggregate(g.id, tuple(links[g.id])) for g in aggregates]
    racing = set(Round.objects.filter(event=event).values_list("group_id", flat=True))
    unfinished = set(
        HeatLane.objects.filter(event=event)
        .exclude(HAS_RESULT)
        .values_list("round__group_id", flat=True)
    )
    by_id = {g.id: g for g in aggregates}
    return [by_id[i] for i in ready_aggregates(graph, racing, unfinished)]
