"""Master-heat numbering (#23), PHP's `assign_master_heats()`.

The chart services call `reassign_master_heats` through
`schedule.after_chart_change`, inside the transaction of each chart change.
"""

from collections.abc import Iterable

from django.db import transaction
from django.db.models import Case, F, Max, Value, When

from core.models import Event, RaceState
from racing.engine.scheduling.master import HeatRef, assign_master_heats
from racing.models import HAS_RESULT, HeatLane


def reassign_master_heats(event: Event, keep: Iterable[HeatRef] = ()) -> None:
    """Renumber the event's unraced heats, after the highest raced number.

    A lane row without a result makes its heat unraced, and every lane row of
    a renumbered heat gets the new number. Heats whose lanes all have results
    keep theirs. Runs whether or not `use-master-sched` is on, as in PHP.

    The heats in `keep`, `(roundid, heat)`, keep their numbers too, and the
    others are numbered after them. Under the master schedule, a reschedule
    passes the current heat, so it stays first in master order (D34).
    """
    keep = set(keep)
    with transaction.atomic():
        RaceState.objects.select_for_update().get(event=event)
        rows = HeatLane.objects.filter(event=event)
        highest = rows.filter(HAS_RESULT).aggregate(n=Max("master_heat"))["n"] or 0
        unraced = set(
            rows.exclude(HAS_RESULT).values_list(
                "round__number", "round_id", "heat", "master_heat"
            )
        )
        held = [n for _, roundid, heat, n in unraced if (roundid, heat) in keep]
        numbers = assign_master_heats(
            {(lvl, r, h) for lvl, r, h, _ in unraced if (r, h) not in keep},
            max([highest, *held]) + 1,
        )
        if not numbers:
            return
        rows.filter(round_id__in={roundid for roundid, _ in numbers}).update(
            master_heat=Case(
                *(
                    When(round_id=roundid, heat=heat, then=Value(n))
                    for (roundid, heat), n in numbers.items()
                ),
                default=F("master_heat"),
                output_field=HeatLane._meta.get_field("master_heat"),
            )
        )
