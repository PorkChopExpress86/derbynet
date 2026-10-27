"""Master-heat numbering (#23), PHP's `assign_master_heats()`.

The chart services call `reassign_master_heats` through
`schedule.after_chart_change`, inside the transaction of each chart change.
"""

from collections.abc import Iterable

from django.db import transaction
from django.db.models import Case, Exists, F, Max, OuterRef, Value, When

from core.models import Event, RaceState
from racing.engine.scheduling.master import HeatRef, assign_master_heats
from racing.models import HAS_RESULT, EmptiedHeat, HeatLane

type HeldEmptiedHeat = tuple[int, int, int, int]
"""round number, round id, heat, master heat"""


def _emptied_heats(event: Event) -> tuple[list[HeldEmptiedHeat], list[int]]:
    """The event's held emptied heats, and the ids of its passed ones."""
    later_result = HeatLane.objects.filter(
        round_id=OuterRef("round_id"),
        heat__gt=OuterRef("heat"),
        completed__gte=OuterRef("held_at"),
    )
    rows = (
        EmptiedHeat.objects.filter(event=event)
        .annotate(passed=Exists(later_result))
        .order_by("id")
        .values_list("id", "round__number", "round_id", "heat", "master_heat", "passed")
    )
    held, passed = [], []
    for pk, level, roundid, heat, n, gone in rows:
        if gone:
            passed.append(pk)
        else:
            held.append((level, roundid, heat, n))
    return held, passed


def held_emptied_heats(event: Event) -> list[HeldEmptiedHeat]:
    """The emptied heats (D39) that still hold their numbers. An emptied heat
    is passed once a later heat of its own round gets a result after it was
    held, by `completed`, and is then ignored whichever round is current.
    Results written before it emptied and master renumbering never pass it,
    so under the master schedule a heat of a round that does not run again
    stays held with its number. Every reader of `EmptiedHeat` goes through
    here."""
    return _emptied_heats(event)[0]


def release_passed_emptied_heats(event: Event) -> None:
    """Delete the passed emptied heats. Call inside a chart change's
    transaction."""
    passed = _emptied_heats(event)[1]
    if passed:
        EmptiedHeat.objects.filter(pk__in=passed).delete()


def reassign_master_heats(event: Event, keep: Iterable[HeatRef] = ()) -> None:
    """Renumber the event's unraced heats, after the highest raced number.

    A lane row without a result makes its heat unraced, and every lane row of
    a renumbered heat gets the new number. Heats whose lanes all have results
    keep theirs. Runs whether or not `use-master-sched` is on, as in PHP.

    The heats in `keep`, `(roundid, heat)`, keep their numbers too, and the
    others are numbered after them. Under the master schedule, a reschedule
    passes the current heat and the staged heat, so `next` goes from one to
    the other (D34, D39).

    A held emptied heat (D39) is numbered as an unraced heat, so it keeps its
    place in master order with no lanes. A passed one is deleted first.
    """
    keep = set(keep)
    with transaction.atomic():
        RaceState.objects.select_for_update().get(event=event)
        rows = HeatLane.objects.filter(event=event)
        highest = rows.filter(HAS_RESULT).aggregate(n=Max("master_heat"))["n"] or 0
        release_passed_emptied_heats(event)
        held_empty = held_emptied_heats(event)
        emptied = EmptiedHeat.objects.filter(event=event)
        unraced = set(
            rows.exclude(HAS_RESULT).values_list(
                "round__number", "round_id", "heat", "master_heat"
            )
        ) | set(held_empty)
        held = [n for _, roundid, heat, n in unraced if (roundid, heat) in keep]
        numbers = assign_master_heats(
            {(lvl, r, h) for lvl, r, h, _ in unraced if (r, h) not in keep},
            max([highest, *held]) + 1,
        )
        if not numbers:
            return
        # Only an event with an emptied heat (D39) needs the second update.
        tables = (rows, emptied) if held_empty else (rows,)
        for table in tables:
            table.filter(round_id__in={roundid for roundid, _ in numbers}).update(
                master_heat=Case(
                    *(
                        When(round_id=roundid, heat=heat, then=Value(n))
                        for (roundid, heat), n in numbers.items()
                    ),
                    default=F("master_heat"),
                    output_field=HeatLane._meta.get_field("master_heat"),
                )
            )


def master_index(event: Event) -> list[tuple[int, int, int, bool]]:
    """The index `master_target` reads, `(masterheat, roundid, heat,
    has_result)`: each lane row in row id order, as PHP's query returns them,
    then each emptied heat (D39) as one heat with no result, so `next` can
    land on it and move on from it. `racing.services.heats.load_chart` gives
    the heat state machine the same index, which a test checks."""
    rows = HeatLane.objects.filter(event=event).order_by("id")
    lanes = [
        (n, r, h, time is not None or place is not None)
        for n, r, h, time, place in rows.values_list(
            "master_heat", "round_id", "heat", "finish_time", "finish_place"
        )
    ]
    return lanes + [(n, r, h, False) for _, r, h, n in held_emptied_heats(event)]
