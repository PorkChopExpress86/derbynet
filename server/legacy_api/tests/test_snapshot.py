"""`snapshot.get` and `snapshot.put` (#36, S5 to S7)."""

import io
import json
import os
import re
from pathlib import Path

import pytest

from accounts.permissions import Perm
from awards.models import Award, BallotAward, Vote
from awards.tests.factories import AwardFactory, BallotAwardFactory, VoteFactory
from core.models import Event, RaceSettings, SiteState
from core.tests.factories import EventFactory
from legacy_api.tests.conftest import log_in
from racing.models import HeatLane, RosterEntry, Round
from racing.tests.factories import HeatLaneFactory, RoundFactory
from roster.models import Group, Racer, Subgroup
from roster.tests.factories import GroupFactory, RacerFactory, SubgroupFactory
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


def _awards_and_ballot(event):
    """A pack award, a group award, both on the ballot, a vote each from two
    voters, and a durable aggregate beside a plain group."""
    lions = GroupFactory(event=event, name="Lions")
    every = GroupFactory(event=event, name="All", durable=True)
    for group in (lions, every):
        RoundFactory(event=event, group=group, number=1)
    racer = RacerFactory(group=lions)
    pack = AwardFactory(event=event, name="Pack")
    den = AwardFactory(event=event, name="Den", group=lions)
    for award in (pack, den):
        BallotAwardFactory(award=award, depth=2)
    for identifier in ("one", "two"):
        VoteFactory(
            voter__event=event, voter__identifier=identifier, award=pack, racer=racer
        )


def test_get_writes_the_values_php_stores(client, coordinator):
    # #221: PHP leaves Classes.durable NULL unless a class is made durable
    # (create_class, website/inc/aggregate_round.inc:115-133, class.add's 1),
    # stores 0 for an award's missing classid and rankid (award.edit's
    # class_and_rank of 0, website/ajax/action.award.edit.inc:68-73) and
    # racerid (`racerid INTEGER DEFAULT 0`, website/sql/sqlite/schema.inc:23),
    # and has one ballot, BALLOTID 1 (website/inc/voterid.inc:5-6), for every
    # BallotAwards and Voters row.
    _awards_and_ballot(coordinator)

    tables = tables_of(get(client).content)

    assert [r["durable"] for r in tables["Classes"]["rows"]] == [None, 1]
    assert [
        (r["classid"], r["rankid"], r["racerid"]) for r in tables["Awards"]["rows"]
    ] == [(0, 0, 0), (1, 0, 0)]
    assert [r["ballotid"] for r in tables["BallotAwards"]["rows"]] == [1, 1]
    assert tables["BallotAwards"]["high-water"] == 1
    assert tables["BallotAwards"]["missing"] == []
    assert [r["ballotid"] for r in tables["Voters"]["rows"]] == [1, 1]


def test_get_then_put_keeps_the_awards_and_the_ballot(client, coordinator):
    _awards_and_ballot(coordinator)
    first = tables_of(get(client).content)

    assert "<success/>" in put(client, get(client).content).content.decode()

    event = active()
    assert event.pk != coordinator.pk
    assert tables_of(get(client).content) == first
    assert sorted(
        (a.name, a.group.name if a.group else None, a.racer_id)
        for a in Award.objects.filter(event=event)
    ) == [("Den", "Lions", None), ("Pack", None, None)]
    assert sorted(Group.objects.filter(event=event).values_list("name", "durable")) == [
        ("All", True),
        ("Lions", False),
    ]
    assert BallotAward.objects.filter(event=event).count() == 2
    assert Vote.objects.filter(event=event).count() == 2


# A value other than its default for every RaceSettings field, but the
# folders: a restore keeps the open event's folders (#134), which
# test_a_restore_keeps_the_folder_paths_and_brings_the_photos_back checks.
# The two references are filled in by the test.
EVERY_SETTING = {
    "lane_count": 6,
    "unused_lane_mask": 2,
    "reverse_lanes": True,
    "use_master_sched": True,
    "use_points": True,
    "scoring": 1,
    "max_runs_per_car": 2,
    "rotation_schedule": True,
    "track_length": 32.5,
    "finish_formatting": "%6.4f",
    "update_period_ms": 3000,
    "warn_no_timer": False,
    "timer_send_logs": True,
    "group_formation_rule": "by-class",
    "use_subgroups": True,
    "supergroup_label": "Troop",
    "partition_label": "Patrol",
    "full_field_rule": "group",
    "full_field_group": "All",
    "n_pack_trophies": 4,
    "n_den_trophies": 2,
    "n_rank_trophies": 1,
    "one_trophy_per": True,
    "use_xbs": True,
    "xbs_award": "Best Build",
    "car_numbering": "200+201",
    "name_style": 1,
    "ballot_password": "secret",
    "photos_on_now_racing": "car",
    "show_cars_on_deck": False,
    "show_racer_photos_rr": False,
    "show_car_photos_rr": False,
    "now_racing_linger_ms": 5000,
    "lane_colors": "red,blue",
    "images_dir": "Cub Scouts",
    "racing_scene": "Racing",
    "upload_videos": False,
    "replay_skipback_ms": 6000,
    "replay_num_showings": 3,
    "replay_rate": 75,
    "replay_post_roll_ms": 2003,
    "replay_max_clip_ms": 12000,
    "replay_ice_servers": "stun:stun.example.org:3478",
    "websocket_url": "ws://example.org/ws",
    "ws_trigger_port": "8080",
}
KEPT_FOLDERS = {"photo_directory", "car_photo_directory", "video_directory"}
KEPT_FOLDERS |= {"logs_directory"}


def _settings_by_name(event) -> dict:
    row = RaceSettings.objects.get(event=event)
    out = {}
    for name in EVERY_SETTING:
        value = getattr(row, name)
        out[name] = getattr(value, "name", value)
    return out


def test_get_then_put_keeps_every_setting(client, coordinator):
    # D82: the snapshot carries every setting Django keeps, so a new field
    # with no RaceInfo key fails here.
    from kiosks.models import Scene
    from roster.models import GroupConstituent

    fields = {
        f.name
        for f in RaceSettings._meta.concrete_fields
        if f.name not in ("id", "event")
    }
    assert set(EVERY_SETTING) == fields - KEPT_FOLDERS
    defaults = RaceSettings()
    for name, value in EVERY_SETTING.items():
        default = getattr(defaults, name)
        assert value != getattr(default, "name", default), name
    # Another event's rows first, so no id is the snapshot's own number: a
    # setting that names a row has to be renumbered with it.
    other = EventFactory()
    GroupFactory.create_batch(2, event=other)
    lions = GroupFactory(event=coordinator, name="Lions")
    RoundFactory(event=coordinator, group=lions, number=1)
    every = GroupFactory(event=coordinator, name="All", durable=True)
    GroupConstituent.objects.create(
        event=coordinator, aggregate=every, constituent=lions
    )
    values = {
        **EVERY_SETTING,
        "full_field_group": every,
        "racing_scene": Scene.objects.get(event=coordinator, name="Racing"),
    }
    RaceSettings.objects.filter(event=coordinator).update(**values)
    assert _settings_by_name(coordinator) == EVERY_SETTING

    assert "<success/>" in put(client, get(client).content).content.decode()

    assert active().pk != coordinator.pk
    assert _settings_by_name(active()) == EVERY_SETTING


def test_get_then_put_keeps_the_rows_the_race_state_names(client, coordinator):
    # RaceInfo's ids are the snapshot's numbers for the rows they name, as
    # the importer reads them back, whatever ids the database handed out.
    from core.models import RaceState
    from kiosks.models import Scene

    other = EventFactory()
    RoundFactory.create_batch(2, event=other, group__event=other)
    lions = GroupFactory(event=coordinator, name="Lions")
    first = RoundFactory(event=coordinator, group=lions, number=1)
    RoundFactory(event=coordinator, group=lions, number=2)
    awards = Scene.objects.get(event=coordinator, name="Awards")
    RaceState.objects.filter(event=coordinator).update(
        current_group=lions,
        current_round=first,
        current_heat=2,
        last_heat={"round_id": first.pk, "heat": 1, "results": None},
        current_scene=awards,
        playlist_scene=awards,
    )

    assert "<success/>" in put(client, get(client).content).content.decode()

    state = RaceState.objects.get(event=active())
    assert (state.current_group.name, state.current_round.number) == ("Lions", 1)
    assert state.last_heat == {
        "round_id": state.current_round_id,
        "heat": 1,
        "results": None,
    }
    assert (state.current_scene.name, state.playlist_scene.name) == ("Awards",) * 2


def _kiosk_classes(event, params: str) -> list[str]:
    """The names of the groups a kiosk's `classids` lists, in its order."""
    names = dict(Group.objects.filter(event=event).values_list("pk", "name"))
    return [names.get(pk, f"no group {pk}") for pk in json.loads(params)["classids"]]


def _standings_of(event, params: str) -> tuple:
    """The round and subgroup a standings kiosk's `st-rr<round>-<subgroup>`
    key names, by name."""
    round_id, subgroup_id = json.loads(params)["key"].removeprefix("st-rr").split("-")
    found = Round.objects.filter(event=event, pk=round_id).first()
    subgroup = Subgroup.objects.filter(event=event, pk=subgroup_id).first()
    return (
        found and (found.group.name, found.number),
        subgroup and subgroup.name,
    )


def _pk(model, event, name):
    return model.objects.get(event=event, name=name).pk


# The current award key of each kind, built from the event's rows by name,
# as awards/presentation.py and speed_awards.py build them.
AWARD_KEYS = {
    "judged": lambda e: f"award-{_pk(Award, e, 'Best paint')}",
    "pack": lambda e: "speed-2",
    "group with a tie": lambda e: f"speed-1b-{_pk(Group, e, 'Lions')}",
    "subgroup": lambda e: (
        f"speed-3-{_pk(Group, e, 'Lions')}-{_pk(Subgroup, e, 'Cubs')}"
    ),
}


@pytest.mark.parametrize("award", AWARD_KEYS)
def test_get_then_put_keeps_each_kiosks_classes_and_the_current_award(
    client, coordinator, award
):
    """#231: the ids inside a kiosk's parameters and the current award key
    name the same rows, by name, after a round trip."""
    from core.models import RaceState
    from kiosks.models import Scene, SceneKiosk

    other = EventFactory()
    GroupFactory.create_batch(2, event=other)
    AwardFactory.create_batch(2, event=other)
    wolves = GroupFactory(event=coordinator, name="Wolves")
    lions = GroupFactory(event=coordinator, name="Lions")
    SubgroupFactory(event=coordinator, group=wolves, name="Pups")
    cubs = SubgroupFactory(event=coordinator, group=lions, name="Cubs")
    RoundFactory(event=coordinator, group=wolves, number=1)
    RoundFactory(event=coordinator, group=lions, number=1)
    lions_2 = RoundFactory(event=coordinator, group=lions, number=2)
    AwardFactory(event=coordinator, name="Other")
    AwardFactory(event=coordinator, name="Best paint")
    racing = Scene.objects.get(event=coordinator, name="Racing")
    slideshow = {"title": "Hi", "subdir": "", "classids": [lions.pk, wolves.pk]}
    for name, page, params in (
        ("Lobby", "please-check-in", {"classids": [lions.pk]}),
        ("Hall", "slideshow", slideshow),
        ("Board", "standings", {"key": f"st-rr{lions_2.pk}-{cubs.pk}"}),
    ):
        SceneKiosk.objects.create(
            event=coordinator,
            scene=racing,
            kiosk_name=name,
            page=f"kiosks/{page}.kiosk",
            params=json.dumps(params),
        )
    RaceState.objects.filter(event=coordinator).update(
        current_award_key=AWARD_KEYS[award](coordinator)
    )

    assert "<success/>" in put(client, get(client).content).content.decode()

    new = active()
    kiosks = {k.kiosk_name: k.params for k in SceneKiosk.objects.filter(event=new)}
    assert _kiosk_classes(new, kiosks["Lobby"]) == ["Lions"]
    assert _kiosk_classes(new, kiosks["Hall"]) == ["Lions", "Wolves"]
    assert json.loads(kiosks["Hall"])["title"] == "Hi"
    assert _standings_of(new, kiosks["Board"]) == (("Lions", 2), "Cubs")
    state = RaceState.objects.get(event=new)
    assert state.current_award_key == AWARD_KEYS[award](new)


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
