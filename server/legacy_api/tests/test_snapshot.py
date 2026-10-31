"""`snapshot.get` and `snapshot.put` (#36, S5 to S7)."""

import io
import json
import os
import re
from pathlib import Path

import pytest

from accounts.permissions import Perm
from core.models import Event, RaceSettings, SiteState
from core.tests.factories import EventFactory
from legacy_api.tests.conftest import log_in
from racing.models import HeatLane, RosterEntry, Round
from racing.tests.factories import HeatLaneFactory, RoundFactory
from roster.models import Group, Racer
from roster.tests.factories import GroupFactory, RacerFactory
from transfer.legacy import folders
from transfer.legacy.folders import FOLDERS, ImportFolder, recover

pytestmark = pytest.mark.django_db

GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "tests/golden/http/standings/q13-drop-slowest.snapshot.xml"
)


@pytest.fixture(autouse=True)
def events_root(settings, tmp_path):
    """Each test's event folders go under its own temporary directory. Autouse,
    so the folders of the `event` fixture are there too."""
    settings.DATA_DIR = tmp_path / "data"
    return settings.DATA_DIR / "events"


@pytest.fixture
def coordinator(client, event):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    return event


def get(client, query=""):
    return client.get(f"/action.php?query=snapshot.get{query}")


def put(client, content: bytes):
    return client.post(
        "/action.php",
        {"action": "snapshot.put", "snapshot": io.BytesIO(content)},
    )


def tables_of(content: bytes) -> dict:
    text = content.decode()
    body = text[text.index("<![CDATA[") + 9 : text.rindex("]]>")]
    return {entry["table"]: entry for entry in json.loads(body) if "table" in entry}


def active():
    return SiteState.objects.get(pk=1).active_event


def test_get_needs_set_up(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert b"notauthorized" in get(client).content


def test_put_needs_set_up(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert b"notauthorized" in put(client, GOLDEN.read_bytes()).content
    assert Event.objects.count() == 1


def test_get_writes_the_wrapper_and_every_table_in_php_order(client, coordinator):
    group = GroupFactory(event=coordinator, name="Lions")
    HeatLaneFactory(
        round=RoundFactory(event=coordinator, group=group),
        heat=1,
        lane=1,
        finish_time=3.5,
        finish_place=1,
        racer__car_number=101,
        racer__passed_inspection=True,
    )

    response = get(client)

    assert response["Content-Type"].startswith("text/xml")
    assert response.content.startswith(
        b'<?xml version="1.0" encoding="UTF-8"?>\n<snapshot><![CDATA[\n['
    )
    tables = tables_of(response.content)
    assert list(tables) == [
        "Classes",
        "Ranks",
        "RegistrationInfo",
        "Rounds",
        "AwardTypes",
        "Awards",
        "Roster",
        "RaceChart",
        "RaceInfo",
        "SceneKiosk",
        "Scenes",
        "Playlist",
        "BallotAwards",
        "Voters",
        "Votes",
        "Partitions",
        "ConstituentClasses",
        "Events",
        "EventLaneDetails",
    ]
    assert tables["Events"]["rows"] == tables["EventLaneDetails"]["rows"] == []
    assert tables["RegistrationInfo"]["rows"][0]["carnumber"] == 101
    assert tables["RaceChart"]["rows"][0]["finishtime"] == 3.5
    assert tables["Classes"]["missing"] == []
    assert tables["Classes"]["high-water"] == len(tables["Classes"]["rows"])


def test_clean_cuts_names_to_initials(client, coordinator):
    group = GroupFactory(event=coordinator)
    HeatLaneFactory(
        round=RoundFactory(event=coordinator, group=group),
        racer__first_name="Matthew",
        racer__last_name="Glennon",
    )

    row = tables_of(get(client, "&clean").content)["RegistrationInfo"]["rows"][0]

    assert (row["firstname"], row["lastname"]) == ("M", "G-1")


def test_a_php_snapshot_loads_with_its_row_counts(client, coordinator):
    reply = put(client, GOLDEN.read_bytes()).content.decode()

    assert "<success/>" in reply
    sent = tables_of(GOLDEN.read_bytes())
    event = active()
    assert Racer.objects.filter(event=event).count() == len(
        sent["RegistrationInfo"]["rows"]
    )
    assert Group.objects.filter(event=event, durable=False).count() <= len(
        sent["Classes"]["rows"]
    )
    assert re.search(r'<table name="Classes" rows-before="0" idcolumn="classid"', reply)
    assert Event.objects.count() == 1


def test_get_then_put_keeps_the_rows(client, coordinator):
    put(client, GOLDEN.read_bytes())
    first = tables_of(get(client).content)

    assert "<success/>" in put(client, get(client).content).content.decode()
    second = tables_of(get(client).content)

    for name in (
        "Classes",
        "Ranks",
        "RegistrationInfo",
        "Rounds",
        "RaceChart",
        "Roster",
    ):
        assert len(second[name]["rows"]) == len(first[name]["rows"]), name
    assert HeatLane.objects.filter(event=active()).count() == len(
        first["RaceChart"]["rows"]
    )
    assert RosterEntry.objects.filter(event=active()).count() == len(
        first["Roster"]["rows"]
    )
    assert Round.objects.filter(event=active()).count() == len(first["Rounds"]["rows"])


def test_an_unreadable_file_fails_and_changes_nothing(client, coordinator):
    group = GroupFactory(event=coordinator, name="Keep me")

    reply = put(client, b"not a snapshot").content.decode()

    assert 'code="json-parsing"' in reply and "<success/>" not in reply
    assert Group.objects.filter(pk=group.pk).exists()
    assert active().pk == coordinator.pk


def test_an_unknown_column_or_table_is_refused_and_changes_nothing(client, coordinator):
    group = GroupFactory(event=coordinator, name="Keep me")
    text = GOLDEN.read_text()
    evil = text.replace(
        '"classid": 1,\n        "class": "Snapshot",',
        '"classid": 1,\n        "class": "Snapshot",'
        '\n        "x); DROP TABLE core_event; --": 1,',
        1,
    ).replace(
        '[\n  {\n    "table": "Classes"',
        '[\n  {"table": "Evil; DROP TABLE core_event", "rows": [{"a": 1}],'
        ' "missing": []},\n  {\n    "table": "Classes"',
        1,
    )
    assert evil != text

    reply = put(client, evil.encode()).content.decode()

    assert 'code="bad-snapshot"' in reply and "<success/>" not in reply
    assert "x); drop table core_event; --" in reply
    assert Event.objects.count() == 1
    assert Group.objects.filter(pk=group.pk).exists()
    assert not Group.objects.filter(event=active(), name="Snapshot").exists()


def test_an_unknown_table_alone_is_refused(client, coordinator):
    text = GOLDEN.read_text()
    evil = text.replace(
        '[\n  {\n    "table": "Classes"',
        '[\n  {"table": "Evil; DROP TABLE core_event", "rows": [{"a": 1}],'
        ' "missing": []},\n  {\n    "table": "Classes"',
        1,
    )
    assert evil != text

    reply = put(client, evil.encode()).content.decode()

    assert 'code="bad-snapshot"' in reply and "evil; drop table" in reply


def test_a_photo_name_cannot_leave_the_photo_folder(client, coordinator, tmp_path):
    text = GOLDEN.read_text()
    photo = {
        "racerid": 1,
        "imagefile": {"basename": "../../escaped.jpg", "displayed": "ffd8ffd9"},
    }
    body = (
        text[: text.rindex("]\n]]>")].rstrip()
        + ",\n"
        + json.dumps(photo)
        + "\n]\n]]></snapshot>\n"
    )

    reply = put(client, body.encode()).content.decode()

    assert 'code="bad-snapshot"' in reply and "../../escaped.jpg" in reply
    assert "<success/>" not in reply
    assert not list(Path("/").glob("escaped.jpg"))
    assert not (tmp_path.parent / "escaped.jpg").exists()
    assert active().pk == coordinator.pk


def test_tables_and_columns_older_php_exports_carried_are_accepted(client, coordinator):
    text = GOLDEN.read_text()
    old = text.replace(
        '[\n  {\n    "table": "Classes"',
        '[\n  {"table": "Divisions", "rows": [{"divisionid": 1}], "missing": []},\n'
        '  {\n    "table": "Classes"',
        1,
    ).replace('"table": "Playlist"', '"table": "RacingQueue"', 1)
    assert old != text

    assert "<success/>" in put(client, old.encode()).content.decode()


# #134: a restore keeps the event's four folder settings.


def folder_settings(event):
    row = RaceSettings.for_event(event)
    return {field: getattr(row, field) for field in FOLDERS}


def with_photos(event):
    """Two racers of `event` with a headshot and a car photo on disk, and
    video and log folders with no files. Returns `{path: bytes}` of the
    photos."""
    for directory in folder_settings(event).values():
        Path(directory).mkdir(parents=True, exist_ok=True)
    row = RaceSettings.for_event(event)
    group = GroupFactory(event=event)
    RoundFactory(event=event, group=group, number=1)
    files = {}
    for n in (1, 2):
        RacerFactory(
            group=group,
            car_number=100 + n,
            headshot=f"head-{n}.jpg",
            car_photo=f"car-{n}.jpg",
        )
        for directory, name in (
            (row.photo_directory, f"head-{n}.jpg"),
            (row.car_photo_directory, f"car-{n}.jpg"),
        ):
            path = Path(directory) / name
            path.write_bytes(f"{name} as saved".encode())
            files[path] = path.read_bytes()
    return files


def photos_resolve(event):
    row = RaceSettings.for_event(event)
    racers = Racer.objects.filter(event=event)
    assert racers.count() == 2
    for racer in racers:
        assert (Path(row.photo_directory) / racer.headshot).is_file()
        assert (Path(row.car_photo_directory) / racer.car_photo).is_file()


def leftovers(events_root):
    """Import and removal folders under `events_root`."""
    return sorted(
        p.name
        for p in events_root.rglob("*")
        if folders.MARK in p.name or p.name.startswith(folders.REMOVING)
    )


def set_folder(event, **paths):
    RaceSettings.objects.filter(event=event).update(
        **{field: str(path) for field, path in paths.items()}
    )


def _usual(event, tmp_path):
    return {}


def _elsewhere(event, tmp_path):
    """The headshots are in a folder outside `events/`, with a file of the
    crew's own."""
    elsewhere = tmp_path / "usb" / "racers"
    elsewhere.mkdir(parents=True)
    (elsewhere / "crew.jpg").write_bytes(b"the crew's own file")
    set_folder(event, photo_directory=elsewhere)
    return {elsewhere / "crew.jpg": b"the crew's own file"}


def _symlink(event, tmp_path):
    """`events/<slug>/racers` is a symlink to a folder on another disk."""
    target = tmp_path / "usb" / "racers"
    target.mkdir(parents=True)
    (target / "crew.jpg").write_bytes(b"the crew's own file")
    link = Path(RaceSettings.for_event(event).photo_directory)
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    return {target / "crew.jpg": b"the crew's own file"}


def _shared(event, tmp_path):
    """Another event points at the same headshot folder, and has a file in
    it."""
    other = EventFactory()
    shared = Path(RaceSettings.for_event(event).photo_directory)
    shared.mkdir(parents=True)
    (shared / "other.jpg").write_bytes(b"the other event's file")
    set_folder(other, photo_directory=shared)
    return {shared / "other.jpg": b"the other event's file"}


def _moved_data_dir(event, tmp_path):
    """The event's folders are under a `DATA_DIR` the server no longer
    uses."""
    old = tmp_path / "old-data" / "events" / event.slug
    set_folder(event, **{field: old / sub for field, sub in FOLDERS.items()})
    return {}


# How the event's folders are laid out before the restore. Each returns the
# files of others that the restore must leave as they are.
LAYOUTS = {
    "usual": _usual,
    "elsewhere": _elsewhere,
    "symlink": _symlink,
    "shared": _shared,
    "moved-data-dir": _moved_data_dir,
}


def set_up(event, layout, tmp_path):
    """`event` in `layout`, with photos, and an empty subfolder in its videos
    folder. Returns the folder settings, the photos, the files of others,
    and the snapshot with photos."""
    others = LAYOUTS[layout](event, tmp_path)
    saved = with_photos(event)
    before = folder_settings(event)
    # A file would make the restore refuse; a subfolder shows whether the
    # videos folder is kept.
    (Path(before["video_directory"]) / "heat-1").mkdir()
    return before, saved, others


def assert_restored(event, before, saved, others, events_root):
    assert folder_settings(event) == before
    for path, content in {**saved, **others}.items():
        assert path.read_bytes() == content
    photos_resolve(event)
    assert (Path(before["video_directory"]) / "heat-1").is_dir()
    assert leftovers(events_root) == []
    for directory in before.values():
        assert not [p for p in Path(directory).rglob("*") if folders.PLACING in p.name]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_a_restore_keeps_the_folder_paths_and_brings_the_photos_back(
    client, coordinator, events_root, tmp_path, layout
):
    before, saved, others = set_up(coordinator, layout, tmp_path)
    snapshot = get(client, "&photos").content
    for path in saved:
        path.write_bytes(b"changed after the snapshot")
    # A file the snapshot does not have.
    stale = Path(before["car_photo_directory"]) / "stale.jpg"
    stale.write_bytes(b"not in the snapshot")

    assert "<success/>" in put(client, snapshot).content.decode()

    event = active()
    assert event.pk != coordinator.pk
    assert_restored(event, before, saved, others, events_root)
    # A swapped folder holds only the snapshot's files; one kept in place,
    # here the car photos under the old DATA_DIR, keeps the others too.
    assert stale.exists() == (layout == "moved-data-dir")
    if layout == "symlink":
        assert Path(before["photo_directory"]).is_symlink()
    if layout == "shared":
        (other,) = Event.objects.exclude(pk=event.pk)
        assert folder_settings(other)["photo_directory"] == before["photo_directory"]


def test_a_restore_over_an_event_with_videos_is_refused(
    client, coordinator, events_root, tmp_path
):
    before, saved, others = set_up(coordinator, "usual", tmp_path)
    video = Path(before["video_directory"]) / "heat-1.mkv"
    video.write_bytes(b"a recording")
    snapshot = get(client, "&photos").content

    reply = put(client, snapshot).content.decode()

    assert 'code="import-failed"' in reply and "--discard-videos" in reply
    assert active().pk == coordinator.pk
    assert video.read_bytes() == b"a recording"
    assert folder_settings(coordinator) == before
    assert leftovers(events_root) == []


class Killed(BaseException):
    """Stands for a process killed at one step: nothing catches it."""


# The steps of a restore after its import's commit, each as when to kill
# (before or after the call), and the function.
KILL_POINTS = {
    "committed": ("before", ImportFolder, "finish"),
    "linked": ("after", folders, "_link_copy"),
    "set-aside": ("after", folders, "_set_aside"),
    "moved-in": ("after", folders, "_move_in"),
    "file-linked": ("after", folders, "_link_or_copy"),
    "placed-file": ("after", folders, "_place_file"),
    "between-folders": ("after", folders, "_keep_one"),
    "repointed": ("after", folders, "_repoint"),
}


def kill_at(point, monkeypatch):
    when, owner, name = KILL_POINTS[point]
    real = getattr(owner, name)

    def wrapped(*args, **kwargs):
        if when == "after":
            real(*args, **kwargs)
        raise Killed

    monkeypatch.setattr(owner, name, wrapped)


@pytest.mark.parametrize(
    "layout, point",
    [
        *(("usual", point) for point in KILL_POINTS if point != "placed-file"),
        # In a folder kept in place, the first file is linked to its
        # temporary name and not yet renamed.
        ("elsewhere", "file-linked"),
        ("elsewhere", "placed-file"),
        ("elsewhere", "between-folders"),
        ("elsewhere", "repointed"),
        ("symlink", "placed-file"),
        ("shared", "placed-file"),
    ],
)
def test_a_restore_killed_before_its_folder_switch_leaves_folders_that_exist(
    client, coordinator, events_root, tmp_path, monkeypatch, layout, point
):
    before, saved, others = set_up(coordinator, layout, tmp_path)
    snapshot = get(client, "&photos").content
    for path in saved:
        path.write_bytes(b"changed after the snapshot")

    started = []
    real_init = ImportFolder.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        started.append(self)

    with monkeypatch.context() as patched:
        patched.setattr(ImportFolder, "__init__", init)
        kill_at(point, patched)
        with pytest.raises(Killed):
            put(client, snapshot)
    # A killed process lets go of its lock.
    (killed,) = started
    os.close(killed._lock)

    event = active()
    assert event.pk != coordinator.pk
    assert all(Path(d).is_dir() for d in folder_settings(event).values())
    photos_resolve(event)

    # The next import, or restore, finishes the switch first.
    recover(events_root)

    assert_restored(event, before, saved, others, events_root)
