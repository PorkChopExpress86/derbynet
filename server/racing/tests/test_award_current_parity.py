"""`query=award.current` for every listed speed award against the PHP
transcripts under `tests/golden/http/award-current/standings/` (#33).

After each `award.list` of a standings fixture, the capture presented every
speed award it listed and kept PHP's `query=award.current`, named
`<label> <key> award.current` (`capture_award_list` in
`capture/capture-http.sh`). Django's reply is built as the endpoint builds
it: the key looked up among the awards Django lists under the settings of
the fixture's `<label> award.list` (`current_speed_award`), and the racer's
fields filled in by `awards.listing.presented`. The capture presented each
award revealed, and PHP's duration is masked.

Where Django's award list differs from PHP's on purpose (Q22, Q23), the
exchanges `deviations.json` lists must match their expected reply instead,
and differ from PHP's. To write those entries again after a change, run
`WRITE_DEVIATIONS=1 uv run pytest racing/tests/test_award_current_parity.py`
from `server/`.
"""

import os
import re
from pathlib import Path

import pytest

from awards.listing import presented
from racing.engine.standings.speed_awards import current_speed_award
from racing.engine.tests.golden import (
    GOLDEN_DIR,
    read_transcript,
    transcript_deviations,
    write_deviation_entries,
)
from racing.engine.tests.standings_fixtures import Fixture, label, php_json
from racing.engine.tests.test_speed_awards_parity import (
    AWARD_LIST,
    REASONS,
    django_awards,
    quirk_of,
    raw_headers,
)

AWARD_CURRENT_DIR = GOLDEN_DIR / "http" / "award-current" / "standings"


def transcripts() -> list[Path]:
    return sorted(AWARD_CURRENT_DIR.glob("*.http"))


def fixture_name(path: Path) -> str:
    return f"http/award-current/standings/{path.name}"


def award_list_of(fixture: Fixture, exchange: str) -> str:
    """The fixture's `award.list` exchange whose settings `exchange` ran under."""
    *words, _key, _request = exchange.split(" ")[1:]
    lists = {label(name): name for name in fixture.named(AWARD_LIST)}
    return lists[" ".join(words)]


def django_body(fixture: Fixture, exchange: str, computed: dict) -> bytes:
    """Django's `query=award.current` body for `exchange`. `computed` keeps
    each `award.list`'s standings and awards, which its keys share."""
    key = exchange.split(" ")[-2]
    listed = award_list_of(fixture, exchange)
    if listed not in computed:
        computed[listed] = django_awards(fixture, listed)
    standings, awards = computed[listed]
    found = current_speed_award(key, standings, awards)
    if found is None or found.racer_id is None:
        return php_json({}) + b"\n"
    racer = standings.racers[found.racer_id]
    shown = presented(
        found.key,
        found.name,
        True,
        racer,
        standings.subgroups[racer.subgroup_id].name,
        name_style=0,
        use_subgroups=fixture.state(listed).use_subgroups,
    )
    shown["duration"] = "<seconds>"
    return php_json({"current": shown}) + b"\n"


def test_award_current_matches_php_for_every_listed_speed_award():
    wrong = []
    compared = deviated = 0
    for path in transcripts():
        fixture = Fixture(path.stem)
        expected = transcript_deviations(fixture_name(path))
        computed = {}
        for name, exchange in read_transcript(path).items():
            django = django_body(fixture, name, computed)
            if name in expected:
                deviated += 1
                ok = django == expected[name][1]["body"] and django != exchange["body"]
            else:
                compared += 1
                ok = django == exchange["body"]
            if not ok:
                wrong.append((path.stem, name))
    assert wrong == []
    assert compared > 4000
    assert 0 < deviated < 20


def DEVIATION_CLAIM(quirk: str, fixture: str) -> bool:  # noqa: N802
    """Each quirk of REASONS in each `award.current` transcript: which
    exchanges differ is known only once the writer runs."""
    return quirk in REASONS and fixture.startswith("http/award-current/standings/")


def write_award_current_deviations():
    """Every exchange where Django's reply differs from PHP's, listed under
    the quirk of its `award.list`, with Django's exchanges under
    `deviations/<quirk>/award-current/`."""
    written: dict[tuple[str, str], tuple[list[str], bytes]] = {}
    for path in transcripts():
        fixture = Fixture(path.stem)
        headers = raw_headers(path)
        computed = {}
        for name, exchange in read_transcript(path).items():
            django = django_body(fixture, name, computed)
            if django == exchange["body"]:
                continue
            quirk = quirk_of(fixture, award_list_of(fixture, name))
            head = re.sub(
                rb"\nlength: \d+", f"\nlength: {len(django)}".encode(), headers[name]
            )
            names, out = written.get((path.stem, quirk), ([], b""))
            written[(path.stem, quirk)] = (
                [*names, name],
                out + head + b"\nbody:\n" + django + b"\n",
            )
    entries = []
    for (stem, quirk), (names, out) in sorted(written.items()):
        expected = f"deviations/{quirk}/award-current/{stem}.http"
        path = GOLDEN_DIR / expected
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(out)
        entries.append(
            {
                "quirk": quirk,
                "fixture": f"http/award-current/standings/{stem}.http",
                "exchanges": names,
                "reason": REASONS[quirk],
                "expected": expected,
            }
        )
    write_deviation_entries(DEVIATION_CLAIM, entries)


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the award.current entries"
)
def test_write_award_current_deviations():
    write_award_current_deviations()
