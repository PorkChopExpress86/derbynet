"""Rounds and rosters (#25): follow-on and aggregate rounds, and the round
counts the coordinator page shows.

Ported from `make_new_round` and `write_roster_entries` in
`website/inc/roster.inc`, `prepare_new_round` and the rest of
`website/ajax/action.roster.new.inc`, `website/ajax/action.roster.delete.inc`,
`maybe_clean_up_aggregate_class_for_round` in `website/inc/aggregate_round.inc`
and `all_rounds_with_counts` in `website/inc/rounds.inc`. `new_round_roster`
and `playlist_finalists` choose a new round's racers from the standings
(#26). Writers take the `RaceState` lock first, as `generate_schedule`
needs.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from django.db import transaction
from django.db.models.functions import Lower

from core.models import FullFieldRule, RaceSettings
from core.services import lock_race_state
from racing.engine.groups.aggregates import Aggregate, ready_aggregates
from racing.engine.standings import finalists
from racing.models import HAS_RESULT, HeatLane, PlaylistEntry, RosterEntry, Round
from racing.services.standings import standings_snapshot
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Racer,
    Subgroup,
)
from roster.services.groups import (
    create_aggregate_group,
    delete_group_rows,
    delete_rounds,
    is_aggregate,
    reset_full_field,
)


class Failure(StrEnum):
    SCHEDULED = "scheduled"
    FIRST_ROUND = "first_round"
    CONSTITUENT = "constituent"
    NO_RACERS = "no_racers"


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


@dataclass(frozen=True)
class FollowOn:
    """The next round of the group that raced `round`."""

    round: Round


@dataclass(frozen=True)
class FromAggregate:
    """Round 1 of an existing aggregate group."""

    group: Group


@dataclass(frozen=True)
class NewAggregate:
    """Round 1 of a new aggregate group, which also decides the pack's
    awards from then on, as PHP assumes it is the grand final. It draws
    from `subgroups` when any are given, else from `groups`."""

    name: str
    groups: Sequence[Group] = ()
    subgroups: Sequence[Subgroup] = ()


RosterSource = FollowOn | FromAggregate | NewAggregate


@dataclass(frozen=True)
class Entrants:
    """A new round's racers in roster order, and the cut's concerns, which
    the playlist's intermission shows for D28."""

    racers: tuple[Racer, ...]
    concerns: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewRoster:
    round: Round
    qualifiers: tuple[finalists.Finalist, ...]
    extras: tuple[Racer, ...]
    concerns: tuple[str, ...]


def _choose(
    cut: finalists.Cut, extras: Sequence[Racer]
) -> tuple[finalists.Cut, tuple[Racer, ...], tuple[Racer, ...]]:
    """The cut with each racer once, the qualifiers' racers, and the extras
    who did not qualify, each once (D32)."""
    cut = cut.unique()
    qualifiers = cut.finalists
    by_id = Racer.objects.in_bulk([f.racer_id for f in qualifiers])
    chosen = set(by_id)
    added = tuple({r.pk: r for r in extras if r.pk not in chosen}.values())
    return cut, tuple(by_id[f.racer_id] for f in qualifiers), added


def new_round_roster(
    source: RosterSource,
    top: int,
    bucketed: bool = False,
    extras: Sequence[Racer] = (),
) -> NewRoster:
    """`roster.new`: make the round, or clear the roster of the one that
    exists, and enter the racers the standings qualify, then `extras`.

    `top` is how many racers to take, or with `bucketed` how many from each
    group or subgroup the round draws from; a tie at the cut takes more.
    Extras are racers the race coordinator allowed in (D32). They follow the
    qualifiers and never take a qualifier's place, so a rebuild that passes
    the same extras keeps them. Fails with `no_racers`, and changes nothing,
    when the roster would be empty or a new aggregate draws from nothing.
    """
    if isinstance(source, FollowOn):
        event_id = source.round.event_id
    elif isinstance(source, FromAggregate):
        event_id = source.group.event_id
    else:
        members = [*source.groups, *source.subgroups]
        if not members:
            raise RoundFailure(Failure.NO_RACERS, "No eligible racers found.")
        event_id = members[0].event_id
    with transaction.atomic():
        lock_race_state(event_id)
        if isinstance(source, FollowOn):
            group = source.round.group
            round = prepare_new_round(group, source.round.number + 1)
            choose = finalists.follow_on
        else:
            if isinstance(source, NewAggregate):
                group = _new_aggregate(event_id, source)
            else:
                group = source.group
            round = prepare_new_round(group, 1)
            choose = finalists.for_aggregate
        standings = standings_snapshot(round.event).standings
        cut, qualifiers, added = _choose(
            choose(standings, group.id, bucketed=bucketed, limit=top), extras
        )
        if not qualifiers and not added:
            raise RoundFailure(Failure.NO_RACERS, "No eligible racers found.")
        write_roster(round, qualifiers + added)
        return NewRoster(round, cut.finalists, added, cut.concerns)


def _new_aggregate(event_id, source: NewAggregate) -> Group:
    """`create_aggregate_class()`, then `full-field-calc` set to it."""
    group = create_aggregate_group(
        event_id, source.name, source.groups, source.subgroups
    )
    RaceSettings.objects.filter(event_id=event_id).update(
        full_field_rule=FullFieldRule.GROUP, full_field_group=group
    )
    return group


StepKey = tuple[int, int]
"""A playlist step's round: its group's id and its round number."""


def playlist_finalists(
    extras: Mapping[StepKey, Sequence[Racer]] = MappingProxyType({}),
):
    """The playlist's `FinalistsFor`: the racers of a step's round, as
    `activate_playlist_entry()` chooses them, then that round's extras
    (D32), keyed by the step's group and round number.

    Round 1 of a group is an aggregate's first round; any later round
    follows on from the group's own standings. The step's `bucket_limit`
    and `bucketed` set the cut.
    """

    def finalists_for(entry: PlaylistEntry, round: Round) -> Entrants:
        standings = standings_snapshot(round.event).standings
        choose = (
            finalists.for_aggregate if entry.round_number == 1 else finalists.follow_on
        )
        cut = choose(
            standings,
            entry.group_id,
            bucketed=entry.bucketed,
            limit=entry.bucket_limit,
        )
        cut, qualifiers, added = _choose(
            cut, extras.get((entry.group_id, entry.round_number), ())
        )
        return Entrants(qualifiers + added, cut.concerns)

    return finalists_for


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
