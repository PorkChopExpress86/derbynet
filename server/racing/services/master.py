"""Master-heat numbering (#23), PHP's `assign_master_heats()`.

The chart services call `reassign_master_heats` through
`schedule.after_chart_change`, inside the transaction of each chart change.
"""

from collections.abc import Iterable

from django.db import transaction
from django.db.models import Case, F, Max, Value, When

from core.models import Event, RaceState
from racing.engine.scheduling.master import HeatRef, assign_master_heats
from racing.models import HAS_RESULT, EmptiedHeat, HeatLane


def reassign_master_heats(event: Event, keep: Iterable[HeatRef] = ()) -> None:
    """Renumber the event's unraced heats, after the highest raced number.

    A lane row without a result makes its heat unraced, and every lane row of
    a renumbered heat gets the new number. Heats whose lanes all have results
    keep theirs. Runs whether or not `use-master-sched` is on, as in PHP.

    The heats in `keep`, `(roundid, heat)`, keep their numbers too, and the
    others are numbered after them. Under the master schedule, a reschedule
    passes the current heat and the staged heat, so `next` goes from one to
    the other (D34, D39).

    An emptied heat (D39) is numbered as an unraced heat, so it keeps its
    place in master order with no lanes.
    """
    keep = set(keep)
    with transaction.atomic():
        RaceState.objects.select_for_update().get(event=event)
        rows = HeatLane.objects.filter(event=event)
        highest = rows.filter(HAS_RESULT).aggregate(n=Max("master_heat"))["n"] or 0
        emptied = EmptiedHeat.objects.filter(event=event)
        held_empty = emptied.exists()
        unraced = set(
            rows.exclude(HAS_RESULT).values_list(
                "round__number", "round_id", "heat", "master_heat"
            )
        ) | set(emptied.values_list("round__number", "round_id", "heat", "master_heat"))
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
    land on it and move on from it. The heat state machine (#27) reads it."""
    rows = HeatLane.objects.filter(event=event).order_by("id")
    lanes = [
        (n, r, h, time is not None or place is not None)
        for n, r, h, time, place in rows.values_list(
            "master_heat", "round_id", "heat", "finish_time", "finish_place"
        )
    ]
    emptied = EmptiedHeat.objects.filter(event=event).order_by("id")
    return lanes + [
        (n, r, h, False)
        for n, r, h in emptied.values_list("master_heat", "round_id", "heat")
    ]
