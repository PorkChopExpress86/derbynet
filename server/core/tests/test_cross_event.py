"""D2 keeps every event in one database, so no row may point into another event.

`check_invariants()` reports such rows under `cross-event`. The tests derive
the references from the models on their own, so a foreign key added later
falls under them without a change here.
"""

import pytest
from django.apps import apps

from awards.models import Award
from core.invariants import check_invariants, cross_event_references
from core.models import Event
from core.tests.populated import populated_event
from racing.tests.factories import HeatLaneFactory
from roster.models import Group, Racer

pytestmark = pytest.mark.django_db


def _points_at_event_rows(field):
    target = field.related_model
    return field.name != "event" and any(
        f.name == "event" and f.related_model is Event for f in target._meta.fields
    )


# Auto-created models (an M2M without a through model) are included, so a
# reference stored in one fails the first test.
REFERENCES = sorted(
    (
        (model, field)
        for model in apps.get_models(include_auto_created=True)
        for field in model._meta.concrete_fields
        if field.is_relation and _points_at_event_rows(field)
    ),
    key=lambda pair: (pair[0]._meta.label_lower, pair[1].name),
)

# A check constraint ties this field to another column.
ALSO_SET = {("core.racesettings", "full_field_group"): {"full_field_rule": "group"}}


def _label(model, field):
    return f"{model._meta.label_lower}.{field.name}"


def _reported(event, model, field, row_id):
    rows = check_invariants(event).get("cross-event", [])
    wanted = {"model": model._meta.label_lower, "field": field.name, "id": row_id}
    return [row for row in rows if wanted.items() <= row.items()]


def test_every_reference_to_an_event_scoped_row_is_checked():
    assert REFERENCES
    assert {_label(m, f) for m, f in REFERENCES} == {
        _label(m, f) for m, f in cross_event_references()
    }


def test_a_populated_event_reports_no_cross_event_reference():
    event = populated_event()
    populated_event()

    assert "cross-event" not in check_invariants(event)


@pytest.mark.parametrize(
    ("model", "field"), REFERENCES, ids=[_label(m, f) for m, f in REFERENCES]
)
def test_a_reference_into_another_event_is_reported(model, field):
    event, other = populated_event(), populated_event()
    row = model.objects.filter(event=event).first()
    targets = field.related_model.objects.filter(event=other)
    if field.unique:
        targets = targets.exclude(pk__in=model.objects.values(field.attname))
    target = targets.first()
    assert row is not None, f"populated_event() has no {model._meta.label}"
    assert target is not None, f"no free {field.related_model._meta.label}"

    model.objects.filter(pk=row.pk).update(
        **{field.attname: target.pk},
        **ALSO_SET.get((model._meta.label_lower, field.name), {}),
    )

    assert _reported(event, model, field, row.pk)
    assert "cross-event" not in check_invariants(other)


def test_a_heat_lane_saved_with_another_events_racer_is_reported():
    event, other = populated_event(), populated_event()
    stranger = Racer.objects.filter(event=other).first()

    lane = HeatLaneFactory(round__group__event=event, racer=stranger)

    assert _reported(event, lane._meta.model, lane._meta.get_field("racer"), lane.pk)


def test_a_bulk_created_award_scoped_to_another_events_group_is_reported():
    event, other = populated_event(), populated_event()
    award = Award.objects.filter(event=event).first()
    award.pk = None
    award.group = Group.objects.filter(event=other).first()

    [created] = Award.objects.bulk_create([award])

    assert _reported(event, Award, Award._meta.get_field("group"), created.pk)
