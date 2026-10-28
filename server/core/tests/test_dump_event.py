import io
import json

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import event_scoped_models
from core.tests.factories import EventFactory
from core.tests.populated import populated_event

pytestmark = pytest.mark.django_db


def _dump(slug):
    out = io.StringIO()
    call_command("dump_event", slug, stdout=out)
    return json.loads(out.getvalue())


def test_dump_event_prints_every_event_scoped_models_rows_as_json():
    event = populated_event()
    EventFactory()  # rows of another event stay out

    with CaptureQueriesContext(connection) as queries:
        dumped = _dump(event.slug)

    assert dumped["event"]["slug"] == event.slug
    labels = {model._meta.label_lower for model in event_scoped_models()}
    assert set(dumped["models"]) == labels
    assert len(labels) == 33
    for label, rows in dumped["models"].items():
        assert rows, f"{label} has no rows"
        assert all(row["event"] == event.id for row in rows), label
    writes = ("INSERT", "UPDATE", "DELETE")
    assert not [q for q in queries if q["sql"].lstrip().upper().startswith(writes)]


def test_dump_event_rejects_an_unknown_slug():
    with pytest.raises(CommandError, match="no-such-event"):
        _dump("no-such-event")
