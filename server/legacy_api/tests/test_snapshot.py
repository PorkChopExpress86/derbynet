"""`snapshot.get` and `snapshot.put` (#36, S5 to S7)."""

import io
import json
import re
from pathlib import Path

import pytest

from accounts.permissions import Perm
from core.models import Event, SiteState
from legacy_api.tests.conftest import log_in
from racing.models import HeatLane, RosterEntry, Round
from racing.tests.factories import HeatLaneFactory, RoundFactory
from roster.models import Group, Racer
from roster.tests.factories import GroupFactory

pytestmark = pytest.mark.django_db

GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "tests/golden/http/standings/q13-drop-slowest.snapshot.xml"
)


@pytest.fixture
def coordinator(client, event):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    return event


def get(client, query=""):
    return client.get(f"/action.php?query=snapshot.get{query}")


def put(client, content: bytes):
    return client.post(
        "/action.php",
        {"action": "snapshot.put", "snapshot": io.BytesIO(content)},
    )


def tables_of(content: bytes) -> dict:
    text = content.decode()
    body = text[text.index("<![CDATA[") + 9 : text.rindex("]]>")]
    return {entry["table"]: entry for entry in json.loads(body) if "table" in entry}


def active():
    return SiteState.objects.get(pk=1).active_event


def test_get_needs_set_up(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert b"notauthorized" in get(client).content


def test_put_needs_set_up(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert b"notauthorized" in put(client, GOLDEN.read_bytes()).content
    assert Event.objects.count() == 1


def test_get_writes_the_wrapper_and_every_table_in_php_order(client, coordinator):
    group = GroupFactory(event=coordinator, name="Lions")
    HeatLaneFactory(
        round=RoundFactory(event=coordinator, group=group),
        heat=1,
        lane=1,
        finish_time=3.5,
        finish_place=1,
        racer__car_number=101,
        racer__passed_inspection=True,
    )

    response = get(client)

    assert response["Content-Type"].startswith("text/xml")
    assert response.content.startswith(
        b'<?xml version="1.0" encoding="UTF-8"?>\n<snapshot><![CDATA[\n['
    )
    tables = tables_of(response.content)
    assert list(tables)[:3] == ["Classes", "Ranks", "RegistrationInfo"]
    assert tables["RegistrationInfo"]["rows"][0]["carnumber"] == 101
    assert tables["RaceChart"]["rows"][0]["finishtime"] == 3.5
    assert tables["Classes"]["missing"] == []
    assert tables["Classes"]["high-water"] == len(tables["Classes"]["rows"])


def test_clean_cuts_names_to_initials(client, coordinator):
    group = GroupFactory(event=coordinator)
    HeatLaneFactory(
        round=RoundFactory(event=coordinator, group=group),
        racer__first_name="Matthew",
        racer__last_name="Glennon",
    )

    row = tables_of(get(client, "&clean").content)["RegistrationInfo"]["rows"][0]

    assert (row["firstname"], row["lastname"]) == ("M", "G-1")


def test_a_php_snapshot_loads_with_its_row_counts(client, coordinator):
    reply = put(client, GOLDEN.read_bytes()).content.decode()

    assert "<success/>" in reply
    sent = tables_of(GOLDEN.read_bytes())
    event = active()
    assert Racer.objects.filter(event=event).count() == len(
        sent["RegistrationInfo"]["rows"]
    )
    assert Group.objects.filter(event=event, durable=False).count() <= len(
        sent["Classes"]["rows"]
    )
    assert re.search(r'<table name="Classes" rows-before="0"', reply)
    assert Event.objects.count() == 1


def test_get_then_put_keeps_the_rows(client, coordinator):
    put(client, GOLDEN.read_bytes())
    first = tables_of(get(client).content)

    assert "<success/>" in put(client, get(client).content).content.decode()
    second = tables_of(get(client).content)

    for name in (
        "Classes",
        "Ranks",
        "RegistrationInfo",
        "Rounds",
        "RaceChart",
        "Roster",
    ):
        assert len(second[name]["rows"]) == len(first[name]["rows"]), name
    assert HeatLane.objects.filter(event=active()).count() == len(
        first["RaceChart"]["rows"]
    )
    assert RosterEntry.objects.filter(event=active()).count() == len(
        first["Roster"]["rows"]
    )
    assert Round.objects.filter(event=active()).count() == len(first["Rounds"]["rows"])


def test_an_unreadable_file_fails_and_changes_nothing(client, coordinator):
    group = GroupFactory(event=coordinator, name="Keep me")

    reply = put(client, b"not a snapshot").content.decode()

    assert 'code="json-parsing"' in reply and "<success/>" not in reply
    assert Group.objects.filter(pk=group.pk).exists()
    assert active().pk == coordinator.pk


def test_an_unknown_column_and_table_are_ignored_not_run_as_sql(client, coordinator):
    text = GOLDEN.read_text()
    evil = text.replace(
        '"classid": 1,\n        "class": "Snapshot",',
        '"classid": 1,\n        "class": "Snapshot",'
        '\n        "x); DROP TABLE core_event; --": 1,',
        1,
    ).replace(
        '[\n  {\n    "table": "Classes"',
        '[\n  {"table": "Evil; DROP TABLE core_event", "rows": [{"a": 1}],'
        ' "missing": []},\n  {\n    "table": "Classes"',
        1,
    )
    assert evil != text

    reply = put(client, evil.encode()).content.decode()

    assert "<success/>" in reply
    assert Event.objects.count() == 1
    assert Group.objects.filter(event=active(), name="Snapshot").exists()


def test_a_photo_name_cannot_leave_the_photo_folder(client, coordinator, tmp_path):
    text = GOLDEN.read_text()
    photo = {
        "racerid": 1,
        "imagefile": {"basename": "../../escaped.jpg", "displayed": "ffd8ffd9"},
    }
    body = (
        text[: text.rindex("]\n]]>")].rstrip()
        + ",\n"
        + json.dumps(photo)
        + "\n]\n]]></snapshot>\n"
    )

    reply = put(client, body.encode()).content.decode()

    assert "<success/>" in reply
    assert not list(Path("/").glob("escaped.jpg"))
    assert not (tmp_path.parent / "escaped.jpg").exists()
    racer = Racer.objects.filter(event=active()).order_by("pk").first()
    assert ".." not in racer.headshot
