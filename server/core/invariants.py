"""The `query.database.check` invariants that no database constraint enforces.

#12 classifies the 40 tags of `website/ajax/query.database.check.inc`. 26 are
constraints on the models, 3 went away with their column, and the 11 below
are checks over one event's rows.
"""

from django.db.models import Count, Exists, F, OuterRef, Q

from core.models import RaceSettings
from racing.models import HeatLane, RosterEntry, Round
from roster.models import Group, Partition, Subgroup


def _aggregate(event):
    return Group.objects.filter(event=event).filter(
        Q(constituent_links__isnull=False) | Q(constituent_subgroups__isnull=False)
    )


def _organic(event):
    return Group.objects.filter(event=event).exclude(
        id__in=_aggregate(event).values("id")
    )


def _rows(queryset, *fields):
    return [dict(row) for row in queryset.values(*fields).distinct()]


def classes_ranks(event):
    """An organic group has a subgroup, unless the event has no partitions."""
    if not Partition.objects.filter(event=event).exists():
        return []
    return _rows(_organic(event).filter(subgroups__isnull=True), "id", "name")


def classes_round1(event):
    """Every organic group has round 1."""
    return _rows(
        _organic(event).exclude(rounds__number=1),
        "id",
        "name",
    )


def aggregate_members(event):
    """An aggregate has no racers."""
    return _rows(_aggregate(event).filter(racers__isnull=False), "id", "name")


def aggregate_ranks(event):
    """An aggregate has no subgroups of its own."""
    return _rows(_aggregate(event).filter(subgroups__isnull=False), "id", "name")


def classes_ambiguous_sort(event):
    """Group sort orders have no duplicates in an event."""
    return list(
        Group.objects.filter(event=event)
        .values("sort_order")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
        .order_by("sort_order")
    )


def ranks_ambiguous_sort(event):
    """Subgroup sort orders have no duplicates within a group."""
    return list(
        Subgroup.objects.filter(event=event)
        .values("group_id", "sort_order")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
        .order_by("group_id", "sort_order")
    )


def racechart_masterheat(event):
    """All lanes of a heat share one master heat."""
    other_master = HeatLane.objects.filter(
        round=OuterRef("round"), heat=OuterRef("heat")
    ).exclude(master_heat=OuterRef("master_heat"))
    return _rows(
        HeatLane.objects.filter(event=event)
        .exclude(master_heat=0)
        .filter(Exists(other_master)),
        "id",
        "round_id",
        "heat",
        "lane",
        "master_heat",
    )


def racechart_masterheat2(event):
    """An unraced master heat names one (round, heat)."""
    unraced = HeatLane.objects.filter(event=event, completed__isnull=True)
    other_heat = unraced.filter(master_heat=OuterRef("master_heat")).exclude(
        round=OuterRef("round"), heat=OuterRef("heat")
    )
    return _rows(
        unraced.exclude(master_heat=0).filter(Exists(other_heat)),
        "id",
        "round_id",
        "heat",
        "lane",
        "master_heat",
    )


def racechart_straight_lanes(event):
    """A heat has at most `lane_count` lanes. An unset lane count checks nothing."""
    lane_count = RaceSettings.for_event(event).lane_count
    if not lane_count:
        return []
    return list(
        HeatLane.objects.filter(event=event)
        .values("round_id", "heat")
        .annotate(count=Count("id"))
        .filter(count__gt=lane_count)
        .order_by("round_id", "heat")
    )


def roster_round_classid(event):
    """A racer in an organic group's round-1 roster belongs to that group."""
    return _rows(
        RosterEntry.objects.filter(
            event=event,
            round__number=1,
            round__group__in=_organic(event),
        ).exclude(racer__group=F("round__group")),
        "id",
        "round_id",
        "racer_id",
    )


def rounds_sequential(event):
    """Round numbers in a group have no gaps."""
    previous = Round.objects.filter(
        group=OuterRef("group"), number=OuterRef("number") - 1
    )
    return _rows(
        Round.objects.filter(event=event).exclude(number=1).exclude(Exists(previous)),
        "id",
        "group_id",
        "number",
    )


CHECKS = {
    "classes-ranks": classes_ranks,
    "classes-round1": classes_round1,
    "aggregate-members": aggregate_members,
    "aggregate-ranks": aggregate_ranks,
    "classes-ambiguous-sort": classes_ambiguous_sort,
    "ranks-ambiguous-sort": ranks_ambiguous_sort,
    "racechart-masterheat": racechart_masterheat,
    "racechart-masterheat2": racechart_masterheat2,
    "racechart-straight-lanes": racechart_straight_lanes,
    "roster-round-classid": roster_round_classid,
    "rounds-sequential": rounds_sequential,
}


def check_invariants(event):
    """Return `{tag: [violating rows]}` for each check that finds a violation."""
    violations = {tag: check(event) for tag, check in CHECKS.items()}
    return {tag: rows for tag, rows in violations.items() if rows}
