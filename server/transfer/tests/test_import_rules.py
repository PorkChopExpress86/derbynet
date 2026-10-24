"""The importer's rules for legacy data that PHP accepted but Django refuses."""

import hashlib
import io
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from awards.models import Award
from core.models import ActionLog, Event, RaceSettings, RaceState, SiteState
from kiosks.models import Kiosk
from roster.models import Group, GroupConstituent, Partition, Racer
from transfer.legacy import photos
from transfer.legacy.source import read_source
from transfer.models import LegacyId
from transfer.tests.legacy import (
    FIXTURES,
    SNAPSHOT,
    event_folder,
    legacy_folder,
    legacy_ids,
    run_import,
    source_rows,
)

pytestmark = pytest.mark.django_db


def _import(tmp_path, source, *args):
    return run_import(source, *args, report_dir=tmp_path)


def _event(report):
    return Event.objects.get(pk=report["event"]["id"])


def _findings(report, kind):
    return [f for f in report["findings"] if f["kind"] == kind]


def _racer(report, car_number):
    return Racer.objects.get(event=_event(report), car_number=car_number)


def _md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def test_the_source_file_is_never_written(tmp_path):
    database = legacy_folder(tmp_path, "v10")
    before = (_md5(database), database.stat().st_mtime_ns)
    database.chmod(0o444)

    _import(tmp_path, database)

    assert (_md5(database), database.stat().st_mtime_ns) == before
    assert sorted(p.name for p in database.parent.iterdir()) == [
        "cars",
        "derbynet.sqlite3",
        "racers",
    ]


def test_the_event_name_defaults_to_the_source_folder(tmp_path):
    report, _ = _import(tmp_path, legacy_folder(tmp_path, "v10", folder="Pack 42 2024"))
    assert _event(report).name == "Pack 42 2024"


def test_a_snapshot_reads_with_or_without_its_xml_bookends(tmp_path):
    text = SNAPSHOT.read_text()
    bare = text[text.index("<![CDATA[") + len("<![CDATA[") : text.rindex("]]>")]
    source = tmp_path / "bare" / "snapshot.json"
    source.parent.mkdir()
    source.write_text(bare)

    report, _ = _import(tmp_path, source)

    assert report["source"]["kind"] == "snapshot"
    assert report["counts"]["registrationinfo"]["imported"] == 82


def test_a_file_that_is_neither_sqlite_nor_a_snapshot_is_refused(tmp_path):
    source = tmp_path / "x" / "notes.txt"
    source.parent.mkdir()
    source.write_text("not a database")
    with pytest.raises(CommandError, match="neither a SQLite database nor a snapshot"):
        _import(tmp_path, source)
    assert not Event.objects.exists()


# Handed off from the #13 review: one bad value never stops the import.
def test_a_bad_raceinfo_value_is_reported_and_the_rest_imports(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[
            "UPDATE RaceInfo SET itemvalue = 'four' WHERE itemkey = 'lane_count'",
            "INSERT INTO RaceInfo(itemkey, itemvalue) VALUES('track-length', '-5')",
            "INSERT INTO RaceInfo(itemkey, itemvalue) VALUES('timer_state', '2+x++')",
            "INSERT INTO RaceInfo(itemkey, itemvalue) VALUES('n-pack-trophies', '7')",
            "INSERT INTO RaceInfo(itemkey, itemvalue) VALUES('no-such-key', 'hi')",
        ],
    )

    report, _ = _import(tmp_path, database)

    bad = {f["legacy_id"]: f["detail"] for f in _findings(report, "bad-value")}
    assert set(bad) == {"lane_count", "track-length", "timer_state"}
    assert "'four'" in bad["lane_count"]
    assert [f["legacy_id"] for f in _findings(report, "unknown-key")] == ["no-such-key"]
    settings_row = RaceSettings.for_event(_event(report))
    assert settings_row.lane_count == 0
    assert settings_row.track_length == 40
    assert settings_row.n_pack_trophies == 7
    assert report["counts"]["registrationinfo"]["imported"] == 82


# #64 C30: a key written twice. The later row wins.
def test_a_repeated_raceinfo_key_keeps_the_highest_raceinfoid(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[
            "INSERT INTO RaceInfo(raceinfoid, itemkey, itemvalue)"
            " VALUES(900, 'supergroup-label', 'Troop')",
            "INSERT INTO RaceInfo(raceinfoid, itemkey, itemvalue)"
            " VALUES(800, 'supergroup-label', 'Pack')",
        ],
    )

    report, _ = _import(tmp_path, database)

    assert RaceSettings.for_event(_event(report)).supergroup_label == "Troop"
    duplicates = _findings(report, "duplicate-key")
    assert len(duplicates) == 1 and "'Troop'" in duplicates[0]["detail"]


# #64 C11: PHP writes scoring=1 on every standings include when drop-slowest
# is set. The importer converts it once.
@pytest.mark.parametrize(
    ("rows", "scoring"),
    [
        (["('drop-slowest', '1')"], 1),
        (["('drop-slowest', '1')", "('scoring', '2')"], 2),
        (["('drop-slowest', '0')"], 0),
    ],
)
def test_drop_slowest_becomes_scoring_1_unless_scoring_is_set(tmp_path, rows, scoring):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[f"INSERT INTO RaceInfo(itemkey, itemvalue) VALUES{row}" for row in rows],
    )
    report, _ = _import(tmp_path, database)
    assert RaceSettings.for_event(_event(report)).scoring == scoring


@pytest.mark.parametrize(
    ("show", "expected"), [("'1'", "head"), ("'0'", "0"), (None, "0")]
)
def test_show_racer_photos_sets_an_unset_photos_on_now_racing(tmp_path, show, expected):
    sql = ["DELETE FROM RaceInfo WHERE itemkey = 'photos-on-now-racing'"]
    if show is not None:
        sql.append(
            "INSERT INTO RaceInfo(itemkey, itemvalue)"
            f" VALUES('show-racer-photos', {show})"
        )
    report, _ = _import(tmp_path, legacy_folder(tmp_path, "v10", sql=sql))
    assert RaceSettings.for_event(_event(report)).photos_on_now_racing == expected


# Handed off from the #13 review: legacy absolute paths become relative ones.
def test_windows_and_dotted_photo_paths_are_rebased_or_reported(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[
            "UPDATE RaceInfo SET itemvalue = 'C:\\DerbyNet\\2024\\pack\\racers'"
            " WHERE itemkey = 'photo-directory'",
            "UPDATE RegistrationInfo SET imagefile ="
            " 'c:\\derbynet\\2024\\PACK\\racers\\0-cropped\\head-101.jpg'"
            " WHERE carnumber = 101",
            "UPDATE RegistrationInfo SET imagefile = '/old/racers/../../etc/passwd'"
            " WHERE carnumber = 111",
            "UPDATE RegistrationInfo SET imagefile = 'D:\\photos\\..'"
            " WHERE carnumber = 121",
            "UPDATE RegistrationInfo SET carphoto = '/elsewhere/cars/car-101.jpg'"
            " WHERE carnumber = 101",
        ],
    )

    report, _ = _import(tmp_path, database)

    folder = event_folder(_event(report))
    rebased = _racer(report, 101)
    assert rebased.headshot == "0-cropped/head-101.jpg"
    assert _md5(folder / "racers" / "0-cropped" / "head-101.jpg") == _md5(
        FIXTURES / "v10-photos" / "racers" / "0-cropped" / "head-101.jpg"
    )
    assert _md5(folder / "racers" / "head-101.jpg") == _md5(
        FIXTURES / "v10-photos" / "racers" / "head-101.jpg"
    )
    assert rebased.car_photo == "car-101.jpg"
    assert (folder / "cars" / "car-101.jpg").is_file()
    assert _racer(report, 111).headshot == "passwd"
    assert _racer(report, 121).headshot == ""
    missing = _findings(report, "missing-photo")
    assert [f["legacy_id"] for f in missing] == [_legacy_racer(report, 111)]
    unusable = [
        f for f in _findings(report, "photo-path") if "D:\\\\photos" in f["detail"]
    ]
    assert len(unusable) == 1
    assert not (folder / "racers" / "passwd").exists()


def _legacy_racer(report, car_number):
    ids = legacy_ids(_event(report))
    return ids[("roster.racer", _racer(report, car_number).id)]


def test_photos_are_found_under_the_photos_dir_options(tmp_path):
    database = legacy_folder(tmp_path, "v10", photos=False)
    shutil.copytree(FIXTURES / "v10-photos" / "racers", tmp_path / "heads")
    shutil.copytree(FIXTURES / "v10-photos" / "cars", tmp_path / "carpix")

    report, _ = _import(
        tmp_path,
        database,
        "--photos-dir",
        str(tmp_path / "heads"),
        "--car-photos-dir",
        str(tmp_path / "carpix"),
    )

    assert _findings(report, "missing-photo") == []
    folder = event_folder(_event(report))
    assert (folder / "racers" / "0-cropped" / "head-101.jpg").is_file()
    assert (folder / "cars" / "car-101.jpg").is_file()


def test_a_photo_with_no_file_is_reported(tmp_path):
    report, _ = _import(tmp_path, legacy_folder(tmp_path, "v10", photos=False))
    missing = _findings(report, "missing-photo")
    assert len(missing) == 3
    assert _racer(report, 111).headshot == "head-111.jpg"


def _violating_source(tmp_path, folder="bad"):
    # An organic group without round 1 (classes-round1).
    return legacy_folder(
        tmp_path,
        "v10",
        folder=folder,
        sql=["INSERT INTO Classes(classid, class, sortorder) VALUES(50, 'Ghosts', 50)"],
    )


# Handed off from the #13 review: the whole import is one transaction.
def test_a_violation_rolls_back_everything_the_import_wrote(tmp_path, data_dir):
    with pytest.raises(CommandError, match="classes-round1"):
        _import(tmp_path, _violating_source(tmp_path))

    assert not Event.objects.exists()
    assert not Racer.objects.exists()
    assert not LegacyId.objects.exists()
    assert not SiteState.objects.exists()
    assert [p for p in data_dir.rglob("*") if p.is_file()] == []
    report = (tmp_path / "report.json").read_text()
    assert '"outcome": "rolled back"' in report
    assert '"classes-round1"' in report


def test_accept_violations_keeps_the_import_and_reports_them(tmp_path):
    report, out = _import(tmp_path, _violating_source(tmp_path), "--accept-violations")
    assert report["outcome"] == "committed"
    assert [row["name"] for row in report["invariants"]["classes-round1"]] == ["Ghosts"]
    assert "classes-round1: 1" in out


def test_a_failed_replace_keeps_the_old_event(tmp_path):
    first, _ = _import(tmp_path, legacy_folder(tmp_path, "v10"), "--event", "Pack")
    old = _event(first)
    files = sorted(p for p in event_folder(old).rglob("*") if p.is_file())
    assert files

    with pytest.raises(CommandError):
        _import(tmp_path, _violating_source(tmp_path), "--event", "Pack", "--replace")

    assert list(Event.objects.values_list("id", "slug")) == [(old.id, old.slug)]
    assert SiteState.get().active_event == old
    assert Racer.objects.filter(event=old).count() == 82
    assert LegacyId.objects.filter(event=old).exists()
    assert sorted(p for p in event_folder(old).rglob("*") if p.is_file()) == files


def test_replace_moves_the_active_event_to_the_new_import(tmp_path):
    first, _ = _import(tmp_path, legacy_folder(tmp_path, "v10"), "--event", "Pack")
    assert SiteState.get().active_event_id == first["event"]["id"]
    second, _ = _import(
        tmp_path, legacy_folder(tmp_path, "v9"), "--event", "Pack", "--replace"
    )
    assert SiteState.get().active_event_id == second["event"]["id"]


def test_a_racechart_row_with_no_racer_is_dropped_and_reported(tmp_path):
    report, _ = _import(tmp_path, legacy_folder(tmp_path, "v1"))
    dropped = [f for f in _findings(report, "dropped-row") if f["table"] == "racechart"]
    assert len(dropped) == 1 and "racerid is empty" in dropped[0]["detail"]
    counts = report["counts"]["racechart"]
    assert counts["imported"] == counts["source"] - 1


def test_v1_mixed_case_columns_read_as_lower_case(tmp_path):
    v1, _ = _import(tmp_path / "a", legacy_folder(tmp_path / "a", "v1"), "--event", "a")
    v10, _ = _import(
        tmp_path / "b", legacy_folder(tmp_path / "b", "v10"), "--event", "b"
    )

    def names(report):
        return sorted(
            Racer.objects.filter(event=_event(report)).values_list(
                "car_number", "first_name", "last_name"
            )
        )

    assert names(v1) == names(v10)
    assert RaceSettings.for_event(_event(v1)).supergroup_label == "x" * 50


def test_check_in_times_before_schema_10_come_from_events(tmp_path):
    database = legacy_folder(tmp_path, "v9")
    events = source_rows(
        database, "SELECT racerid, tstamp FROM Events WHERE action = 1 ORDER BY seq"
    )
    assert events
    report, _ = _import(tmp_path, database)
    ids = legacy_ids(_event(report))
    zone = ZoneInfo(settings.TIME_ZONE)
    seen = {}
    for racer in Racer.objects.filter(event=_event(report)):
        racerid = ids[("roster.racer", racer.id)]
        latest = [row["tstamp"] for row in events if row["racerid"] == racerid]
        if not latest:
            assert racer.checked_in_at is None
            continue
        wall = datetime.fromisoformat(latest[-1]).replace(tzinfo=zone)
        # Racers checked in within one second keep their order by microseconds.
        assert racer.checked_in_at.replace(microsecond=0) == wall
        seen.setdefault(wall, []).append(racer.checked_in_at)
    assert any(len(times) > 1 for times in seen.values())
    assert all(len(set(times)) == len(times) for times in seen.values())


def test_subgroups_sharing_a_name_become_renamed_partitions(tmp_path):
    # PHP's INSERT ... SELECT FROM Ranks fails on Partitions.name UNIQUE and
    # leaves no partitions at all. The importer renames and reports instead.
    database = legacy_folder(
        tmp_path,
        "v7",
        sql=["UPDATE Ranks SET rank = 'Wolves' WHERE rankid IN (1, 2)"],
    )
    report, _ = _import(tmp_path, database)
    names = sorted(
        Partition.objects.filter(event=_event(report)).values_list("name", flat=True)
    )
    assert names[-2:] == ["Wolves", "Wolves (2)"]
    renamed = [
        f for f in _findings(report, "changed-value") if f["table"] == "partitions"
    ]
    assert [f["legacy_id"] for f in renamed] == [2]
    assert report["invariants"] == {}


def test_constituent_csv_and_rows_are_merged_and_differences_reported(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=["DELETE FROM ConstituentClasses WHERE dependent = 8 AND antecedent = 2"],
    )
    report, _ = _import(tmp_path, database)

    younger = Group.objects.get(event=_event(report), name="Younger")
    assert sorted(younger.constituents.values_list("name", flat=True)) == [
        "Lions & Tigers",
        "White's Wolves",
    ]
    mismatches = _findings(report, "constituent-mismatch")
    assert [f["legacy_id"] for f in mismatches] == [8]
    assert GroupConstituent.objects.filter(event=_event(report)).count() == 4


def test_kiosk_pages_split_into_page_params_and_reload(tmp_path):
    report, _ = _import(tmp_path, legacy_folder(tmp_path, "v10"))
    kiosks = {k.address: k for k in Kiosk.objects.filter(event=_event(report))}
    assert kiosks["fixture-kiosk-1"].page == "kiosks/please-check-in.kiosk"
    assert kiosks["fixture-kiosk-1"].params == {"classids": [1, 2]}
    assert kiosks["fixture-kiosk-1"].name == "Main"
    assert kiosks["fixture-kiosk-3"].page == "kiosks/standings.kiosk"
    assert kiosks["fixture-kiosk-3"].reload_requested
    assert not kiosks["fixture-kiosk-1"].reload_requested
    local = _findings(report, "local-kiosk-page")
    assert {(f["table"], f["legacy_id"]) for f in local} == {
        ("kiosks", "fixture-kiosk-2"),
        ("scenekiosk", 14),
    }


def test_action_history_is_copied_verbatim_and_bad_json_is_kept(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[
            "INSERT INTO ActionHistory(historyid, received, request)"
            " VALUES(9000, '2024-02-10 13:05:09', '{not json')"
        ],
    )
    source = source_rows(database, "SELECT historyid, request FROM ActionHistory")
    report, _ = _import(tmp_path, database)

    ids = legacy_ids(_event(report))
    logs = {
        ids[("core.actionlog", log.id)]: log
        for log in ActionLog.objects.filter(event=_event(report))
    }
    assert len(logs) == len(source)
    for row in source:
        if row["historyid"] == 9000:
            assert logs[9000].request == {"unparsed": "{not json"}
        else:
            assert logs[row["historyid"]].request == json.loads(row["request"])
    assert [f["legacy_id"] for f in _findings(report, "unparsed-action")] == [9000]
    assert logs[9000].received == datetime(
        2024, 2, 10, 13, 5, 9, tzinfo=ZoneInfo(settings.TIME_ZONE)
    )


def test_an_award_of_a_deleted_group_becomes_pack_wide(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[
            "UPDATE Awards SET classid = 99, rankid = 98 WHERE awardname = 'Best Bear'",
            "UPDATE Awards SET racerid = 777 WHERE awardname = 'Best in Show'",
        ],
    )
    report, _ = _import(tmp_path, database)

    bear = Award.objects.get(event=_event(report), name="Best Bear")
    assert bear.group is None and bear.subgroup is None
    assert len(_findings(report, "unscoped-award")) == 1
    assert Award.objects.get(event=_event(report), name="Best in Show").racer is None
    lionic = Award.objects.get(event=_event(report), name="Most Lionic")
    assert lionic.group.name == "Lions & Tigers"


def test_references_in_raceinfo_point_at_the_imported_rows(tmp_path):
    report, _ = _import(tmp_path, legacy_folder(tmp_path, "v10"))
    state = RaceState.for_event(_event(report))
    ids = legacy_ids(_event(report))
    assert ids[("racing.round", state.current_round_id)] == 5
    assert ids[("roster.group", state.current_group_id)] == 5
    assert state.last_heat == {
        "round_id": state.current_round_id,
        "heat": 16,
        "results": None,
    }


def test_the_report_lists_counts_findings_and_invariants(tmp_path):
    report, out = _import(tmp_path, legacy_folder(tmp_path, "v10"))
    assert report["source"]["version"] == 10
    assert report["counts"]["racechart"] == {"source": 328, "imported": 328}
    assert report["invariants"] == {}
    assert "events" in report["not_imported"]
    # Three racer photos, one of them cropped, so four files.
    assert report["photos"] == {
        "folder": str(event_folder(_event(report))),
        "paths": 3,
        "files_copied": 4,
    }
    assert "racechart: 328 of 328" in out


def _new_id(event, model, legacy_id):
    return LegacyId.objects.get(event=event, model=model, legacy_id=legacy_id).new_id


def test_long_names_that_collide_once_cut_are_renamed(tmp_path):
    long = "A" * 75
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[
            f"UPDATE Classes SET class = '{long}x' WHERE classid = 1",
            f"UPDATE Classes SET class = '{long}y' WHERE classid = 2",
        ],
    )
    report, _ = _import(tmp_path, database)
    names = {
        group.name
        for group in Group.objects.filter(event=_event(report))
        if group.name.startswith("AAA")
    }
    assert names == {long, "A" * 71 + " (2)"}
    assert len(_findings(report, "changed-value")) == 2


def test_a_snapshot_basename_naming_the_cropped_folder_is_dropped(tmp_path):
    text = SNAPSHOT.read_text()
    photo = next(p for p in read_source(SNAPSHOT).photos if p.original is None)
    source = tmp_path / "crafted" / "snapshot.xml"
    source.parent.mkdir()
    source.write_text(
        text.replace(f'"basename": "{photo.basename}"', '"basename": "0-cropped"', 1)
    )
    report, _ = _import(tmp_path, source)
    racer = Racer.objects.get(pk=_new_id(_event(report), "roster.racer", photo.racerid))
    column = "headshot" if photo.column == "imagefile" else "car_photo"
    assert getattr(racer, column) == ""
    assert any("'0-cropped'" in f["detail"] for f in _findings(report, "photo-path"))


def test_a_dropped_racer_publishes_no_photo(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=["UPDATE RegistrationInfo SET carnumber = 1e20 WHERE carnumber = 101"],
    )
    report, _ = _import(tmp_path, database)
    folder = event_folder(_event(report))
    assert not (folder / "racers" / "head-101.jpg").exists()
    assert not (folder / "cars" / "car-101.jpg").exists()
    assert (folder / "racers" / "head-111.jpg").is_file()
    assert report["photos"]["files_copied"] == 1


def test_a_photo_write_failure_rolls_back_and_keeps_the_old_photos(
    tmp_path, monkeypatch
):
    first, _ = _import(tmp_path, legacy_folder(tmp_path, "v10"), "--event", "Pack")
    old = _event(first)
    folder = event_folder(old)
    before = {p: _md5(p) for p in folder.rglob("*") if p.is_file()}
    assert before
    real_copy = photos.shutil.copyfileobj
    copies = []

    def failing_copy(source, target):
        copies.append(source)
        if len(copies) == 2:
            raise OSError(28, "No space left on device")
        return real_copy(source, target)

    monkeypatch.setattr(photos.shutil, "copyfileobj", failing_copy)
    with pytest.raises(CommandError, match="No space left"):
        _import(tmp_path, legacy_folder(tmp_path, "v9"), "--event", "Pack", "--replace")
    monkeypatch.undo()

    assert list(Event.objects.values_list("id", flat=True)) == [old.id]
    assert [p.name for p in folder.parent.iterdir()] == [folder.name]
    assert {p: _md5(p) for p in folder.rglob("*") if p.is_file()} == before


def test_nul_characters_are_dropped_for_postgres(tmp_path):
    database = legacy_folder(
        tmp_path,
        "v10",
        sql=[
            "UPDATE RegistrationInfo SET carname = 'Zoom' || char(0) || 'er'"
            " WHERE carnumber = 101",
            "INSERT INTO ActionHistory(historyid, received, request)"
            " VALUES(9001, '2024-02-10 13:05:09', '{\"action\":\"x\\u0000y\"}')",
        ],
    )
    report, _ = _import(tmp_path, database)
    assert _racer(report, 101).car_name == "Zoomer"
    log = ActionLog.objects.get(pk=_new_id(_event(report), "core.actionlog", 9001))
    assert log.request == {"action": "xy"}


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_a_report_at_the_source_or_its_side_files_is_refused(tmp_path, suffix):
    database = legacy_folder(tmp_path, "v10")
    before = _md5(database)
    report = database.parent / ".." / database.parent.name / (database.name + suffix)

    with pytest.raises(CommandError, match="--report"):
        call_command("import_derbynet", str(database), "--report", str(report))

    assert _md5(database) == before
    assert sorted(p.name for p in database.parent.iterdir()) == [
        "cars",
        "derbynet.sqlite3",
        "racers",
    ]
    assert not Event.objects.exists()


def test_an_existing_report_is_overwritten_only_with_force_report(tmp_path):
    database = legacy_folder(tmp_path, "v10")
    report = tmp_path / "report.json"
    report.write_text("keep me")

    with pytest.raises(CommandError, match="--force-report"):
        call_command("import_derbynet", str(database), "--report", str(report))
    assert report.read_text() == "keep me"
    assert not Event.objects.exists()

    call_command(
        "import_derbynet",
        str(database),
        "--report",
        str(report),
        "--force-report",
        stdout=io.StringIO(),
    )
    assert json.loads(report.read_text())["outcome"] == "committed"


def _folder_state(folder):
    return {p.name: _md5(p) for p in folder.iterdir() if p.is_file()}


def _wal_source(tmp_path):
    """A v10 folder whose last write, car 101's name, lives only in its -wal."""
    database = legacy_folder(tmp_path, "v10", photos=False)
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute(
        "UPDATE RegistrationInfo SET carname = 'From WAL' WHERE carnumber = 101"
    )
    writer.commit()
    # As a crashed PHP server leaves it: the -wal with no checkpoint, no -shm.
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    shutil.copyfile(database, frozen / database.name)
    shutil.copyfile(f"{database}-wal", frozen / f"{database.name}-wal")
    writer.close()
    return frozen / database.name


@pytest.fixture
def read_only(tmp_path):
    folders = []

    def make(folder):
        folder.chmod(0o555)
        folders.append(folder)
        return folder

    yield make
    for folder in folders:
        folder.chmod(0o755)


def test_a_wal_source_imports_its_wal_and_adds_no_side_files(tmp_path):
    database = _wal_source(tmp_path)
    before = _folder_state(database.parent)

    report, _ = _import(tmp_path, database)

    assert _racer(report, 101).car_name == "From WAL"
    assert _folder_state(database.parent) == before


@pytest.mark.parametrize("wal", [False, True])
def test_a_source_in_a_read_only_folder_imports(tmp_path, read_only, wal):
    database = _wal_source(tmp_path) if wal else legacy_folder(tmp_path, "v10")
    before = _folder_state(database.parent)
    read_only(database.parent)

    report, _ = _import(tmp_path, database)

    assert report["counts"]["registrationinfo"]["imported"] == 82
    assert _folder_state(database.parent) == before


def test_a_hot_journal_is_refused_with_a_clear_message(tmp_path):
    database = legacy_folder(tmp_path, "v10", photos=False)
    writer = sqlite3.connect(database, isolation_level=None)
    writer.execute("PRAGMA cache_size=1")
    writer.execute("BEGIN")
    # Big enough to spill the one-page cache, so the journal is hot.
    writer.execute("UPDATE RegistrationInfo SET carname = hex(randomblob(500))")
    crashed = tmp_path / "crashed"
    crashed.mkdir()
    for name in (database.name, f"{database.name}-journal"):
        shutil.copyfile(database.parent / name, crashed / name)
    writer.execute("ROLLBACK")
    writer.close()
    before = _folder_state(crashed)

    with pytest.raises(CommandError, match="unfinished write.*-journal"):
        _import(tmp_path, crashed / database.name)

    assert _folder_state(crashed) == before
    assert not Event.objects.exists()


def test_a_snapshot_strips_nul_characters_but_keeps_an_escaped_backslash(tmp_path):
    text = SNAPSHOT.read_text()
    row = '"carnumber": 101,\n     "carname": "",\n     "lastname": "Asher",'
    assert row in text
    crafted = row.replace('"carname": ""', r'"carname": "C:\\u0000\\x"').replace(
        '"Asher"', r'"Ash\u0000er"'
    )
    source = tmp_path / "crafted" / "snapshot.xml"
    source.parent.mkdir()
    source.write_text(text.replace(row, crafted))

    report, _ = _import(tmp_path, source)

    racer = _racer(report, 101)
    assert racer.car_name == "C:\\u0000\\x"
    assert racer.last_name == "Asher"


def _snapshot_with_headshots(tmp_path, headshots):
    """The v10 snapshot with only `headshots`: racerid -> (basename, original,
    displayed). Each of those racers gets an imagefile path."""
    text = SNAPSHOT.read_text()
    entries = json.loads(text[text.index("<![CDATA[") + 9 : text.rindex("]]>")])
    entries = [entry for entry in entries if "racerid" not in entry]
    rows = next(e for e in entries if e["table"] == "RegistrationInfo")["rows"]
    for row in rows:
        row["imagefile"] = row["carphoto"] = None
        if row["racerid"] in headshots:
            row["imagefile"] = f"/old/racers/{headshots[row['racerid']][0]}"
    for racerid, (name, original, displayed) in headshots.items():
        photo = {"basename": name, "displayed": displayed.hex()}
        if original is not None:
            photo["original"] = original.hex()
        entries.append({"racerid": racerid, "imagefile": photo})
    source = tmp_path / "crafted" / "snapshot.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps(entries))
    return source


def _headshot(report, racerid):
    racer = Racer.objects.get(pk=_new_id(_event(report), "roster.racer", racerid))
    return racer.headshot


def test_snapshot_photos_sharing_a_name_each_keep_their_own_file(tmp_path):
    source = _snapshot_with_headshots(
        tmp_path,
        {
            1: ("same.jpg", b"original 1", b"cropped 1"),
            11: ("same.jpg", None, b"photo 11"),
            21: ("same.jpg", None, b"photo 21"),
            31: ("same.jpg", None, b"photo 11"),
        },
    )

    report, _ = _import(tmp_path, source)

    racers = event_folder(_event(report)) / "racers"
    shots = {racerid: _headshot(report, racerid) for racerid in (1, 11, 21, 31)}
    assert shots == {
        1: "0-cropped/same.jpg",
        11: "same-2.jpg",
        21: "same-3.jpg",
        31: "same-2.jpg",
    }
    assert (racers / "same.jpg").read_bytes() == b"original 1"
    assert (racers / "0-cropped" / "same.jpg").read_bytes() == b"cropped 1"
    assert (racers / "same-2.jpg").read_bytes() == b"photo 11"
    assert (racers / "same-3.jpg").read_bytes() == b"photo 21"
    renamed = [f for f in _findings(report, "photo-path") if "same-" in f["detail"]]
    assert sorted(f["legacy_id"] for f in renamed) == [11, 21, 31]


def test_snapshot_names_that_sanitize_alike_each_keep_their_own_file(tmp_path):
    source = _snapshot_with_headshots(
        tmp_path, {1: ("é.jpg", None, b"e acute"), 11: ("è.jpg", None, b"e grave")}
    )

    report, _ = _import(tmp_path, source)

    racers = event_folder(_event(report)) / "racers"
    assert (_headshot(report, 1), _headshot(report, 11)) == ("_.jpg", "_-2.jpg")
    assert (racers / "_.jpg").read_bytes() == b"e acute"
    assert (racers / "_-2.jpg").read_bytes() == b"e grave"


def test_a_snapshot_name_too_long_for_the_disk_is_shortened(tmp_path):
    long = "a" * 300 + ".jpg"
    source = _snapshot_with_headshots(
        tmp_path,
        {
            1: (long, b"original 1", b"cropped 1"),
            11: (long, None, b"photo 11"),
        },
    )

    report, _ = _import(tmp_path, source)

    racers = event_folder(_event(report)) / "racers"
    first, second = _headshot(report, 1), _headshot(report, 11)
    assert first.startswith("0-cropped/aaa") and first.endswith(".jpg")
    assert len(first) <= 255
    name = first.removeprefix("0-cropped/")
    assert (racers / name).read_bytes() == b"original 1"
    assert (racers / first).read_bytes() == b"cropped 1"
    assert second.endswith("-2.jpg") and len(second) <= 255
    assert (racers / second).read_bytes() == b"photo 11"
    shortened = [
        f for f in _findings(report, "photo-path") if "too long" in f["detail"]
    ]
    assert sorted(f["legacy_id"] for f in shortened) == [1, 11]


def test_symbolic_links_in_the_photo_folders_are_skipped_and_reported(
    tmp_path, data_dir
):
    database = legacy_folder(tmp_path, "v10")
    folder = database.parent
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"secret")
    (folder / "racers" / "head-111.jpg").unlink()
    (folder / "racers" / "head-111.jpg").symlink_to(secret)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "head-101.jpg").symlink_to(secret)
    shutil.rmtree(folder / "racers" / "0-cropped")
    (folder / "racers" / "0-cropped").symlink_to(elsewhere)
    car_dir = tmp_path / "carpix"
    car_dir.mkdir()
    (car_dir / "car-101.jpg").symlink_to(secret)

    report, _ = _import(tmp_path, database, "--car-photos-dir", str(car_dir))

    assert not any(
        p.read_bytes() == b"secret" for p in data_dir.rglob("*") if p.is_file()
    )
    links = sorted(f["detail"] for f in _findings(report, "symlink"))
    assert len(links) == 3
    assert any(str(folder / "racers" / "head-111.jpg") in d for d in links)
    assert any(str(folder / "racers" / "0-cropped") in d for d in links)
    assert any(str(car_dir / "car-101.jpg") in d for d in links)
    assert _racer(report, 101).headshot == "head-101.jpg"
    assert _racer(report, 101).car_photo == "car-101.jpg"
    folder = event_folder(_event(report))
    assert _md5(folder / "cars" / "car-101.jpg") == _md5(
        FIXTURES / "v10-photos" / "cars" / "car-101.jpg"
    )
    assert [f["legacy_id"] for f in _findings(report, "missing-photo")] == [
        _legacy_racer(report, 111)
    ]


def _checkins(tmp_path, events, *args):
    """Import v9 with `events` as racers 1 and 11's only EVENT_CHECKIN rows:
    `(seq, tstamp, racerid)`. Returns the report and both check-in times."""
    database = legacy_folder(
        tmp_path,
        "v9",
        sql=[
            "DELETE FROM Events WHERE racerid IN (1, 11)",
            *(
                f"INSERT INTO Events(seq, tstamp, action, racerid)"
                f" VALUES({seq}, '{tstamp}', 1, {racerid})"
                for seq, tstamp, racerid in events
            ),
        ],
    )
    report, _ = _import(tmp_path, database, *args)
    event = _event(report)
    times = {
        racerid: Racer.objects.get(
            pk=_new_id(event, "roster.racer", racerid)
        ).checked_in_at
        for racerid in (1, 11)
    }
    return report, times


def test_the_latest_check_in_of_a_racer_wins(tmp_path):
    zone = ZoneInfo(settings.TIME_ZONE)
    _, times = _checkins(
        tmp_path,
        [
            (900, "2030-01-01 09:00:00", 1),
            (902, "2030-01-01 10:00:00", 1),
            (901, "2030-01-01 09:30:00", 11),
        ],
    )
    assert times[1] == datetime(2030, 1, 1, 10, tzinfo=zone)
    assert times[11] == datetime(2030, 1, 1, 9, 30, tzinfo=zone)


def test_check_ins_keep_php_seq_order_when_the_clock_went_back(tmp_path):
    # PHP lists check-ins by MAX(seq): racer 1 checked in after racer 11,
    # though the clock had gone back.
    zone = ZoneInfo(settings.TIME_ZONE)
    report, times = _checkins(
        tmp_path,
        [(901, "2030-01-01 10:00:00", 11), (902, "2030-01-01 09:00:00", 1)],
    )
    assert times[11] == datetime(2030, 1, 1, 10, tzinfo=zone)
    assert times[1] == datetime(2030, 1, 1, 10, 0, 0, 1, tzinfo=zone)
    moved = [f for f in _findings(report, "changed-value") if f["table"] == "events"]
    assert [f["legacy_id"] for f in moved] == [902]


def test_legacy_times_read_in_the_time_zone_option(tmp_path):
    report, times = _checkins(
        tmp_path,
        [(901, "2030-01-01 10:00:00", 11), (902, "2030-01-01 11:00:00", 1)],
        "--time-zone",
        "America/New_York",
    )
    assert report["source"]["time_zone"] == "America/New_York"
    assert times[11] == datetime(2030, 1, 1, 15, tzinfo=UTC)


def test_the_time_zone_defaults_to_the_server_one(tmp_path, settings):
    settings.TIME_ZONE = "Europe/Paris"
    report, times = _checkins(tmp_path, [(901, "2030-01-01 10:00:00", 11)])
    assert report["source"]["time_zone"] == "Europe/Paris"
    assert times[11] == datetime(2030, 1, 1, 9, tzinfo=UTC)


def test_an_unknown_time_zone_is_refused(tmp_path):
    with pytest.raises(CommandError, match="Mars/Olympus"):
        _import(tmp_path, legacy_folder(tmp_path, "v9"), "--time-zone", "Mars/Olympus")
    assert not Event.objects.exists()


def test_a_check_in_in_the_autumn_fold_reads_as_the_first_and_is_reported(tmp_path):
    report, times = _checkins(
        tmp_path,
        [(901, "2030-11-03 01:30:00", 11)],
        "--time-zone",
        "America/New_York",
    )
    # 01:30 EDT, the first of the two.
    assert times[11] == datetime(2030, 11, 3, 5, 30, tzinfo=UTC)
    ambiguous = _findings(report, "ambiguous-time")
    assert [(f["table"], f["legacy_id"]) for f in ambiguous] == [("events", 901)]
