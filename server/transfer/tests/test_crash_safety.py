"""A process killed at any step of `--replace` leaves one whole event."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import CommandError
from django.db import connection

from core.models import Event, RaceSettings
from roster.models import Racer
from transfer.legacy.folders import FOLDERS, ImportFolder, recover
from transfer.tests.legacy import event_folder, legacy_folder, run_import

SERVER = Path(__file__).resolve().parents[2]

STEPS = [
    ("staged", "old"),
    ("pointed", "old"),
    ("ids-written", "old"),
    ("checked", "old"),
    ("noted", "old"),
    ("committed", "new"),
    ("removing", "new"),
    ("removed-one", "new"),
    ("finished", "new"),
]


def _md5(data):
    return hashlib.md5(data).hexdigest()


def _files(event):
    """`{"<folder>/<path>": md5}` for every file in the event's folders."""
    settings_row = RaceSettings.for_event(event)
    files = {}
    for field in FOLDERS:
        root = Path(getattr(settings_row, field))
        for path in root.rglob("*"):
            if path.is_file():
                files[f"{root.name}/{path.relative_to(root)}"] = _md5(path.read_bytes())
    return files


def _photos_resolve(event):
    settings_row = RaceSettings.for_event(event)
    for racer in Racer.objects.filter(event=event):
        if racer.headshot:
            assert (Path(settings_row.photo_directory) / racer.headshot).is_file()
        if racer.car_photo:
            assert (Path(settings_row.car_photo_directory) / racer.car_photo).is_file()


# serialized_rollback restores the seeded award types after the flush.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize("step, survivor", STEPS)
def test_a_replace_killed_at_any_step_leaves_one_whole_event(
    tmp_path, data_dir, step, survivor
):
    first, _ = run_import(
        legacy_folder(tmp_path / "old", "v10"),
        "--event",
        "Pack",
        report_dir=tmp_path,
    )
    old = Event.objects.get(pk=first["event"]["id"])
    old_settings = RaceSettings.for_event(old)
    for directory in (old_settings.video_directory, old_settings.logs_directory):
        Path(directory).mkdir(exist_ok=True)
        (Path(directory) / "old.txt").write_bytes(b"old event")
    old_files = _files(old)
    assert len(old_files) == 6
    source = legacy_folder(tmp_path / "new", "v10")
    (source.parent / "racers" / "head-111.jpg").write_bytes(b"new 111")
    new_files = {
        **{k: v for k, v in old_files.items() if not k.endswith("old.txt")},
        "racers/head-111.jpg": _md5(b"new 111"),
    }

    killed = subprocess.run(
        [
            sys.executable,
            "-m",
            "transfer.tests.crash_import",
            step,
            connection.settings_dict["NAME"],
            str(source),
            "--event",
            "Pack",
            "--replace",
            "--discard-videos",
        ],
        cwd=SERVER,
        env={**os.environ, "DERBYNET_DATA_DIR": str(data_dir)},
        capture_output=True,
        text=True,
    )
    assert killed.returncode == 9, killed.stderr

    (event,) = Event.objects.all()
    assert (event.id == old.id) == (survivor == "old")
    assert _files(event) == (old_files if survivor == "old" else new_files)
    assert Racer.objects.filter(event=event).count() == 82
    _photos_resolve(event)

    recover(data_dir / "events")

    assert _files(event) == (old_files if survivor == "old" else new_files)
    folder = event_folder(event)
    assert [p.name for p in folder.parent.iterdir()] == [folder.name]
    assert sorted(p.name for p in folder.iterdir()) == [
        "cars",
        "logs",
        "racers",
        "videos",
    ]


@pytest.mark.django_db
def test_recover_removes_only_unfinished_imports_that_no_process_holds(
    tmp_path, data_dir
):
    events = data_dir / "events"
    report, _ = run_import(
        legacy_folder(tmp_path, "v10"), "--event", "Pack", report_dir=tmp_path
    )
    live = event_folder(Event.objects.get(pk=report["event"]["id"]))
    # Finished, but no event here points at it: another database's event,
    # or a DATA_DIR shared by mistake. Not provably dead, so it stays.
    finished = events / "other.import-finished"
    (finished / "racers").mkdir(parents=True)
    dead = events / "dead.import-pending"
    (dead / "racers").mkdir(parents=True)
    (dead / ".pending").touch()
    running = ImportFolder(events, "running")

    removed = recover(events)

    assert removed == [dead]
    assert sorted(p.name for p in events.iterdir()) == sorted(
        [live.name, finished.name, running.path.name]
    )
    running.discard()


def _rmtree_that_dies_after_the_pending_mark():
    """Delete the `.pending` mark first, then stop, as a crash can."""

    def partial(path, *args, **kwargs):
        (Path(path) / ".pending").unlink(missing_ok=True)
        raise KeyboardInterrupt

    return partial


@pytest.mark.django_db
def test_a_removal_killed_after_the_pending_mark_is_cleaned_by_the_next_import(
    tmp_path, data_dir, monkeypatch
):
    events = data_dir / "events"
    folder = ImportFolder(events, "dead")
    (folder.path / "racers" / "head.jpg").write_bytes(b"copy")
    with monkeypatch.context() as patched:
        patched.setattr(
            "transfer.legacy.folders.shutil.rmtree",
            _rmtree_that_dies_after_the_pending_mark(),
        )
        with pytest.raises(KeyboardInterrupt):
            folder.discard()
    leaked = [p for p in events.iterdir() if p != folder.path]
    assert len(leaked) == 1
    assert leaked[0].name.startswith(".removing-")
    assert not (leaked[0] / ".pending").exists()

    report, _ = run_import(
        legacy_folder(tmp_path, "v10"), "--event", "Pack", report_dir=tmp_path
    )

    assert str(leaked[0]) in report["recovered"]
    live = event_folder(Event.objects.get(pk=report["event"]["id"]))
    assert [p.name for p in events.iterdir()] == [live.name]


@pytest.mark.django_db
def test_recover_removes_a_removing_folder_unless_an_event_points_at_it(
    tmp_path, data_dir
):
    events = data_dir / "events"
    report, _ = run_import(
        legacy_folder(tmp_path, "v10"), "--event", "Pack", report_dir=tmp_path
    )
    event = Event.objects.get(pk=report["event"]["id"])
    live = event_folder(event)
    dead = events / ".removing-dead"
    (dead / "racers").mkdir(parents=True)
    (dead / "racers" / "x.jpg").write_bytes(b"x")
    used = events / ".removing-used"
    for folder in FOLDERS.values():
        (used / folder).mkdir(parents=True)
    RaceSettings.objects.filter(event=event).update(
        **{field: str(used / folder) for field, folder in FOLDERS.items()}
    )

    removed = recover(events)

    assert removed == [dead]
    assert sorted(p.name for p in events.iterdir()) == sorted([live.name, used.name])


@pytest.mark.django_db
def test_a_refused_replace_runs_before_recover_and_leaves_dead_folders(
    tmp_path, data_dir
):
    events = data_dir / "events"
    first, _ = run_import(
        legacy_folder(tmp_path, "v10"), "--event", "Pack", report_dir=tmp_path
    )
    old = RaceSettings.for_event(Event.objects.get(pk=first["event"]["id"]))
    (Path(old.video_directory) / "heat.mkv").write_bytes(b"video")
    pending = events / "dead.import-pending"
    (pending / "racers").mkdir(parents=True)
    (pending / ".pending").touch()
    removing = events / ".removing-dead"
    (removing / "racers").mkdir(parents=True)

    with pytest.raises(CommandError, match="--discard-videos"):
        run_import(
            legacy_folder(tmp_path, "v9"),
            "--event",
            "Pack",
            "--replace",
            report_dir=tmp_path,
        )

    assert pending.is_dir()
    assert removing.is_dir()


@pytest.mark.django_db
def test_recover_reads_the_list_note_an_older_import_wrote(tmp_path, data_dir):
    events = data_dir / "events"
    report, _ = run_import(
        legacy_folder(tmp_path, "v10"), "--event", "Pack", report_dir=tmp_path
    )
    live = event_folder(Event.objects.get(pk=report["event"]["id"]))
    old = events / "pack-old"
    (old / "racers").mkdir(parents=True)
    (live / ".replaced").write_text('["pack-old/racers"]')

    removed = recover(events)

    assert removed == [old / "racers"]
    assert [p.name for p in events.iterdir()] == [live.name]
    assert not (live / ".replaced").exists()
