"""`database.execute` (#63): named scripts, not a SQL runner."""

import json
from unittest import mock
from urllib.parse import urlencode

import pytest

from accounts.permissions import Perm
from core.models import RaceSettings
from core.tests.factories import EventFactory
from core.tests.populated import populated_event
from kiosks.models import SignalMessage
from kiosks.tests.factories import SignalMessageFactory
from legacy_api.tests.conftest import log_in, post
from roster.models import Racer
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db

DETAIL_KEYS = {
    "locked",
    "database",
    "schema",
    "purge",
    "roster",
    "groups",
    "awards",
    "settings",
    "form_fields",
}


@pytest.fixture
def coordinator(client, event):
    log_in(client, Perm.SET_UP)
    return client


def execute(client, script=None):
    fields = {"action": "database.execute"}
    if script is not None:
        fields["script"] = script
    return json.loads(post(client, urlencode(fields)).content)


def test_schema_empties_the_racers_and_keeps_the_preserved_settings(coordinator, event):
    RacerFactory(event=event, group__event=event)
    RaceSettings.objects.filter(event=event).update(
        photo_directory="/data/photos", lane_count=4, use_points=True
    )

    body = execute(coordinator, "schema")

    assert body["outcome"]["code"] == "success"
    assert not Racer.objects.filter(event=event).exists()
    row = RaceSettings.for_event(event)
    assert (row.photo_directory, row.lane_count) == ("/data/photos", 4)
    assert row.use_points is False


def test_schema_reports_what_it_executed_and_the_setup_details(coordinator):
    body = execute(coordinator, "schema")

    assert [step["sql"] for step in body["executed"]]
    assert set(body["details"]) == DETAIL_KEYS
    assert body["details"]["purge"] == {
        "nracers": 0,
        "nawards": 0,
        "nheats": 0,
        "nresults": 0,
    }
    assert body["details"]["schema"]["button"] == "re-initialize"


def test_schema_clears_only_the_active_event(client, event):
    other = populated_event()
    log_in(client, Perm.SET_UP)
    before = Racer.objects.filter(event=other).count()
    assert before

    execute(client, "schema")

    assert Racer.objects.filter(event=other).count() == before


def test_schema_runs_twice_in_a_row(coordinator):
    assert execute(coordinator, "schema")["outcome"]["code"] == "success"
    assert execute(coordinator, "schema")["outcome"]["code"] == "success"


@pytest.mark.parametrize("script", ["../x", "a/b", "..\\x", "/etc/passwd", "sub\\dir"])
def test_a_script_with_a_path_separator_is_insecure(coordinator, event, script):
    RacerFactory(event=event, group__event=event)

    body = execute(coordinator, script)

    assert body["outcome"] == {
        "summary": "failure",
        "code": "insecure",
        "description": "Only local SQL script execution is allowed.",
    }
    assert Racer.objects.filter(event=event).exists()


@pytest.mark.parametrize("script", ["nope", "", "Schema", "schema.inc"])
def test_an_unknown_script_is_sqlnoscript_and_changes_nothing(
    coordinator, event, script
):
    RacerFactory(event=event, group__event=event)

    body = execute(coordinator, script)

    assert body["outcome"]["code"] == "sqlnoscript"
    assert body["outcome"]["description"] == f"SQL script {script} not found."
    assert body["executed"] == []
    assert Racer.objects.filter(event=event).exists()


def test_a_missing_script_is_sqlnoscript(coordinator):
    assert execute(coordinator)["outcome"]["code"] == "sqlnoscript"


def test_update_schema_succeeds_when_none_is_pending(coordinator):
    body = execute(coordinator, "update-schema")

    assert body["outcome"]["code"] == "success"
    assert body["executed"] == []


def test_update_schema_applies_what_is_pending(coordinator):
    migration = mock.Mock(app_label="core")
    migration.name = "0099_x"  # `name` is not a Mock keyword to set in the call.
    plan = [(migration, False)]
    with (
        mock.patch("core.database_scripts.MigrationExecutor") as executor,
        mock.patch("core.database_scripts.call_command") as migrate,
    ):
        executor.return_value.migration_plan.return_value = plan
        body = execute(coordinator, "update-schema")

    migrate.assert_called_once()
    assert body["outcome"]["code"] == "success"
    assert body["executed"] == [{"sql": "Migrate core.0099_x", "result": 0}]


def test_drop_message_queue_table_empties_the_queue(coordinator, event):
    SignalMessageFactory(event=event)
    other = EventFactory()
    SignalMessageFactory(event=other)

    body = execute(coordinator, "drop-message-queue-table")

    assert body["outcome"]["code"] == "success"
    assert not SignalMessage.objects.filter(event=event).exists()
    assert SignalMessage.objects.filter(event=other).exists()


def test_without_set_up_nothing_runs(client, event):
    log_in(client, Perm.CONTROL_RACE)
    RacerFactory(event=event, group__event=event)

    body = execute(client, "schema")

    assert body["outcome"]["code"] == "notauthorized"
    assert "details" not in body
    assert Racer.objects.filter(event=event).exists()
