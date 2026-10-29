"""The award endpoints against the PHP captures (#33), with ids normalized as
`php_http` describes.

The scenario runs the steps of `capture/scenarios/17-awards.sh` in order,
as `test_race_control_parity` runs its scenarios. The exchanges
`deviations.json` lists (A1 to A5) must match their expected Django output
instead, and must differ from PHP's (D25).

`WRITE_DEVIATIONS=1 uv run pytest legacy_api/tests/test_awards_parity.py`
rewrites the #33 entries and their expected transcripts.
"""

import json
import os

import pytest

from legacy_api.tests.php_http import award_key
from legacy_api.tests.test_race_control_parity import (
    Scenario,
    _dump,
    assert_listed,
    write_deviations,
)
from roster.models import Racer

pytestmark = pytest.mark.django_db

SPEED_KEYS = ("speed-1", "speed-2", "speed-1-1", "speed-2-2", "speed-1-2-2", "speed-9")


def run_awards(client) -> Scenario:
    s = Scenario(client)
    s.reset()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=2")
    for i in (1, 2, 3):
        for den, base in (("Lion", 100), ("Tiger", 200)):
            s.import_racer(
                f"firstname={den}{i}&lastname=Racer&carnumber={base + i}"
                f"&partition={den}s"
            )
    s.learn_rows()
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.post_ok("action=schedule.generate&roundid=1")
    s.post_ok("action=schedule.generate&roundid=2")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    s.race_round()
    s.post_ok("action=heat.select&roundid=2&now_racing=1")
    s.race_round(salt=5)

    s.scenario("http/awards/awards.http")
    for label in (
        "import",
        "import for a class",
        "import for a subgroup",
        "import missing type",
        "import empty name",
        "import unknown type",
        "import unknown class",
        "import unknown rank",
        "import wrong class",
        "import unknown car",
        "edit new",
        "edit rename",
        "edit to a subgroup",
        "edit on the ballot",
        "winner",
        "winner no such award",
        "winner no such racer",
        "winner wrong class",
        "winner of another class for a subgroup award",
    ):
        s.capture(label)
    # Lion2, car 102, is not eligible for any award.
    s.post_ok(
        "action=racer.edit&racer=3&firstname=Lion2&lastname=Racer&carno=102"
        "&carname=&rankid=1&exclude=1"
    )
    for label in (
        "winner excluded",
        "winner cleared",
        "adhoc",
        "adhoc replaced",
        "xbs",
        "list",
        "list without ad hoc",
        "xbs off",
        "order",
        "delete",
        "present judged",
        "current judged",
        "present no winner",
        "current no winner",
    ):
        s.capture(label)
    for key in SPEED_KEYS:
        s.post_ok(f"action=award.present&key={key}&reveal=0")
        s.capture(f"current {key}")
    s.post_ok("action=award.present&key=prize-1")
    s.capture("current unrecognized")
    s.capture("calc-rule")
    s.post_ok("action=award.present&key=speed-1")
    s.capture("current speed-1 with no pack award")
    s.post_ok("action=award.calc-rule&full-field-calc=0")

    s.post_ok("action=class.add&constituent_1=1&constituent_2=1&name=Cubs")
    s.learn_rows()
    s.post_ok(
        "action=award.edit&awardid=new&name=Cub%20Spirit&awardtypeid=4"
        "&class_and_rank=3,0"
    )
    for label in (
        "aggregate award list",
        "A1 edit new with no class",
        "A2 edit new in no class",
        "A3 present nothing",
        "A4 calc-rule no aggregate",
        "A5 present a long key",
    ):
        s.capture(label)
    return s


def test_the_award_scenario_matches_php(client, event):
    s = run_awards(client)
    _dump(s)
    assert s.mismatches() == []


AWARDS = "http/awards/awards.http"
SCENARIOS = {"awards": run_awards}
# quirk: (reason, scenario, {fixture: [exchange names]}, expected file name)
DEVIATIONS = {
    "A1": (
        "An award stored with no class, one award.edit makes with no"
        " class_and_rank or award.xbs makes, lists classid and rankid null in"
        " PHP, which the awards editor shows as an unknown class; Django lists"
        " 0, as for every other pack-wide award (#33, for the owner to"
        " confirm).",
        "awards",
        {
            AWARDS: [
                "025 list",
                "026 list without ad hoc",
                "044 A1 edit new with no class",
            ]
        },
        None,
    ),
    "A2": (
        "PHP stores a class_and_rank that names no class of the event; Django"
        " refuses it with unknownclass, as award.import does (#33, for the"
        " owner to confirm).",
        "awards",
        {AWARDS: ["045 A2 edit new in no class"]},
        None,
    ),
    "A3": (
        "award.present with neither key nor reveal never sets an outcome in"
        " PHP, which answers in-progress; Django answers success (#33).",
        "awards",
        {AWARDS: ["046 A3 present nothing"]},
        None,
    ),
    "A4": (
        "PHP stores a full-field-calc that names no class; Django refuses it"
        " with not-written, as settings.write does (#33, for the owner to"
        " confirm).",
        "awards",
        {AWARDS: ["047 A4 calc-rule no aggregate"]},
        None,
    ),
    "A5": (
        "PHP stores an award key of any length; Django refuses one past 100"
        " characters with not-written (#33, for the owner to confirm).",
        "awards",
        {AWARDS: ["048 A5 present a long key"]},
        None,
    ),
}


def test_every_listed_deviation_is_a_reply_the_scenario_makes():
    assert_listed(DEVIATIONS, SCENARIOS)


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the #33 entries"
)
def test_write_award_deviations(event):
    write_deviations(DEVIATIONS, SCENARIOS)


def test_award_current_names_each_speed_award_phps_list_holds(client, event):
    """Every key of the speed-award ladder PHP listed, presented in turn:
    `award.current` names the racer PHP's list gives it."""
    s = run_awards(client)
    php = json.loads(s.transcripts[AWARDS]["043 aggregate award list"]["body"])
    # The pack, Cubs and Tigers give three places; Lions two, as Lion2 is
    # not eligible for any award.
    assert len(php["speed-awards"]) == 11
    for award in php["speed-awards"]:
        key = award_key(award["key"], s.ids.django)
        s.post_ok(f"action=award.present&key={key}")
        current = json.loads(
            client.get("/action.php", {"query": "award.current"}).content
        )["current"]
        racer = Racer.objects.get(pk=s.ids.django("racer", award["racerid"]))
        assert (current["key"], current["carnumber"]) == (key, racer.car_number)
