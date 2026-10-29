"""An event from the end of PHP's `exercise-one-database.sh` run, put back
through `snapshot.put` (#36).

`data/php-suite/` holds what a PHP server answered after that suite: its
`snapshot.get`, `database.check` and `export-json.php`. The server ran with
`TZ=America/New_York`, as the golden captures did. `export-json.php` is
compared byte for byte, except that PHP writes `null` for a racer with no
"From" text and Django writes `""`.
"""

import json
import re
from pathlib import Path

import pytest

from accounts.permissions import Perm
from legacy_api.tests.conftest import log_in
from racing.models import HeatLane, RosterEntry, Round
from roster.models import Group, Racer

pytestmark = pytest.mark.django_db

DATA = Path(__file__).resolve().parent / "data" / "php-suite"
SNAPSHOT = DATA / "snapshot.xml"


def tables(text: str) -> dict:
    body = re.search(r"<!\[CDATA\[(.*)\]\]>", text, re.DOTALL)[1]
    return {entry["table"]: entry["rows"] for entry in json.loads(body)}


@pytest.fixture
def restored(client, event, settings):
    settings.TIME_ZONE = "America/New_York"
    log_in(client, Perm.SET_UP | Perm.VIEW_RACE_RESULTS, role="RaceCoordinator")
    reply = client.post(
        "/action.php",
        {"action": "snapshot.put", "snapshot": SNAPSHOT.open("rb")},
    )
    assert "<success/>" in reply.content.decode()
    return client


def test_the_row_counts_and_the_check_match_php(restored):
    sent = tables(SNAPSHOT.read_text())

    check = restored.get("/action.php?query=database.check").json()

    assert check == json.loads((DATA / "database-check.xml").read_text())
    assert Racer.objects.count() == len(sent["RegistrationInfo"])
    assert HeatLane.objects.count() == len(sent["RaceChart"])
    assert RosterEntry.objects.count() == len(sent["Roster"])
    assert Round.objects.count() == len(sent["Rounds"])
    assert Group.objects.filter(durable=False).count() <= len(sent["Classes"])


def test_export_json_is_the_bytes_php_wrote(restored):
    response = restored.get("/export-json.php")

    # PHP's database holds NULL where a racer has no "From" text; Django's
    # text columns hold an empty string instead. Only the Roster sheet has it.
    php = (DATA / "export-json.json").read_text()
    roster, rest = php.split('"Results"', 1)
    expected = (
        re.sub(r"^( +)null(,?)$", r'\1""\2', roster, flags=re.MULTILINE)
        + '"Results"'
        + rest
    )
    assert response.content.decode() == expected
