import io
import json

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from awards.tests.factories import BallotAwardFactory, VoteFactory
from core.management.commands.dump_event import event_scoped_models
from core.tests.factories import ActionLogFactory, EventFactory
from kiosks.tests.factories import (
    KioskFactory,
    ReplayCommandFactory,
    SignalMessageFactory,
)
from racing.tests.factories import (
    AuditLaneResultFactory,
    HeatLaneFactory,
    PlaylistEntryFactory,
    RosterEntryFactory,
    TimerSettingFactory,
)
from roster.tests.factories import GroupConstituentFactory

pytestmark = pytest.mark.django_db


def _populated_event():
    event = EventFactory()
    entry = RosterEntryFactory(round__group__event=event)
    HeatLaneFactory(round=entry.round, racer=entry.racer, lane=1)
    GroupConstituentFactory(aggregate__event=event)
    PlaylistEntryFactory(group=entry.round.group)
    TimerSettingFactory(event=event)
    AuditLaneResultFactory(audit_event__event=event)
    VoteFactory(voter__event=event)
    BallotAwardFactory(award__event=event)
    KioskFactory(event=event)
    SignalMessageFactory(event=event)
    ReplayCommandFactory(event=event)
    ActionLogFactory(event=event)
    return event


def _dump(slug):
    out = io.StringIO()
    call_command("dump_event", slug, stdout=out)
    return json.loads(out.getvalue())


def test_dump_event_prints_every_event_scoped_models_rows_as_json():
    event = _populated_event()
    EventFactory()  # rows of another event stay out

    with CaptureQueriesContext(connection) as queries:
        dumped = _dump(event.slug)

    assert dumped["event"]["slug"] == event.slug
    labels = {model._meta.label_lower for model in event_scoped_models()}
    assert set(dumped["models"]) == labels
    assert len(labels) == 26
    for label, rows in dumped["models"].items():
        assert rows, f"{label} has no rows"
        assert all(row["event"] == event.id for row in rows), label
    writes = ("INSERT", "UPDATE", "DELETE")
    assert not [q for q in queries if q["sql"].lstrip().upper().startswith(writes)]


def test_dump_event_rejects_an_unknown_slug():
    with pytest.raises(CommandError, match="no-such-event"):
        _dump("no-such-event")
