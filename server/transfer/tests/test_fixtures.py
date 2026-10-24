"""The importer against the fixtures that build.sh made with the PHP server."""

import hashlib
import json
import posixpath
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core.management import CommandError

from awards.models import Award
from core.invariants import check_invariants
from core.legacy_keys import Dropped, Mapped, Ref, encode, entry_for
from core.models import Event
from racing.models import HeatLane, RosterEntry, Round
from roster.models import Group, Partition, Racer, Subgroup
from transfer.legacy.source import read_source
from transfer.models import LegacyId
from transfer.tests.legacy import (
    FIXTURES,
    SNAPSHOT,
    event_folder,
    legacy_dump,
    legacy_folder,
    legacy_ids,
    run_import,
    source_rows,
)

pytestmark = pytest.mark.django_db

SQLITE_FIXTURES = ["v10", "v9", "v7", "v7-upgraded", "v5", "v1"]

# PHP's database.check flags the NULL sort orders of a schema-1 database. The
# importer fills each one in, and reports it.
SORT_ORDER_TAGS = {
    "classes-sortorder",
    "ranks-sortorder",
    "partition-sortorder",
    "classes-ambiguous-sort",
    "ranks-ambiguous-sort",
}


def _import(tmp_path, source, *args):
    return run_import(source, *args, report_dir=tmp_path)


def _event(report):
    return Event.objects.get(pk=report["event"]["id"])


@pytest.mark.parametrize("name", SQLITE_FIXTURES + ["v10.snapshot"])
def test_every_fixture_imports_with_no_invariant_violation(tmp_path, name):
    if name == "v10.snapshot":
        source, check = SNAPSHOT, "v10"
    else:
        source, check = legacy_folder(tmp_path, name), name.removesuffix("-upgraded")

    report, out = _import(tmp_path, source)

    assert report["outcome"] == "committed"
    assert report["invariants"] == {}
    assert check_invariants(_event(report)) == {}
    assert "check_invariants: no violations" in out

    php = json.loads((FIXTURES / "checks" / f"{check}.json").read_text())
    php_tags = set(php) - {"outcome", "details"}
    if check == "v1":
        assert php_tags and php_tags <= SORT_ORDER_TAGS
        filled = {
            (f["table"], f["legacy_id"])
            for f in report["findings"]
            if f["kind"] == "changed-value" and f["detail"].startswith("sortorder")
        }
        for tag in php_tags - {"classes-ambiguous-sort", "ranks-ambiguous-sort"}:
            table = {"classes-sortorder": "classes", "ranks-sortorder": "ranks"}.get(
                tag, "partitions"
            )
            id_column = {"classes": "classid", "ranks": "rankid"}.get(
                table, "partitionid"
            )
            for row in php[tag]:
                assert (table, row[id_column]) in filled
    else:
        assert php["outcome"]["summary"] == "success"
        assert php_tags == set()


def _completed_text(moment):
    if moment is None:
        return None
    return moment.astimezone(ZoneInfo(settings.TIME_ZONE)).strftime("%Y-%m-%d %H:%M:%S")


def test_v10_rows_match_the_source_through_legacy_ids(tmp_path):
    database = legacy_folder(tmp_path, "v10")
    report, _ = _import(tmp_path, database)
    event = _event(report)
    ids = legacy_ids(event)

    def legacy(model, new_id):
        return None if new_id is None else ids[(model._meta.label_lower, new_id)]

    def compare(sql, expected_of, model, actual_of):
        expected = sorted(expected_of(row) for row in source_rows(database, sql))
        actual = sorted(actual_of(obj) for obj in model.objects.filter(event=event))
        assert actual == expected, model._meta.label
        assert expected

    def none_if_negative(value):
        return None if value is None or value < 0 else value

    raceinfo = {
        row["itemkey"]: row["itemvalue"]
        for row in source_rows(database, "SELECT * FROM RaceInfo ORDER BY raceinfoid")
    }

    def relative(path, key):
        return posixpath.relpath(path, raceinfo[key]) if path else ""

    compare(
        "SELECT * FROM Classes",
        lambda r: (
            r["classid"],
            r["class"],
            r["sortorder"],
            bool(r["durable"]),
            none_if_negative(r["ntrophies"]),
        ),
        Group,
        lambda g: (legacy(Group, g.id), g.name, g.sort_order, g.durable, g.n_trophies),
    )
    compare(
        "SELECT * FROM Ranks",
        lambda r: (
            r["rankid"],
            r["rank"],
            r["classid"],
            r["sortorder"],
            none_if_negative(r["ntrophies"]),
        ),
        Subgroup,
        lambda s: (
            legacy(Subgroup, s.id),
            s.name,
            legacy(Group, s.group_id),
            s.sort_order,
            s.n_trophies,
        ),
    )
    compare(
        "SELECT * FROM Partitions",
        lambda r: (r["partitionid"], r["name"], r["rankid"], r["sortorder"]),
        Partition,
        lambda p: (
            legacy(Partition, p.id),
            p.name,
            legacy(Subgroup, p.subgroup_id),
            p.sort_order,
        ),
    )
    compare(
        "SELECT * FROM RegistrationInfo",
        lambda r: (
            r["racerid"],
            r["carnumber"],
            r["carname"] or "",
            r["firstname"],
            r["lastname"],
            r["classid"],
            r["rankid"],
            r["partitionid"],
            bool(r["passedinspection"]),
            r["checkin_time"],
            relative(r["imagefile"], "photo-directory"),
            relative(r["carphoto"], "car-photo-directory"),
            r["note"] or "",
            bool(r["exclude"]),
        ),
        Racer,
        lambda r: (
            legacy(Racer, r.id),
            r.car_number,
            r.car_name,
            r.first_name,
            r.last_name,
            legacy(Group, r.group_id),
            legacy(Subgroup, r.subgroup_id),
            legacy(Partition, r.partition_id),
            r.passed_inspection,
            None
            if r.checked_in_at is None
            else round(r.checked_in_at.timestamp() * 1000),
            r.headshot,
            r.car_photo,
            r.note,
            r.exclude,
        ),
    )
    compare(
        "SELECT * FROM Rounds",
        lambda r: (r["roundid"], r["round"], r["classid"]),
        Round,
        lambda r: (legacy(Round, r.id), r.number, legacy(Group, r.group_id)),
    )
    compare(
        "SELECT * FROM Roster",
        lambda r: (r["rosterid"], r["roundid"], r["racerid"]),
        RosterEntry,
        lambda e: (
            legacy(RosterEntry, e.id),
            legacy(Round, e.round_id),
            legacy(Racer, e.racer_id),
        ),
    )
    compare(
        "SELECT * FROM RaceChart",
        lambda r: (
            r["resultid"],
            r["roundid"],
            r["heat"],
            r["lane"],
            r["racerid"],
            r["finishtime"],
            r["finishplace"],
            r["completed"] or None,
            r["masterheat"] or 0,
        ),
        HeatLane,
        lambda h: (
            legacy(HeatLane, h.id),
            legacy(Round, h.round_id),
            h.heat,
            h.lane,
            legacy(Racer, h.racer_id),
            h.finish_time,
            h.finish_place,
            _completed_text(h.completed),
            h.master_heat,
        ),
    )
    compare(
        "SELECT * FROM Awards",
        lambda r: (
            r["awardid"],
            r["awardname"],
            r["awardtypeid"],
            r["classid"] or None,
            r["rankid"] or None,
            r["racerid"] or None,
            r["sort"],
        ),
        Award,
        lambda a: (
            legacy(Award, a.id),
            a.name,
            a.award_type_id,
            legacy(Group, a.group_id),
            legacy(Subgroup, a.subgroup_id),
            legacy(Racer, a.racer_id),
            a.sort,
        ),
    )

    # Settings and state: each RaceInfo key encodes back to its legacy text.
    from core.models import RaceSettings, RaceState
    from kiosks.models import ReplayStatus
    from racing.models import TimerStatus

    rows = {
        "core.RaceSettings": RaceSettings.for_event(event),
        "core.RaceState": RaceState.for_event(event),
        "racing.TimerStatus": TimerStatus.for_event(event),
        "kiosks.ReplayStatus": ReplayStatus.for_event(event),
    }
    back = {
        label: {new: old for (m, new), old in ids.items() if m == label}
        for label in ("roster.group", "racing.round", "kiosks.scene")
    }
    packed_refs = {
        "current_round": "racing.round",
        "staged_round": "racing.round",
        "full_field_group": "roster.group",
    }
    folders = {
        "photo-directory",
        "car-photo-directory",
        "video-directory",
        "logs-directory",
    }
    compared = 0
    for key, raw in raceinfo.items():
        entry = entry_for(key)
        if isinstance(entry, Dropped) or key in folders:
            continue
        assert isinstance(entry, Mapped)
        row = rows[entry.model]
        fields = {}
        for name in entry.fields:
            field = row._meta.get_field(name)
            value = getattr(row, field.attname)
            target = (
                entry.codec.model.lower()
                if isinstance(entry.codec, Ref)
                else packed_refs.get(name)
            )
            if target and value is not None:
                value = back[target][value]
            if name == "last_heat" and value is not None:
                value = {**value, "round_id": back["racing.round"][value["round_id"]]}
            fields[name] = value
        value = fields[entry.fields[0]] if len(entry.fields) == 1 else fields
        assert encode(key, value) == raw, key
        compared += 1
    assert compared >= 15


def test_old_v7_and_the_php_upgraded_v7_give_identical_dumps(tmp_path):
    report, _ = _import(
        tmp_path / "a", legacy_folder(tmp_path / "a", "v7"), "--event", "a"
    )
    upgraded, _ = _import(
        tmp_path / "b", legacy_folder(tmp_path / "b", "v7-upgraded"), "--event", "b"
    )
    # The partition structure is logged when the database is upgraded.
    omit = {"core.actionlog.received"}
    old_dump = legacy_dump(_event(report), omit=omit)
    assert "Default" in [row["name"] for row in old_dump["roster.partition"]]
    assert old_dump["core.actionlog"]
    assert old_dump == legacy_dump(_event(upgraded), omit=omit)


# Fields and tables that snapshot.get leaves out (#14).
SNAPSHOT_OMITS = {
    "roster.group.n_trophies",
    "roster.subgroup.n_trophies",
    "roster.racer.note",
    "roster.racer.checked_in_at",
}
SNAPSHOT_OMITTED_TABLES = {"kiosks.kiosk", "core.actionlog"}


def _md5(data):
    return hashlib.md5(data).hexdigest()


def test_the_v10_snapshot_imports_as_the_v10_database_does(tmp_path):
    sqlite, _ = _import(
        tmp_path / "a", legacy_folder(tmp_path / "a", "v10"), "--event", "a"
    )
    snapshot, _ = _import(tmp_path / "b", SNAPSHOT, "--event", "b")

    from_sqlite = legacy_dump(_event(sqlite), omit=SNAPSHOT_OMITS)
    from_snapshot = legacy_dump(_event(snapshot), omit=SNAPSHOT_OMITS)
    for label in SNAPSHOT_OMITTED_TABLES:
        assert from_sqlite.pop(label)
        assert from_snapshot.pop(label) == []
    assert from_sqlite == from_snapshot

    photos = read_source(SNAPSHOT).photos
    assert {photo.column for photo in photos} == {"imagefile", "carphoto"}
    assert any(photo.original for photo in photos)
    folder = event_folder(_event(snapshot))
    sqlite_folder = event_folder(_event(sqlite))
    ids = legacy_ids(_event(snapshot))
    for photo in photos:
        racer = Racer.objects.get(pk=_new_id(ids, "roster.racer", photo.racerid))
        repo, path = (
            ("racers", racer.headshot)
            if photo.column == "imagefile"
            else ("cars", racer.car_photo)
        )
        shown = (folder / repo / path).read_bytes()
        assert _md5(shown) == _md5(photo.displayed)
        assert _md5((sqlite_folder / repo / path).read_bytes()) == _md5(photo.displayed)
        if photo.original is not None:
            assert path == f"0-cropped/{photo.basename}"
            original = (folder / repo / photo.basename).read_bytes()
            assert _md5(original) == _md5(photo.original)


def _new_id(ids, model, legacy_id):
    return next(new for (m, new), old in ids.items() if m == model and old == legacy_id)


def test_a_snapshot_basename_cannot_leave_the_photo_repository(tmp_path, data_dir):
    text = SNAPSHOT.read_text()
    photo = next(p for p in read_source(SNAPSHOT).photos if p.original is None)
    hostile = text.replace(
        f'"basename": "{photo.basename}"', '"basename": "../../etc/x.jpg"', 1
    )
    assert hostile != text
    source = tmp_path / "hostile" / "snapshot.xml"
    source.parent.mkdir()
    source.write_text(hostile)

    report, _ = _import(tmp_path, source)

    repo = "racers" if photo.column == "imagefile" else "cars"
    folder = event_folder(_event(report)) / repo
    assert (folder / "x.jpg").read_bytes() == photo.displayed
    written = [p for p in data_dir.rglob("*") if p.is_file()]
    assert written
    assert all(folder.parent in p.parents for p in written)
    assert not (tmp_path / "etc").exists()
    assert not (data_dir / "etc").exists()
    assert any(
        f["kind"] == "photo-path" and "../../etc/x.jpg" in f["detail"]
        for f in report["findings"]
    )


def test_importing_an_event_name_twice_fails_and_replace_reimports(tmp_path):
    database = legacy_folder(tmp_path, "v10")
    first, _ = _import(tmp_path, database, "--event", "Pack 1")
    before = legacy_dump(_event(first))

    with pytest.raises(CommandError, match="--replace"):
        _import(tmp_path, database, "--event", "Pack 1")
    assert Event.objects.filter(name="Pack 1").count() == 1
    assert legacy_dump(_event(first)) == before

    second, _ = _import(tmp_path, database, "--event", "Pack 1", "--replace")
    assert Event.objects.filter(name="Pack 1").count() == 1
    assert not Event.objects.filter(pk=first["event"]["id"]).exists()
    assert second["event"]["slug"] == first["event"]["slug"]
    assert legacy_dump(_event(second)) == before
    assert not LegacyId.objects.exclude(event_id=second["event"]["id"]).exists()


def test_one_source_imports_under_two_names_with_separate_ids(tmp_path):
    database = legacy_folder(tmp_path, "v10")
    first, _ = _import(tmp_path, database, "--event", "Pack 1")
    second, _ = _import(tmp_path, database, "--event", "Pack 2")
    one, two = _event(first), _event(second)

    assert legacy_dump(one) == legacy_dump(two)
    map_one = {
        (r.model, r.legacy_id): r.new_id for r in LegacyId.objects.filter(event=one)
    }
    map_two = {
        (r.model, r.legacy_id): r.new_id for r in LegacyId.objects.filter(event=two)
    }
    assert map_one.keys() == map_two.keys()
    assert all(map_one[key] != map_two[key] for key in map_one)
    assert check_invariants(one) == {} and check_invariants(two) == {}
