"""The deviation list follows the rules of D25 (#20, "How fixtures record
decisions"). Whether Django's output equals each expected item is checked by
the parity test of each fixture: the engine parity tests now, the legacy API
transcripts once their handlers exist (#31)."""

import importlib
import importlib.util
import json
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from racing.engine.tests.golden import (
    GOLDEN_DIR,
    HAND_RECORDED,
    QUIRK_DECISIONS,
    claims,
    deviations,
    read_cases,
    read_transcript,
)


def items(entry):
    return entry.get("cases") or entry.get("exchanges") or []


def test_every_entry_names_a_fix_quirk_and_a_reason():
    for entry in deviations():
        assert QUIRK_DECISIONS.get(entry["quirk"]) == "fix", entry["quirk"]
        assert entry["reason"].strip(), entry
        assert ("cases" in entry) != ("exchanges" in entry), entry
        assert items(entry), entry


def test_no_item_is_named_twice():
    named = Counter(
        (entry["fixture"], item) for entry in deviations() for item in items(entry)
    )
    assert [key for key, n in named.items() if n > 1] == []


def test_engine_entries_match_fixture_cases_and_expected_records():
    for entry in deviations():
        if "cases" not in entry:
            continue
        php = read_cases(GOLDEN_DIR / entry["fixture"])
        expected = json.loads((GOLDEN_DIR / entry["expected"]).read_text())
        assert set(entry["cases"]) <= set(php), entry["fixture"]
        assert set(expected) == set(entry["cases"]), entry["expected"]


def test_transcript_entries_match_fixture_exchanges_and_expected_files():
    for entry in deviations():
        if "exchanges" not in entry:
            continue
        php = read_transcript(GOLDEN_DIR / entry["fixture"])
        expected = read_transcript(GOLDEN_DIR / entry["expected"])
        assert list(expected) == entry["exchanges"], entry["expected"]
        for name in entry["exchanges"]:
            assert name in php, (entry["fixture"], name)
            for key in ("request", "request-body"):
                assert expected[name].get(key) == php[name].get(key), (name, key)
            assert expected[name] != php[name], f"{name} does not differ from PHP"


SERVER_DIR = Path(__file__).resolve().parents[1]

# Every writer of deviations.json, by the module that declares its
# DEVIATION_CLAIM, the (quirk, fixture) pairs it rewrites and keeps no others.
WRITERS = [
    "legacy_api.tests.test_racers_parity",
    "legacy_api.tests.test_race_control_parity",
    "legacy_api.tests.test_awards_parity",
    "legacy_api.tests.test_kiosks_parity",
    "racing.services.tests.test_heat_state_parity",
    "racing.services.tests.test_master",
    "racing.services.tests.test_schedule",
    "racing.engine.tests.test_aggregates_parity",
    "racing.engine.tests.test_lane_bias",
    "racing.engine.tests.test_speed_awards_parity",
    "tools/reschedule_deviations.py",
]


def writer_claim(writer):
    if writer.endswith(".py"):
        spec = importlib.util.spec_from_file_location(
            Path(writer).stem, SERVER_DIR / writer
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(writer)
    return module.DEVIATION_CLAIM


def test_every_entry_has_exactly_one_writer():
    """A writer replaces only the entries it claims, so an entry claimed by
    two writers is deleted or rewritten by whichever runs last, and one
    claimed by none is never rewritten."""
    owners = {writer: writer_claim(writer) for writer in WRITERS}
    owners["by hand (golden.HAND_RECORDED)"] = HAND_RECORDED
    wrong = {}
    for entry in deviations():
        claimed_by = [name for name, claim in owners.items() if claims(claim, entry)]
        if len(claimed_by) != 1:
            wrong[entry["quirk"], entry["fixture"]] = claimed_by
    assert wrong == {}


def test_the_transcript_reader_checks_lengths(tmp_path):
    path = tmp_path / "bad.http"
    path.write_bytes(
        b"### 001 x\nrequest: GET /\nstatus: 200\nlength: 3\nbody:\nabcd\n"
    )
    with pytest.raises(ValueError, match="wrong length"):
        read_transcript(path)


def schedule_generate_roundid(exchange):
    """The roundid of a `schedule.generate` request, or None."""
    fields = dict(parse_qsl(exchange.get("request-body", "")))
    if fields.get("action") != "schedule.generate":
        return None
    return int(fields["roundid"])


def heats_scheduled(exchange):
    """`{roundid: heats scheduled}` from a poll with `values=...rounds`."""
    try:
        body = json.loads(exchange["body"])
    except ValueError:
        return None
    if "rounds" not in body or "outcome" in body:
        return None
    return {r["roundid"]: r["heats_scheduled"] for r in body["rounds"]}


def test_every_generate_that_php_lets_empty_a_round_is_under_q1():
    """Q1: PHP answers a `schedule.generate` it cannot fulfil with success and
    writes no heats. In the `http/schedule/` transcripts, each such generate
    must be listed under Q1, and so must the poll after it when the round had
    heats before, because Django keeps that chart."""
    q1 = {
        (entry["fixture"], name)
        for entry in deviations()
        if entry["quirk"] == "Q1"
        for name in entry.get("exchanges", [])
    }
    needed = set()
    for path in sorted((GOLDEN_DIR / "http" / "schedule").glob("*.http")):
        fixture = f"http/schedule/{path.name}"
        heats, pending = {}, None
        for name, exchange in read_transcript(path).items():
            roundid = schedule_generate_roundid(exchange)
            if roundid is not None:
                try:
                    success = (
                        json.loads(exchange["body"])["outcome"]["code"] == "success"
                    )
                except ValueError:
                    success = False
                pending = (name, roundid, heats.get(roundid, 0)) if success else None
                continue
            polled = heats_scheduled(exchange)
            if polled is None:
                continue
            if pending is not None:
                generate, roundid, before = pending
                if polled.get(roundid) == 0:
                    needed.add((fixture, generate))
                    if before:
                        needed.add((fixture, name))
                pending = None
            heats = polled
    assert (
        "http/schedule/zero-runs-over-a-chart-4-lanes.http",
        "005 generate-round-3-zero-runs",
    ) in needed
    assert sorted(needed - q1) == []
