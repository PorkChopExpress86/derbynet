"""Replay the PHP transcripts of `capture/scenarios/15-racers.sh` (#29)
through Django, and diff each response with the IDs normalized.

The scenario's unrecorded steps run here in the same order, through
`/action.php`. Requests name rows by PHP's ids; each is translated to the
Django row PHP's id stood for, learned from the responses before it. A
response matches when, with Django's ids written as PHP's, it encodes to
PHP's body byte for byte. The exchanges `deviations.json` lists must match
their expected Django output instead, and must differ from PHP's (D25).

`WRITE_DEVIATIONS=1 uv run pytest legacy_api/tests/test_racers_parity.py`
rewrites the #29 entries and their expected transcripts.
"""

import json
import os
import re
from datetime import timedelta
from urllib.parse import parse_qsl, quote, urlencode

import pytest
from django.test import Client

from legacy_api.encoding import encode_php_json
from racing.engine.tests.golden import (
    DEVIATIONS_FILE,
    GOLDEN_DIR,
    deviations,
    read_transcript,
    transcript_deviations,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def distinct_check_in_times(monkeypatch):
    """Each check-in a millisecond after the last, as the capture's curl
    calls are, so no two racers tie on `seq` in an `order=checkin` list."""
    from django.utils import timezone

    from roster.services import racers

    start = timezone.now()
    ticks = iter(range(1, 10**6))
    monkeypatch.setattr(
        racers.timezone,
        "now",
        lambda: start + timedelta(milliseconds=next(ticks)),
    )


ID_KEYS = ("racerid", "partitionid", "classid", "rankid")
# The scenario names no row with ids from here up.
UNKNOWN_ID = 9999
SEQ = re.compile(r'("seq": )[1-9]\d{9,}')

CHECK_IN_SETUP = [
    "firstname=Adolfo&lastname=Asher&partition=Lions&carnumber=101",
    "firstname=Ben&lastname=Bittinger&partition=Lions&carnumber=102",
    "firstname=Antoine&lastname=Akiyama&partition=Tigers&carnumber=201",
    "firstname=Diego&lastname=de la Cruz&partition=Tigers&carnumber=202",
    "firstname=Freeman&lastname=Fizer&partition=Bears&carnumber=301",
    "firstname=Twin&lastname=One&partition=Bears&carnumber=302",
    "firstname=Twin&lastname=Two&partition=Bears&carnumber=302",
]


class Ids:
    """PHP's id of each Django row, per id key, as the responses show it."""

    def __init__(self):
        self.to_php = {key: {} for key in ID_KEYS}

    def learn(self, php, django):
        if isinstance(php, dict) and isinstance(django, dict):
            for key in php.keys() & django.keys():
                if key in ID_KEYS and isinstance(php[key], int):
                    if isinstance(django[key], int):
                        self.to_php[key].setdefault(django[key], php[key])
                else:
                    self.learn(php[key], django[key])
        elif isinstance(php, list) and isinstance(django, list):
            for a, b in zip(php, django, strict=False):
                self.learn(a, b)

    def django(self, key, php_id):
        for django_id, known in self.to_php[key].items():
            if known == php_id:
                return django_id
        if php_id >= UNKNOWN_ID:
            return php_id  # an id the scenario chose to name no row
        raise KeyError(f"{key} {php_id} has not appeared yet")

    def as_php(self, value):
        if isinstance(value, dict):
            return {
                k: self.to_php[k].get(v, v)
                if k in ID_KEYS and isinstance(v, int)
                else self.as_php(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.as_php(v) for v in value]
        return value


# A request field that names a row, and the id key of that row.
_FIELD_KEYS = {"racer": "racerid", "partitionid": "partitionid"}
_BARCODE_ID = re.compile(r"PWDid(\d+)")
_WHO = {"c": "classid", "d": "partitionid", "r": "rankid"}


def translate(fields: list[tuple[str, str]], ids: Ids) -> list[tuple[str, str]]:
    out = []
    for key, value in fields:
        if key in _FIELD_KEYS and value.isdigit():
            value = str(ids.django(_FIELD_KEYS[key], int(value)))
        elif key == "barcode" and (m := _BARCODE_ID.fullmatch(value)):
            value = f"PWDid{ids.django('racerid', int(m[1]))}"
        elif key == "who" and value[:1] in _WHO and value[1:].isdigit():
            value = value[0] + str(ids.django(_WHO[value[0]], int(value[1:])))
        out.append((key, value))
    return out


def send(client, exchange, ids):
    method, target = exchange["request"].split(" ", 1)
    if method == "GET":
        path, _, query = target.partition("?")
        fields = translate(parse_qsl(query, keep_blank_values=True), ids)
        return client.get(f"/{path}?{urlencode(fields, quote_via=quote)}")
    fields = translate(parse_qsl(exchange["request-body"], keep_blank_values=True), ids)
    return client.post(
        "/" + target,
        urlencode(fields, quote_via=quote),
        content_type="application/x-www-form-urlencoded",
    )


def as_php_body(response, php_body: bytes, ids: Ids) -> bytes:
    """Django's body with its ids written as PHP's and its check-in times
    masked as `normalize.mjs` masks PHP's."""
    django = json.loads(response.content)
    php = json.loads(php_body)
    ids.learn(php, django)
    if "action" in php:
        # The echo of the request, which named rows by Django's ids.
        assert list(django["action"]) == list(php["action"])
        django["action"] = php["action"]
    body = encode_php_json(ids.as_php(django)).decode()
    return SEQ.sub(r'\1"<epoch-ms>"', body).encode()


def login(client, name, password):
    reply = client.post(
        "/action.php", {"action": "role.login", "name": name, "password": password}
    )
    assert json.loads(reply.content)["outcome"]["code"] == "success"
    return client


def post_ok(client, body):
    reply = json.loads(
        client.post(
            "/action.php", body, content_type="application/x-www-form-urlencoded"
        ).content
    )
    assert reply["outcome"]["code"] == "success", reply


def reset(client):
    """`reset_database` of the capture: a coordinator, and an empty event."""
    login(client, "RaceCoordinator", "doyourbest")
    post_ok(client, "action=database.execute&script=schema")


def import_racers(client, rows):
    for row in rows:
        post_ok(client, f"action=racer.import&{row}")


class Run:
    """One transcript replayed, exchange by exchange."""

    def __init__(self, fixture):
        self.fixture = fixture
        self.php = read_transcript(GOLDEN_DIR / fixture)
        self.expected = transcript_deviations(fixture)
        self.ids = Ids()
        self.got = {}

    def replay(self, client, name):
        exchange = self.php[name]
        response = send(client, exchange, self.ids)
        assert response.status_code == int(exchange["status"]), name
        self.got[name] = as_php_body(response, exchange["body"], self.ids)
        return self.got[name]

    def check(self):
        assert set(self.got) == set(self.php)
        for name, body in self.got.items():
            if name in self.expected:
                quirk, expected = self.expected[name]
                assert body == expected["body"], (quirk, name)
                assert body != self.php[name]["body"], (quirk, name, "now matches PHP")
            else:
                assert body.decode() == self.php[name]["body"].decode(), name


def run_check_in(client):
    reset(client)
    import_racers(client, CHECK_IN_SETUP)
    run = Run("http/racers/check-in.http")
    names = list(run.php)
    upto = names.index("015 list order=checkin")
    for name in names[:upto]:
        run.replay(client, name)
    # The scenario's unrecorded passes, by the ids PHP gave the racers.
    for php_id in (4, 6, 7):
        post_ok(
            client,
            f"action=racer.pass&racer={run.ids.django('racerid', php_id)}&value=1",
        )
    post_ok(client, "action=racer.pass&barcode=PWD310&value=1")
    photo = login(Client(), "Photo", "flashbulb")
    for name in names[upto:]:
        run.replay(photo if name.startswith("018 photo") else client, name)
    return run


def run_import_case(client):
    reset(client)
    import_racers(client, [CHECK_IN_SETUP[0]])
    run = Run("http/racers/import-partition-case.http")
    for name in run.php:
        run.replay(client, name)
    return run


def run_walk_in_multiplier_0(client):
    reset(client)
    post_ok(client, "action=settings.write&car-numbering=0%2B1")
    import_racers(
        client,
        [
            "firstname=Adolfo&lastname=Asher&partition=Lions",
            "firstname=Antoine&lastname=Akiyama&partition=Tigers",
        ],
    )
    # The walk-in's request comes before any response names a racer.
    run = Run("http/racers/walk-in-multiplier-0.http")
    for name in run.php:
        run.replay(client, name)
    return run


def run_refusals(client):
    reset(client)
    import_racers(client, [CHECK_IN_SETUP[0]])
    run = Run("http/racers/refusals.http")
    anonymous = Client()
    for name in run.php:
        run.replay(anonymous if "anonymous" in name else client, name)
    return run


def test_check_in_matches_php(client, event):
    run_check_in(client).check()


def test_the_unscripted_passes_named_the_racers_the_scenario_meant(client, event):
    """The ids 4, 6 and 7 above are Diego and the twins in PHP's numbering."""
    run = run_check_in(client)
    final = json.loads(run.php["028 list order=car at the end"]["body"])
    named = {r["racerid"]: f"{r['firstname']} {r['lastname']}" for r in final["racers"]}
    assert [named[i] for i in (4, 6, 7)] == ["Diego de la Cruz", "Twin One", "Twin Two"]


def test_a_partition_named_with_other_case_matches_php_but_for_d34(client, event):
    run_import_case(client).check()


def test_a_walk_in_with_multiplier_0_matches_php_but_for_c14(client, event):
    run_walk_in_multiplier_0(client).check()


def test_the_refusals_differ_from_php_as_listed(client, event):
    run_refusals(client).check()


# ----- Writing the deviation entries -----------------------------------------

DEVIATIONS = {
    "D5": (
        "http/racers/refusals.http",
        ["003 anonymous list"],
        "PHP lists every racer to anyone. Django's racer.list needs a role that"
        " works with racers; the anonymous VIEW_RACE_RESULTS is not enough (D5).",
        "deviations/D5/refusals.http",
        run_refusals,
    ),
    "S23": (
        "http/racers/refusals.http",
        ["004 anonymous pass PWD carno"],
        "PHP checks a racer in for anyone. Django needs CHECK_IN_RACERS or"
        " PHOTO_UPLOAD (S23, #15).",
        "deviations/S23/refusals.http",
        run_refusals,
    ),
    "R1": (
        "http/racers/refusals.http",
        ["001 edit unknown racer", "002 pass unknown racer"],
        "PHP answers success for a racer id that names no racer, and changes"
        " nothing. Django answers racer.edit with noracer and racer.pass with"
        " notunique (#29, accepted in D51).",
        "deviations/R1/refusals.http",
        run_refusals,
    ),
    "R2": (
        "http/racers/refusals.http",
        ["005 import text car number"],
        "PHP stores a car number such as A12 as text. Django's car number is an"
        " integer column, so it refuses one that is not a whole number with"
        " invalid-carnumber (#29, accepted in D60).",
        "deviations/R2/refusals.http",
        run_refusals,
    ),
    "C21": (
        "http/racers/check-in.http",
        ["025 bulk number", "026 bulk unknown who", "027 bulk eligibility"],
        "action.racer.bulk.inc starts with a space before <?php, so PHP's"
        " racer.bulk body is not clean JSON, the file that writes its XML"
        " refusal (C21). Django writes the JSON alone.",
        "deviations/C21/check-in.http",
        run_check_in,
    ),
    "D34": (
        "http/racers/import-partition-case.http",
        ["002 list after LIONS"],
        "PHP's racer.import makes a partition LIONS beside Lions. Django joins"
        " Lions, the partition named with case ignored (D34).",
        "deviations/D34/import-partition-case.http",
        run_import_case,
    ),
    "C14": (
        "http/racers/walk-in-multiplier-0.http",
        ["001 pass PWDwi new car", "002 list after walk-in"],
        "With car-numbering multiplier 0 PHP assigns $partition, not"
        " $partitionid, so a PWDwi walk-in falls to Default. Django puts it in"
        " the last partition (C14).",
        "deviations/C14/walk-in-multiplier-0.http",
        run_walk_in_multiplier_0,
    ),
}


def _exchange_text(name, php, body: bytes) -> bytes:
    lines = [f"### {name}"]
    for key in ("request", "request-body", "status", "content-type", "cache-control"):
        if key in php:
            lines.append(f"{key}: {php[key]}")
    lines += [f"length: {len(body)}", "body:"]
    return "\n".join(lines).encode() + b"\n" + body + b"\n"


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the #29 entries"
)
def test_write_racer_deviations(event):
    listing = json.loads(DEVIATIONS_FILE.read_text())
    entries = [e for e in deviations() if e["quirk"] not in DEVIATIONS]
    for quirk, (fixture, names, reason, expected, runner) in DEVIATIONS.items():
        run = runner(Client())
        path = GOLDEN_DIR / expected
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b"".join(_exchange_text(n, run.php[n], run.got[n]) for n in names)
        )
        entries.append(
            {
                "quirk": quirk,
                "fixture": fixture,
                "exchanges": names,
                "reason": reason,
                "expected": expected,
            }
        )
    listing["deviations"] = entries
    DEVIATIONS_FILE.write_text(json.dumps(listing, indent=2, ensure_ascii=False) + "\n")
