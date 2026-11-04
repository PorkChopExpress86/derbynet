"""The Retrospective of an imported event (#49): its action log keeps the
legacy ids (#12, #14), which `transfer.action_log` resolves through
`transfer.LegacyId` before the history formatter names anything.

The v10 fixture is a PHP server's database after `scenario.sh` (its
`ActionHistory` and its tables agree). The expected text is the history
formatter run on the log with its legacy ids, as `history.php` replays it;
`legacy_api/tests/test_history_formatter.py` holds that formatter to the
text PHP printed for two such logs, sentence for sentence.
"""

import html as html_text
import json
import re
import sqlite3

import pytest

from core.models import ActionLog, SiteState
from legacy_api.history_formatter import entries
from legacy_api.tests.test_history_formatter import same_settings
from roster.models import Racer
from transfer.action_log import Unmapped, resolve_request
from transfer.models import LegacyId
from transfer.tests.legacy import legacy_folder, run_import
from ui.tests.race import race_setup

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def data_dir(settings, tmp_path):
    settings.DATA_DIR = tmp_path / "data"
    settings.DATA_DIR.mkdir()


def legacy_log(database) -> list[tuple[str, dict]]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT received, request FROM ActionHistory ORDER BY received, historyid"
        ).fetchall()
    finally:
        connection.close()
    return [(received, json.loads(request)) for received, request in rows]


def sentences(html: str) -> list[str]:
    return re.findall(r"data-sentence>(.*?)</p>", html, re.S)


def key_order_free(sentence: str) -> str:
    """Postgres keeps a JSON request in its own key order, so a request the
    history prints whole (`rank.edit`) and the settings of a
    `settings.write` come back sorted, where SQLite and PHP keep the order
    sent."""
    sentence = same_settings(sentence)
    head, found, rest = sentence.partition("Rank.edit ")
    if not found:
        return sentence
    request = json.loads(html_text.unescape(rest).replace("\\/", "/"))
    return head + found + json.dumps(request, sort_keys=True)


@pytest.fixture
def imported(tmp_path, event):
    """v10 imported beside an event whose rows took the low ids, so every
    imported row's new id differs from its legacy id."""
    race_setup(event, groups=(("Early", 40), ("Earlier", 40)))
    database = legacy_folder(tmp_path, "v10")
    report, _ = run_import(database, report_dir=tmp_path / "report")
    from core.models import Event

    new = Event.objects.get(pk=report["event"]["id"])
    SiteState.objects.filter(pk=1).update(active_event=new)
    return new, database


def test_the_timeline_of_an_imported_event_reads_as_php_printed_it(login, imported):
    event, database = imported
    moved = LegacyId.objects.filter(event=event, model="roster.racer")
    assert moved.count() > 50
    assert all(row.legacy_id != row.new_id for row in moved)
    expected = [e.sentence for e in entries(legacy_log(database))]
    assert len(expected) > 100
    assert any("Delete racer <b>" in s for s in expected)

    html = login("RaceCoordinator").get("/history/").content.decode()

    assert [key_order_free(s) for s in sentences(html)] == [
        key_order_free(s) for s in expected
    ]


def test_an_action_after_the_import_names_the_racer_the_log_created(login, imported):
    """A racer the imported log created, named by its new id in an action
    Django logged after the import."""
    event, _ = imported
    racer = Racer.objects.filter(event=event).order_by("car_number").first()
    legacy = LegacyId.objects.get(event=event, model="roster.racer", new_id=racer.pk)
    assert legacy.legacy_id != racer.pk
    ActionLog.objects.create(
        event=event, request={"action": "racer.delete", "racer": racer.pk}
    )

    html = login("RaceCoordinator").get("/history/").content.decode()

    last = sentences(html)[-1]
    name = html_text.escape(f"{racer.first_name} {racer.last_name}")
    assert last.startswith(f"Delete racer <b>{name}</b>, {racer.car_number}, ")


def test_a_legacy_id_with_no_new_row_shows_raw_with_a_marker():
    maps = {"roster.racer": {7: 1007}}
    known = resolve_request({"action": "racer.delete", "racer": "7"}, maps)
    unknown = resolve_request({"action": "racer.delete", "racer": 8}, maps)
    assert known["racer"] == "1007"
    assert unknown["racer"] == Unmapped("roster.racer", 8)

    found = entries([("t", unknown)])

    assert found[0].sentence == ("Delete racer <b> 8 (old id, not imported)</b>")


def test_an_unmapped_racer_the_log_created_keeps_its_name():
    """A racer deleted before the export has no new row, but the log that
    created it still names it, and never another racer with that id."""
    maps = {"roster.racer": {}}
    rows = [
        {
            "action": "racer.*create",
            "racerid": 3,
            "firstname": "Ann",
            "lastname": "Old",
            "carnumber": "103",
        },
        {
            "action": "racer.*create",
            "racerid": 3,
            "firstname": "Bob",
            "lastname": "New",
            "carnumber": "203",
        },
    ]
    created_then_new = [
        ("t", resolve_request(rows[0], maps)),
        ("t", rows[1]),
        ("t", resolve_request({"action": "racer.delete", "racer": 3}, maps)),
        ("t", {"action": "racer.delete", "racer": 3}),
    ]

    found = [e.sentence for e in entries(created_then_new)]

    assert found == [
        "Delete racer <b>Ann Old</b>, 103",
        "Delete racer <b>Bob New</b>, 203",
    ]
