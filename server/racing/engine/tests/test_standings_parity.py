"""The standings engine against every PHP standings transcript.

Each transcript under `tests/golden/http/standings/` that records
`query=standings` is rebuilt as an engine event from the fixtures alone
(`standings_fixtures`), under the settings each exchange ran with: scoring
0, 1 and 2, time and points racing, subgroups on and off. Every ladder of
every standings exchange must equal PHP's, racer for racer and rung for
rung, and so must every place `export-json.php` prints: each catalog
presentation's sheet, in catalog order, with its title and name, and each
row's places, heats, scores and names.

A deviation listed in `tests/golden/deviations.json` replaces PHP's exchange
with Django's (D25). The speed-award test owns the Q7 entries.

`UNCOMPARABLE` lists the exchanges where PHP gave no standings to compare,
each with the reason, and each is checked to be what the list says.
"""

import pytest

from racing.engine.standings.oracle import (
    FULL_FIELD,
    Standings,
    round_key,
    round_subgroup_key,
)
from racing.engine.standings.ordinals import ordinal

from .standings_fixtures import (
    EXPORT,
    SNAPSHOT,
    STANDINGS,
    STANDINGS_DIR,
    Fixture,
    body,
    racer_key,
    standings_transcripts,
)

# Time scoring over heats that hold places only, as test-points-racing.sh
# leaves its first round: a racer with no time has a NULL score, which
# StandingsTracker::AddEntry compares as a tie with last_score -1 and files
# under rung -1. The next racer with a score then gets a place past the end
# of the ladder, and `count($this->all_standings[$key][$place])`
# (standings.inc line 382) throws a TypeError, so PHP answers 500. Under
# scoring 1 every timed racer scores 0, joins the "tie", and PHP answers a
# ladder that is an object keyed from "-1". Django gives the NULL score no
# place (`oracle.Row`).
NULL_SCORE_500 = "a NULL score in time scoring: TypeError at standings.inc:382"
NULL_SCORE_RUNG = "a NULL score in time scoring: PHP files it under rung -1"
POINTS = "suite-points-racing"
UNCOMPARABLE = {
    (POINTS, "004 points=0 scoring=0 subgroups=0 standings"): NULL_SCORE_500,
    (POINTS, "006 points=0 scoring=0 subgroups=1 standings"): NULL_SCORE_500,
    (POINTS, "008 points=0 scoring=1 subgroups=0 standings"): NULL_SCORE_RUNG,
    (POINTS, "010 points=0 scoring=1 subgroups=1 standings"): NULL_SCORE_RUNG,
    (POINTS, "012 points=0 scoring=2 subgroups=0 standings"): NULL_SCORE_500,
    (POINTS, "014 points=0 scoring=2 subgroups=1 standings"): NULL_SCORE_500,
}

FIXTURES = standings_transcripts()
EXPORTING = sorted({*FIXTURES, "award-settings"} - {SNAPSHOT})


@pytest.fixture(scope="module", params=FIXTURES)
def fixture(request):
    return Fixture(request.param)


def test_every_standings_transcript_is_compared():
    stems = {p.stem for p in STANDINGS_DIR.glob("*.http")}
    assert set(FIXTURES) == stems - {"award-settings", "edge-cases-setup"}
    assert {"q7-tie-after-earlier-trophy", SNAPSHOT} <= set(FIXTURES)
    assert len([s for s in FIXTURES if s.startswith(("edge-cases-", "suite-"))]) == 12


def test_every_ladder_matches_php(fixture):
    compared = 0
    wrong = {}
    for name in fixture.named(STANDINGS):
        if (fixture.stem, name) in UNCOMPARABLE:
            continue
        php = body(fixture.expected(name))["standings"]
        django = Standings(fixture.event(name)).ladders
        compared += 1
        for key in php.keys() | django.keys():
            if php.get(key) != django.get(key):
                wrong[f"{name} {key}"] = (
                    named_ladder(fixture, django.get(key)),
                    named_ladder(fixture, php.get(key)),
                )
    assert wrong == {}
    assert compared + sum(s == fixture.stem for s, _ in UNCOMPARABLE) == len(
        fixture.named(STANDINGS)
    )


@pytest.mark.parametrize(("stem", "exchange"), sorted(UNCOMPARABLE))
def test_uncomparable_exchanges_are_what_the_list_says(stem, exchange):
    fixture = Fixture(stem)
    php = fixture.exchanges[exchange]
    rows = Standings(fixture.event(exchange)).rows
    assert any(r.summary.score is None for r in rows), "a racer has no score"
    assert any(r.summary.score is not None for r in rows), "a racer has a score"
    if UNCOMPARABLE[(stem, exchange)] == NULL_SCORE_500:
        assert (php["status"], php["body"]) == ("500", b"")
    else:
        ladders = body(php)["standings"].values()
        assert php["status"] == "200"
        assert all(isinstance(ladder, dict) and "-1" in ladder for ladder in ladders)


@pytest.mark.parametrize("stem", EXPORTING)
def test_every_export_sheet_matches_php(stem):
    fixture = Fixture(stem)
    exports = fixture.named(EXPORT)
    assert exports
    for name in exports:
        sheets = body(fixture.expected(name))
        php = [(title, rows) for title, rows in sheets if title.startswith("Standings")]
        django = export_standings(fixture, name)
        assert [t for t, _ in django] == [t for t, _ in php], name
        for (title, mine), (_, theirs) in zip(django, php, strict=True):
            assert mine[0] == theirs[0], (name, title)
            assert mine[1:] == theirs[3:], (name, title)


def export_standings(fixture: Fixture, exchange: str):
    """The Standings sheets of `export_all()` from the engine: per catalog
    entry, the sheet title and the "Standings for" line, then each row as
    `export_standings()` writes it. The scoring line and the headings are
    not compared; they name settings, not standings."""
    event = fixture.event(exchange)
    state = fixture.state(exchange)
    standings = Standings(event)
    racers = event.racers
    show_car_name = any(r.car_name for r in racers)
    use_groups = len(event.groups) > 1
    use_subgroups = event.settings.use_subgroups
    sheets = []
    for entry in standings.catalog():
        title = "Standings " + entry.name.replace("/", "-")
        if len(title.encode()) > 31:
            title = "Standings " + entry.key
        if entry.presentation == "ff":
            title = "Standings"
        rows = [[f"Standings for {entry.name}"]]
        for row in standings.rows:
            p = row.outcome
            if entry.key not in p:
                continue
            racer = row.racer
            values = [
                p[entry.key],
                racer.car_number,
                f"{racer.first_name} {racer.last_name}",
            ]
            if show_car_name:
                values.append(racer.car_name)
            if use_groups:
                values.append(standings.groups[racer.group_id].name)
                shown = entry.presentation in ("ff", "class", "subgroup")
                values.append(p[round_key(row.round.id)] if shown else "")
            if use_subgroups:
                values.append(standings.subgroups[racer.subgroup_id].name)
                values.append(
                    p.get(round_subgroup_key(row.round.id, racer.subgroup_id))
                )
            if entry.presentation == "subgroup":
                values.append(p.get(FULL_FIELD, ""))
            summary = row.summary
            values.append(summary.base)
            if event.settings.use_points:
                values += [summary.avg, ordinal(summary.best), ordinal(summary.worst)]
            else:
                digits = state.time_digits
                values += [
                    float(f"{v:.{digits}f}")
                    for v in (summary.avg, summary.best, summary.worst)
                ]
            rows.append(values)
        sheets.append((title, rows))
    return sheets


@pytest.mark.parametrize("stem", EXPORTING)
def test_the_as_left_settings_agree_with_the_export(stem):
    """The settings `AS_LEFT` reads from the scripts, where the export shows
    them: the subgroup column, the scoring line, the full-field sheet."""
    fixture = Fixture(stem)
    for name in fixture.named(EXPORT):
        event = fixture.event(name)
        state = fixture.state(name)
        sheets = dict(body(fixture.exchanges[name]))
        roster = sheets["Roster"][0]
        columns = roster[roster.index("Car Name") + 1 : roster.index("Excluded?")]
        assert len(columns) == (len(event.groups) > 1) + state.use_subgroups, name
        assert ("Standings" in sheets) == (event.settings.full_field_calc == 0), name
        lines = {
            s[1][0] for title, s in sheets.items() if title.startswith("Standings")
        }
        if state.use_points:
            assert lines == {"Scoring by points"}, name
        else:
            scoring = [
                "Averaging all heat times",
                "Dropping each racer's slowest time",
                "Using each racer's fastest time only",
            ][state.scoring]
            assert lines <= {scoring}, name


def test_the_edge_cases_the_ticket_lists_are_compared():
    all_heats = Fixture("edge-cases-all-heats")
    six_heats = Fixture("edge-cases-six-heats-of-nine")
    average = "004 points=0 scoring=0 subgroups=0 standings"

    ladder = named_ladder(
        all_heats, body(all_heats.exchanges[average])["standings"]["st-r1"]
    )
    assert ["101 Ann Lee", "102 Bob lee"] in ladder, "an exact tie"
    three = ladder.index(["103 Cy Moss", "104 Di Moss", "105 Ed Moss"])
    assert ladder[three + 1 : three + 3] == [[], []], (
        "a three-way tie and two empty rungs"
    )
    points = "016 points=1 scoring=0 subgroups=0 standings"
    ladder = named_ladder(
        all_heats, body(all_heats.exchanges[points])["standings"]["st-r1"]
    )
    assert ladder[0] == ["101 Ann Lee", "102 Bob lee", "103 Cy Moss"], (
        "last names that differ only in case, ordered as NOCASE orders them"
    )

    excluded = [
        r
        for r in all_heats.event(average).racers
        if r.awards.not_eligible_for_any_award
    ]
    assert [racer_key(r.car_number, r.first_name, r.last_name) for r in excluded] == [
        "109 Ivy Quill"
    ], "an excluded racer"
    assert "109 Ivy Quill" not in {n for rung in ladder for n in rung}

    drop_slowest = "008 points=0 scoring=1 subgroups=0 standings"
    rows = Standings(six_heats.event(drop_slowest)).rows
    one_heat = [r for r in rows if r.summary.base == 1 and r.outcome]
    assert one_heat, "a racer with one heat under scoring 1"
    assert drop_slowest in six_heats.named(STANDINGS)


def named_ladder(fixture: Fixture, ladder):
    if not isinstance(ladder, list):
        return ladder
    return [[fixture.car_of(r) for r in rung] for rung in ladder]
