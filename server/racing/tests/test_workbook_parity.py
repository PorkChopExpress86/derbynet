"""The Standings and speed-award sheets of `export-json.php` against the PHP
transcripts under `tests/golden/http/standings/` (#36)."""

import json

import pytest

from racing.engine.standings.oracle import Standings
from racing.engine.standings.speed_awards import speed_awards
from racing.engine.tests.golden import read_transcript
from racing.engine.tests.standings_fixtures import EXPORT, STANDINGS_DIR, Fixture
from racing.services.workbook import Layout, speed_award_rows, standings_sheets
from roster.services.groups import Labels

STEMS = sorted(
    path.stem
    for path in STANDINGS_DIR.glob("*.http")
    if any(e["request"] == EXPORT for e in read_transcript(path).values())
)


by_partition = Labels("Pack", "Den", "Den", "Subgroup")
one_group = Labels("Pack", "Den", "Group", "Den")


def one_group_rule(event) -> bool:
    """The suites that rank racers by den put them in one group."""
    return len(event.groups) == 1 and event.settings.use_subgroups


def layout_of(fixture: Fixture, exchange: str, event) -> Layout:
    state = fixture.state(exchange)
    return Layout(
        names=one_group if one_group_rule(event) else by_partition,
        use_subgroups=event.settings.use_subgroups,
        use_points=event.settings.use_points,
        scoring=int(event.settings.scoring),
        lane_count=event.settings.lane_count,
        finish_formatting=f"%5.{state.time_digits}f",
        show_car_name=any(r.car_name for r in event.racers),
    )


def php_sheets(fixture: Fixture, exchange: str):
    expected = fixture.expected(exchange)
    return json.loads(expected["body"])


def exchanges():
    return list(_exchanges())


def _exchanges():
    for stem in STEMS:
        fixture = Fixture(stem)
        for name in fixture.named(EXPORT):
            if name in fixture.deviated:
                continue
            if not fixture.exchanges[name]["body"].startswith(b"["):
                continue
            yield pytest.param(fixture, name, id=f"{stem}-{name}")


@pytest.mark.parametrize(("fixture", "exchange"), exchanges())
def test_standings_sheets_match_php(fixture, exchange):
    event = fixture.event(exchange)
    standings = Standings(event)
    ours = standings_sheets(layout_of(fixture, exchange, event), standings)
    theirs = [s for s in php_sheets(fixture, exchange) if s[0].startswith("Standings")]
    assert json.loads(json.dumps(ours)) == theirs


@pytest.mark.parametrize(("fixture", "exchange"), exchanges())
def test_speed_award_rows_match_php(fixture, exchange):
    event = fixture.event(exchange)
    standings = Standings(event)
    awards = speed_awards(standings, fixture.state(exchange).trophies).awards
    ours = speed_award_rows(layout_of(fixture, exchange, event), standings, awards)
    sheet = dict(php_sheets(fixture, exchange))["Awards"][1:]
    theirs = [row for row in sheet if row[1] == "Speed Trophy"]
    assert json.loads(json.dumps(ours)) == theirs
