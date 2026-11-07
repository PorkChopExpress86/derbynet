"""The pure pieces of the importer: the upgrade chain, times, and photo paths."""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from transfer.legacy.photos import relative_parts
from transfer.legacy.upgrade import upgrade
from transfer.legacy.values import BadValue, local_time


def _v1_tables():
    return {
        "classes": [{"classid": 1, "class": "Lions"}, {"classid": 2, "class": "All"}],
        "ranks": [
            {"rankid": 7, "rank": "* Not specified", "classid": 1},
            {"rankid": 8, "rank": "Wolves", "classid": 1},
        ],
        "registrationinfo": [
            {"racerid": 3, "carnumber": 101, "classid": 1, "rankid": 8},
        ],
        "rounds": [{"roundid": 4, "round": 1, "classid": 1}],
        "raceinfo": [],
    }


def test_upgrade_from_schema_1_adds_columns_tables_and_partitions():
    tables = upgrade(_v1_tables(), 1)

    assert tables["classes"][0]["sortorder"] is None
    assert tables["classes"][0]["ntrophies"] == -1
    assert tables["registrationinfo"][0]["carphoto"] is None
    assert tables["registrationinfo"][0]["partitionid"] == 8
    assert [(s["sceneid"], s["name"]) for s in tables["scenes"]][:2] == [
        (1, "Welcome"),
        (2, "Opening"),
    ]
    # PHP's 14, less Wrap-Up's local/kiosks/feedback.kiosk (#52).
    assert len(tables["scenekiosk"]) == 13
    assert tables["partitions"] == [
        {"partitionid": 7, "name": "Default", "rankid": 7, "sortorder": None},
        {"partitionid": 8, "name": "Wolves", "rankid": 8, "sortorder": None},
    ]
    [logged] = tables["actionhistory"]
    assert json.loads(logged["request"]) == {
        "action": "partition.*structure",
        "partitions": [
            {"partitionid": 7, "classid": 1, "rankid": 7, "roundid": 4},
            {"partitionid": 8, "classid": 1, "rankid": 8, "roundid": 4},
        ],
        "ranks": [],
    }


def test_upgrade_turns_the_constituents_csv_into_rows_before_schema_8():
    tables = _v1_tables()
    tables["classes"][1]["constituents"] = "1,5"
    upgraded = upgrade(tables, 7)
    assert upgraded["constituentclasses"] == [
        {"antecedent": "1", "dependent": 2},
        {"antecedent": "5", "dependent": 2},
    ]


def test_upgrade_empties_the_tables_php_recreates():
    tables = {
        **_v1_tables(),
        "votes": [{"voteid": 1}],
        "actionhistory": [{"historyid": 1}],
    }
    upgraded = upgrade(tables, 9)
    assert upgraded["votes"] == [
        {"voteid": 1, "awardid": None, "racerid": None, "voterid": None}
    ]
    assert upgraded["actionhistory"] == []
    assert upgrade(tables, 5)["votes"] == []


def test_upgrade_leaves_the_source_rows_alone():
    tables = _v1_tables()
    upgrade(tables, 1)
    assert tables == _v1_tables()


def test_an_autumn_wall_time_reads_as_its_first_occurrence():
    zone = ZoneInfo("America/New_York")
    moment, ambiguous = local_time("2025-11-02 01:30:00", zone)
    assert ambiguous
    assert moment.astimezone(UTC) == datetime(2025, 11, 2, 5, 30, tzinfo=UTC)
    assert local_time("2025-11-02 03:30:00", zone)[1] is False
    assert local_time("", zone) == (None, False)
    with pytest.raises(BadValue):
        local_time("yesterday", zone)


@pytest.mark.parametrize(
    ("path", "directory", "parts"),
    [
        (
            "C:\\DerbyNet\\pack\\racers\\0-cropped\\a.jpg",
            "C:\\DerbyNet\\pack\\racers",
            ["0-cropped", "a.jpg"],
        ),
        ("/var/racers/a.jpg", "/var/racers/", ["a.jpg"]),
        ("/var/racers/../cars/a.jpg", "/var/racers", ["a.jpg"]),
        ("/elsewhere/0-cropped/a.jpg", "/var/racers", ["0-cropped", "a.jpg"]),
        ("D:\\moved\\0-cropped\\a.jpg", "/var/racers", ["0-cropped", "a.jpg"]),
        ("C:\\x\\a.jpg", None, ["a.jpg"]),
    ],
)
def test_relative_parts_reads_windows_and_posix_paths(path, directory, parts):
    assert relative_parts(path, directory) == parts


# Ids inside text (#231). The lookup adds 1000 to every id it knows.
KNOWN = {
    "roster.group": {3, 7},
    "roster.subgroup": {5},
    "racing.round": {4},
    "awards.award": {2},
}


def _lookup(model, legacy):
    return legacy + 1000 if legacy in KNOWN[model] else None


@pytest.mark.parametrize(
    ("place", "tie", "group", "subgroup"),
    [(1, "", None, None), (2, "b", 7, None), (3, "aa", 7, 12), (10, "", 3, 5)],
)
def test_a_speed_key_reads_back_as_it_was_built(place, tie, group, subgroup):
    from racing.engine.standings.speed_awards import SpeedKey, parse_speed_key

    key = SpeedKey(place, tie, group, subgroup)
    assert parse_speed_key(key.text) == key


@pytest.mark.parametrize(
    "key", ["speed-", "speed-0", "speed-02", "speed-1-", "speed-1-x", "speed-1-2-3-4"]
)
def test_text_no_speed_award_builds_is_no_speed_key(key):
    from racing.engine.standings.speed_awards import parse_speed_key

    assert parse_speed_key(key) is None


@pytest.mark.parametrize("key", ["st-ff", "st-r4", "st-rr4-5", "st-q7"])
def test_a_standings_key_reads_back_as_it_was_built(key):
    from racing.engine.standings.oracle import parse_standings_key

    assert parse_standings_key(key).text == key


@pytest.mark.parametrize(
    ("key", "mapped"),
    [
        ("", ""),
        ("award-2", "award-1002"),
        ("speed-1", "speed-1"),
        ("speed-1b-7", "speed-1b-1007"),
        ("speed-2-3-5", "speed-2-1003-1005"),
        ("award-9", ""),
        ("speed-1-9", ""),
        ("speed-1-3-9", ""),
        ("award-x", ""),
    ],
)
def test_an_award_key_maps_its_ids_or_clears(key, mapped):
    from transfer.legacy.embedded import award_key

    found = award_key(key, _lookup)
    assert found.text == mapped
    assert bool(found.dropped) == (mapped == "" and key != "")


CHECK_IN = "kiosks/please-check-in.kiosk"
STANDINGS = "kiosks/standings.kiosk"


@pytest.mark.parametrize(
    ("page", "params", "mapped", "dropped"),
    [
        (CHECK_IN, '{"classids":[7,3]}', '{"classids":[1007,1003]}', 0),
        (CHECK_IN, "{classids:[3]}", '{"classids":[1003]}', 0),
        (CHECK_IN, '{"classids":[3,9,"7",true]}', '{"classids":[1003]}', 3),
        (CHECK_IN, '{"classids":[9]}', '{"classids":[]}', 2),
        (CHECK_IN, '{"classids":[]}', '{"classids":[]}', 0),
        (CHECK_IN, "", "", 0),
        (
            "kiosks/slideshow.kiosk",
            '{"title":"Ünï","subdir":"a","classids":[3]}',
            '{"title":"Ünï","subdir":"a","classids":[1003]}',
            0,
        ),
        (STANDINGS, '{"key":"st-rr4-5"}', '{"key":"st-rr1004-1005"}', 0),
        (STANDINGS, '{"key":"st-ff"}', '{"key":"st-ff"}', 0),
        (STANDINGS, '{"key":"st-q9"}', "{}", 1),
        (STANDINGS, '{"key":"nonsense"}', "{}", 1),
        (STANDINGS, '{"classid":3,"rankid":9}', '{"classid":1003}', 1),
        ("kiosks/qrcode.kiosk", '{"title":"3","content":"7"}', None, 0),
        ("kiosks/award-presentations.kiosk", "{confetti:false}", None, 0),
        ("kiosks/welcome.kiosk", '{"classids":[3]}', None, 0),
    ],
)
def test_a_kiosks_parameters_map_their_ids(page, params, mapped, dropped):
    """Pages with no id parameter keep their text as it is (None)."""
    from transfer.legacy.embedded import kiosk_params

    found = kiosk_params(page, params, _lookup)
    assert found.text == (params if mapped is None else mapped)
    assert len(found.dropped) == dropped
