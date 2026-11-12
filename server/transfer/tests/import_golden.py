"""PHP's roster import page as the golden capture recorded it (#242).

`capture/scenarios/18-import-page.sh` drove `website/import-roster.php` in
a browser over each sample roster in `testing/data`, in the encoding the
file is written in. Each `http/import/<stem>.http` holds the `racer.import`
requests the page sent, in order, then `racer.list&order=name`. The racers
are compared by name, never by id (ADR-12).
"""

import json
from urllib.parse import parse_qsl

from racing.engine.tests.golden import (
    GOLDEN_DIR,
    read_transcript,
    transcript_deviations,
)
from roster.models import Racer
from transfer.batch_import import Row

IMPORT_DIR = GOLDEN_DIR / "http" / "import"
DATA = GOLDEN_DIR.parents[2] / "testing" / "data"

# Each file in testing/data, with its transcript and the codepage the
# capture picked in the page's encoding select, as Django's select names it.
ROSTERS = {
    "sample-names.csv": ("sample-names", "10000"),
    "sample-names-Mac.csv": ("sample-names-Mac", "10000"),
    "sample-names-Windows.csv": ("sample-names-Windows", "1252"),
    "sample-names-MS-DOS.csv": ("sample-names-MS-DOS", "437"),
    "sample-names-ascii.csv": ("sample-names-ascii", "65001"),
    "sample-names-firstlast.csv": ("sample-names-firstlast", "65001"),
    "sample-names-last-comma-first.csv": ("sample-names-last-comma-first", "65001"),
    "sample-names-plurals.csv": ("sample-names-plurals", "65001"),
    "sample-names.xlsx": ("sample-names-xlsx", "65001"),
}


def transcript(name: str) -> dict[str, dict]:
    return read_transcript(IMPORT_DIR / f"{ROSTERS[name][0]}.http")


def page_rows(name: str) -> list[Row]:
    """The rows PHP's page posted for the file, each `racer.import` request's
    fields without `action`, numbered from 2 as below a header row."""
    rows = []
    for exchange in transcript(name).values():
        if not exchange["request"].startswith("POST"):
            continue
        fields = dict(parse_qsl(exchange["request-body"], keep_blank_values=True))
        assert fields.pop("action") == "racer.import"
        assert b'"summary": "success"' in exchange["body"]
        rows.append(Row(len(rows) + 2, fields))
    return rows


def fixture(name: str) -> str:
    """The transcript's path as `deviations.json` names it."""
    return f"http/import/{ROSTERS[name][0]}.http"


def listing(name: str) -> tuple[str, dict]:
    """The transcript's `racer.list` exchange, by name."""
    (found,) = [
        (key, e)
        for key, e in transcript(name).items()
        if "query=racer.list" in e["request"]
    ]
    return found


def listed_racers(body: bytes) -> list[tuple]:
    return sorted(
        (
            r["firstname"],
            r["lastname"],
            r["carnumber"],
            r["carname"],
            r["note_from"],
            r["partition"],
            r["class"],
            r["rank"],
        )
        for r in json.loads(body)["racers"]
    )


def php_racers(name: str) -> list[tuple]:
    """The racers PHP's page made from the file, from its `racer.list`."""
    return listed_racers(listing(name)[1]["body"])


def expected_racers(name: str) -> list[tuple]:
    """The racers Django makes from the file: PHP's, but for a `racer.list`
    that `deviations.json` lists, whose expected exchange it takes."""
    key, _ = listing(name)
    listed = transcript_deviations(fixture(name))
    if key in listed:
        return listed_racers(listed[key][1]["body"])
    return php_racers(name)


def racers(event) -> list[tuple]:
    """The event's racers in the shape of `php_racers`."""
    return sorted(
        Racer.objects.filter(event=event).values_list(
            "first_name",
            "last_name",
            "car_number",
            "car_name",
            "note",
            "partition__name",
            "group__name",
            "subgroup__name",
        )
    )
