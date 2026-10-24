"""The `query.database.check` invariants that no database constraint enforces.

#12 classifies the 40 tags of `website/ajax/query.database.check.inc`. 26 are
constraints on the models, 3 went away with their column, and the 11 below
are checks over one event's rows.

`cross-event` is not a PHP tag. PHP kept one event per database, and D2 keeps
every event in one, so each reference between event-scoped rows must stay
inside one event.
"""

from django.db.models import Count, Exists, F, OuterRef, Q

from core.models import RaceSettings, event_scoped_models, is_event_scoped
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
    """A racer in any round of an organic group belongs to that group.

    PHP compares `Roster.classid` with the racer's group in every round, and
    joins through the racer's group's round 1, so a racer whose group has no
    round 1 is left to `classes-round1`. PHP also flags aggregate rosters,
    because it writes the aggregate's id into `Roster.classid`
    (`roster.inc:131`). Those rosters are valid, so they are not checked here.
    """
    own_round1 = Round.objects.filter(group=OuterRef("racer__group"), number=1)
    return _rows(
        RosterEntry.objects.filter(event=event, round__group__in=_organic(event))
        .exclude(racer__group=F("round__group"))
        .filter(Exists(own_round1)),
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


def cross_event_references():
    """Every foreign key from an event-scoped model to an event-scoped model."""
    return [
        (model, field)
        for model in event_scoped_models()
        for field in model._meta.concrete_fields
        if field.is_relation
        and field.name != "event"
        and is_event_scoped(field.related_model)
    ]


def cross_event(event):
    """The event's rows that point at another event's row.

    The check walks the models, so a foreign key added later is covered. It
    reports rows however they were written: `save()`, `bulk_create()`,
    `update()`, raw SQL, or an import.
    """
    rows = []
    for model, field in cross_event_references():
        found = (
            model.objects.filter(event=event, **{f"{field.name}__isnull": False})
            .exclude(**{f"{field.name}__event": F("event")})
            .values_list("id", field.attname)
        )
        rows += [
            {
                "model": model._meta.label_lower,
                "field": field.name,
                "id": row_id,
                "target_id": target_id,
            }
            for row_id, target_id in found
        ]
    return rows


def check_invariants(event):
    """Return `{tag: [violating rows]}` for each check that finds a violation."""
    checks = {**CHECKS, "cross-event": cross_event}
    violations = {tag: check(event) for tag, check in checks.items()}
    return {tag: rows for tag, rows in violations.items() if rows}
