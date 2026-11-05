"""The standings kiosk against PHP's capture of it (#52, D33):
`capture/scenarios/07-standings-edge-cases.sh` replayed through the legacy
API, then `kiosk.php?page=kiosks/standings.kiosk` from a session of its own,
as `http/kiosks/standings-edge-cases.http` records it.

The roster has Ivy Quill (109), the fastest car, excluded. PHP leaves her out
of the rows and adds a footnote row; Django lists her in score order, marked
not eligible, with no place (D33). The two pages share no markup (D10), so
the D33 entry records only the rows of the presentation shown with no
parameters, as `kiosk_rows` reads them, and the test compares those.

`WRITE_DEVIATIONS=1 uv run pytest ui/tests/test_kiosk_standings_parity.py`
rewrites the D33 entry and its expected transcript.
"""

import html
import json
import os
import re

import pytest
from django.test import Client

from legacy_api.tests.test_race_control_parity import Scenario, _exchange_text
from legacy_api.tests.test_standings import race_with, start_round
from racing.engine.tests.golden import (
    GOLDEN_DIR,
    read_transcript,
    transcript_deviations,
    write_deviation_entries,
)

pytestmark = pytest.mark.django_db

FIXTURE = "http/kiosks/standings-edge-cases.http"
EXCHANGE = "001 standings-kiosk"
EXPECTED = "deviations/D33/standings-edge-cases.http"
REASON = (
    "PHP's standings kiosk leaves out the excluded racer and adds a row"
    " 'Results for 1 ineligible racer not shown'; Django lists the racer in"
    " score order, marked not eligible, with no place (D33). Only the rows of"
    " the presentation shown are recorded, as [place, car, name, why]."
)
DEVIATION_CLAIM = {("D33", FIXTURE)}

ROSTER = [
    "firstname=Ann&lastname=Lee&carnumber=101&partition=Edge",
    "firstname=Bob&lastname=lee&carnumber=102&partition=Edge",
    "firstname=Cy&lastname=Moss&carnumber=103&partition=Edge",
    "firstname=Di&lastname=Moss&carnumber=104&partition=Edge",
    "firstname=Ed&lastname=Moss&carnumber=105&partition=Edge",
    "firstname=Flo&lastname=Nye&carnumber=106&partition=Edge",
    "firstname=Gus&lastname=Oak&carnumber=107&partition=Edge",
    "firstname=Hal&lastname=Pine&carnumber=108&partition=Edge",
    "firstname=Ivy&lastname=Quill&carnumber=109&partition=Edge&exclude=yes",
]
# RACE_TIME_EXPR of the scenario, in seconds: one time per car in every heat.
TIMES = {101: 3, 102: 3, 103: 3.1, 104: 3.1, 105: 3.1}
TIMES |= {106: 3.2, 107: 3.3, 108: 3.4, 109: 2.5}


def text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def kiosk_rows(body: bytes) -> list[list[str]]:
    """Django's rows: [place, car, name, why not eligible], in order."""
    rows = []
    for row in re.findall(r'<li class="k-row".*?</li>', body.decode(), re.S):
        place = re.search(r"data-place>(.*?)</span>\s*<span data-car", row, re.S)
        why = re.search(r"data-why>(.*?)</div>", row, re.S)
        rows.append(
            [
                "" if "No place" in place[1] else text(place[1]),
                text(re.search(r"data-car>(.*?)</span>", row, re.S)[1]),
                text(re.search(r"data-name>(.*?)</div>", row, re.S)[1]),
                text(why[1]) if why else "",
            ]
        )
    return rows


def php_kiosk_rows(body: bytes) -> list[list[str]]:
    """PHP's rows of the first catalog entry, read the same way: the place
    from the entry's place div, and a footnote row as its text under `why`."""
    page = body.decode()
    catalog = json.loads(re.search(r"var g_standings_catalog = (.*?);", page)[1])
    key = catalog[0]["key"]
    div = "insuper" if key == "st-ff" else "inround"
    rows = []
    for classes, cells in re.findall(r'<tr[^>]* class="([^"]*)">(.*?)</tr>', page):
        if key not in classes.split():
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", cells)
        place = re.search(rf"<div class='{div}'>(.*?)</div>", tds[0])[1]
        rows.append([text(place), text(tds[1]), text(tds[2]), ""])
    for classes, note in re.findall(
        r"<tr class='([^']*)'><td colspan='\d+'>(.*?)</td></tr>", page
    ):
        if key in classes.split():
            rows.append(["", "", "", text(note)])
    return rows


def rows_body(rows: list[list[str]]) -> bytes:
    return json.dumps(rows, ensure_ascii=False).encode()


def run_edge_cases(client) -> bytes:
    """The scenario's roster raced out, then the kiosk page's rows."""
    s = Scenario(client)
    s.reset()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=2")
    for row in ROSTER:
        s.import_racer(row)
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    start_round(s, "Edge")
    race_with(s, lambda car, lane: TIMES[car])

    request = read_transcript(GOLDEN_DIR / FIXTURE)[EXCHANGE]["request"]
    response = Client().get("/" + request.removeprefix("GET "))
    assert response.status_code == 200
    return rows_body(kiosk_rows(response.content))


def test_php_leaves_the_excluded_racer_out_with_a_footnote():
    php = php_kiosk_rows(read_transcript(GOLDEN_DIR / FIXTURE)[EXCHANGE]["body"])

    assert [r[1] for r in php if r[1]] == [str(c) for c in range(101, 109)]
    assert php[-1] == ["", "", "", "Results for 1 ineligible racer not shown"]


def test_d33_the_standings_kiosk_lists_the_excluded_racer_with_no_place(client, event):
    django = run_edge_cases(client)
    php = read_transcript(GOLDEN_DIR / FIXTURE)[EXCHANGE]
    [(quirk, expected)] = transcript_deviations(FIXTURE).values()

    assert quirk == "D33"
    assert expected["body"] == django
    assert django != rows_body(php_kiosk_rows(php["body"]))
    rows = json.loads(django)
    [ivy] = [r for r in rows if r[1] == "109"]
    assert ivy[0] == "" and ivy[3]
    assert [r[1] for r in rows if r[0]] == [str(c) for c in range(101, 109)]


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the #52 D33 entry"
)
def test_write_kiosk_standings_deviations(client, event):
    django = run_edge_cases(client)
    php = read_transcript(GOLDEN_DIR / FIXTURE)
    path = GOLDEN_DIR / EXPECTED
    path.parent.mkdir(parents=True, exist_ok=True)
    # PHP's request and status; the body is the rows, so no content type.
    header = {k: php[EXCHANGE][k] for k in ("request", "status")}
    path.write_bytes(_exchange_text(EXCHANGE, header, django))
    entry = {
        "quirk": "D33",
        "fixture": FIXTURE,
        "exchanges": [EXCHANGE],
        "reason": REASON,
        "expected": EXPECTED,
    }
    write_deviation_entries(DEVIATION_CLAIM, [entry])
