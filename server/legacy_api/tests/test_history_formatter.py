"""The history formatter against `history.php` (#36).

`data/history/php-*.json` hold the `ActionHistory` rows of a PHP server after
shell suites ran against it, and the sentences its `history.php` printed for
them (each row's heat, time and sentence, read from the page).
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.models import ActionLog
from legacy_api.history_formatter import HistoryFormatter, entries, history

DATA = Path(__file__).parent / "data" / "history"
FIXTURES = sorted(DATA.glob("php-*.json"))


def load(path):
    data = json.loads(path.read_text())
    return [(r, json.loads(q)) for r, q in data["rows"]], data["expected"]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_sentences_match_what_php_printed(path):
    rows, expected = load(path)

    found = entries(rows)

    assert [(e.heat, e.received, e.sentence) for e in found] == [
        (x["heat"], x["received"], x["sentence"]) for x in expected
    ]
    assert len(found) > 50


def same_settings(sentence: str) -> str:
    """Postgres keeps a JSON request in its own key order, so the settings of
    a `settings.write` come back sorted, where SQLite and PHP keep the order
    sent."""
    head, found, rest = sentence.partition("Write setting(s):")
    if not found:
        return sentence
    return head + found + " ".join(sorted(re.findall(r"<b>.*?</b>: \"[^\"]*\"", rest)))


@pytest.mark.django_db
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_sentences_come_from_the_action_log_of_an_event(path, event):
    rows, expected = load(path)
    for received, request in rows:
        ActionLog.objects.create(
            event=event,
            received=datetime.strptime(received, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=UTC
            ),
            request=request,
        )

    found = history(event)

    assert [same_settings(e.sentence) for e in found] == [
        same_settings(x["sentence"]) for x in expected
    ]


def run(*actions):
    formatter = HistoryFormatter()
    out = []
    for action in actions:
        formatter.set_action(action)
        out.append(formatter.process())
    return out


def test_a_deleted_racer_is_named_from_the_replayed_creation():
    sentences = run(
        {"action": "rank.*create", "rankid": 1, "rank": "Lions", "classid": 1},
        {
            "action": "racer.*create",
            "racerid": 1,
            "firstname": "Ann",
            "lastname": "Lee",
            "carnumber": 101,
            "carname": "",
            "partitionid": 1,
            "rankid": 1,
            "classid": 1,
        },
        {"action": "racer.delete", "racer": "1"},
    )

    assert sentences[-1] == "Delete racer <b>Ann Lee</b>, 101, Lions"


def test_names_are_escaped_as_php_escapes_them():
    (sentence,) = run({"action": "class.add", "name": "<i>A & B's</i>"})

    assert sentence == "Add class &lt;i&gt;A &amp; B&#039;s&lt;/i&gt;"


def test_an_unknown_action_has_no_sentence():
    assert run({"action": "no.such"}, {"unparsed": "x"}) == ["", ""]


def test_a_bulk_action_of_an_unknown_kind_runs_on_into_racer_delete():
    (sentence,) = run({"action": "racer.bulk", "what": "other", "racer": "9"})

    assert sentence == "Delete racer <b> </b>"
