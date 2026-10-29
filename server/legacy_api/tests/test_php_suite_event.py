"""An event from the end of PHP's `exercise-one-database.sh` run, put back
through `snapshot.put` (#36).

`data/php-suite/` holds what a PHP server answered after that suite: its
`snapshot.get`, `database.check` (as JSON) and `export-json.php`. The server ran with
`TZ=America/New_York`, as the golden captures did. `export-json.php` is
compared byte for byte, except that PHP writes `null` for a racer with no
"From" text and Django writes `""`.
"""

import json
import re
from pathlib import Path

import pytest

from accounts.permissions import Perm
from awards.models import Award, BallotAward, Vote, Voter
from core.models import SiteState
from kiosks.models import Scene, SceneKiosk
from legacy_api.tests.conftest import log_in
from racing.models import HeatLane, PlaylistEntry, RosterEntry, Round
from roster.models import Group, Partition, Racer, Subgroup

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
    event = SiteState.objects.get().active_event
    sent = tables(SNAPSHOT.read_text())

    check = restored.get("/action.php?query=database.check").json()

    assert check == json.loads((DATA / "database-check.json").read_text())
    assert Racer.objects.count() == len(sent["RegistrationInfo"])
    assert HeatLane.objects.count() == len(sent["RaceChart"])
    assert RosterEntry.objects.count() == len(sent["Roster"])
    assert Round.objects.count() == len(sent["Rounds"])
    assert Group.objects.filter(event=event, durable=False).count() == len(
        sent["Classes"]
    )
    assert Subgroup.objects.filter(event=event).count() == len(sent["Ranks"])
    assert Partition.objects.filter(event=event).count() == len(sent["Partitions"])
    assert Award.objects.filter(event=event).count() == len(sent["Awards"])
    assert BallotAward.objects.filter(event=event).count() == len(sent["BallotAwards"])
    assert Voter.objects.filter(event=event).count() == len(sent["Voters"])
    assert Vote.objects.filter(event=event).count() == len(sent["Votes"])
    assert Scene.objects.filter(event=event).count() == len(sent["Scenes"])
    assert SceneKiosk.objects.filter(event=event).count() == len(sent["SceneKiosk"])
    assert PlaylistEntry.objects.filter(event=event).count() == len(sent["Playlist"])


def test_export_json_is_the_bytes_php_wrote(restored):
    response = restored.get("/export-json.php")

    # PHP's database holds NULL where a racer has no "From" text; Django's
    # text columns hold an empty string instead. Only the third cell of a
    # Roster row (after the first and last name) is changed.
    php = (DATA / "export-json.json").read_text()
    roster, rest = php.split('"Results"', 1)
    expected = re.sub(r'(\n +"[^\n]*",\n +"[^\n]*",\n +)null,', r'\1"",', roster)
    expected += '"Results"' + rest
    assert expected.count("null") == php.count("null") - 82
    assert response.content.decode() == expected
