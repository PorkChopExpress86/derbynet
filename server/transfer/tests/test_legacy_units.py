"""The pure pieces of the importer: the upgrade chain, times, and photo paths."""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from transfer.legacy.photos import relative_parts
from transfer.legacy.upgrade import upgrade
from transfer.legacy.values import BadValue, kiosk_page, local_time


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
    assert len(tables["scenekiosk"]) == 14
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


def test_kiosk_page_splits_reload_and_params():
    assert kiosk_page('!kiosks/x.kiosk#{"a":1}') == ("kiosks/x.kiosk", {"a": 1}, True)
    assert kiosk_page(None) == ("", {}, False)
    with pytest.raises(BadValue):
        kiosk_page("kiosks/x.kiosk#{confetti:false}")
