"""`manage.py export_event` writes a zip that `import_derbynet` reads back
with the same racers, results and media (#212; #9, Archive)."""

import zipfile
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Event, RaceSettings
from core.services import event_dir
from racing.models import HeatLane
from roster.models import Racer
from transfer.export import DATABASE, MEDIA_FOLDERS
from ui.tests.race import race_round, race_setup, scheduled

pytestmark = pytest.mark.django_db


def racers(event):
    return sorted(
        (
            r.first_name,
            r.last_name,
            r.car_number,
            r.car_name,
            r.group.name,
            r.subgroup.name,
            r.partition.name,
            r.passed_inspection,
            r.exclude,
        )
        for r in Racer.objects.filter(event=event).select_related(
            "group", "subgroup", "partition"
        )
    )


def results(event):
    return sorted(
        (
            lane.round.group.name,
            lane.round.number,
            lane.heat,
            lane.lane,
            lane.racer.car_number,
            lane.finish_time,
            lane.finish_place,
            # RaceChart.completed holds whole seconds (`snapshot._stamp`).
            lane.completed and lane.completed.replace(microsecond=0),
        )
        for lane in HeatLane.objects.filter(event=event).select_related(
            "round__group", "racer"
        )
    )


def photo(event, racer_car, column):
    """The bytes of a racer's photo, as the event's folder holds them."""
    folders = RaceSettings.objects.get(event=event)
    folder = (
        folders.photo_directory if column == "headshot" else folders.car_photo_directory
    )
    racer = Racer.objects.get(event=event, car_number=racer_car)
    name = getattr(racer, column)
    return name, (Path(folder) / name).read_bytes()


@pytest.fixture
def raced(settings):
    from core.events import create_event

    event = create_event("Pack 42 2026")
    rounds = race_setup(event, groups=(("Wolves", 4), ("Bears", 3)))
    scheduled(rounds["Wolves"])
    race_round(event)
    folders = RaceSettings.objects.get(event=event)
    head = Path(folders.photo_directory)
    (head / "0-cropped").mkdir(parents=True, exist_ok=True)
    (head / "wolf.jpg").write_bytes(b"original headshot")
    (head / "0-cropped" / "wolf.jpg").write_bytes(b"cropped headshot")
    Path(folders.car_photo_directory).mkdir(parents=True, exist_ok=True)
    (Path(folders.car_photo_directory) / "car.jpg").write_bytes(b"car photo")
    Racer.objects.filter(event=event, car_number=101).update(
        headshot="0-cropped/wolf.jpg", car_photo="car.jpg"
    )
    Path(folders.video_directory).mkdir(parents=True, exist_ok=True)
    (Path(folders.video_directory) / "Wolves_Round1_Heat01.mkv").write_bytes(b"clip")
    (event_dir(event.slug) / "slides" / "welcome.png").write_bytes(b"slide")
    return event


def test_an_exported_event_imports_with_the_same_racers_results_and_media(
    raced, tmp_path
):
    target = tmp_path / "out" / "pack42.zip"

    call_command("export_event", raced.slug, output=target)
    call_command("import_derbynet", str(target), event="Restored")

    restored = Event.objects.get(name="Restored")
    assert racers(restored) == racers(raced)
    assert results(restored) == results(raced)
    assert len(results(restored)) == HeatLane.objects.filter(event=raced).count() > 0
    assert photo(restored, 101, "headshot") == photo(raced, 101, "headshot")
    assert photo(restored, 101, "car_photo") == photo(raced, 101, "car_photo")
    new = RaceSettings.objects.get(event=restored)
    assert (Path(new.video_directory) / "Wolves_Round1_Heat01.mkv").read_bytes() == (
        b"clip"
    )
    assert (event_dir(restored.slug) / "slides" / "welcome.png").read_bytes() == (
        b"slide"
    )


def test_the_zip_is_a_standalone_database_beside_the_media_folders(raced, tmp_path):
    target = tmp_path / "pack42.zip"

    call_command("export_event", raced.slug, output=target)

    names = zipfile.ZipFile(target).namelist()
    assert DATABASE in names
    assert {f"{folder}/" for folder in MEDIA_FOLDERS} <= set(names)
    assert "racers/0-cropped/wolf.jpg" in names
    assert "videos/Wolves_Round1_Heat01.mkv" in names


def test_a_read_only_event_exports(raced, tmp_path):
    Event.objects.filter(pk=raced.pk).update(read_only=True)

    call_command("export_event", raced.slug, output=tmp_path / "e.zip")

    assert zipfile.is_zipfile(tmp_path / "e.zip")


def test_an_existing_file_needs_force(raced, tmp_path):
    target = tmp_path / "e.zip"
    target.write_bytes(b"keep")

    with pytest.raises(CommandError, match="--force"):
        call_command("export_event", raced.slug, output=target)
    assert target.read_bytes() == b"keep"

    call_command("export_event", raced.slug, output=target, force=True)
    assert zipfile.is_zipfile(target)


def test_an_unknown_slug_is_refused():
    with pytest.raises(CommandError, match="no event"):
        call_command("export_event", "nope")


def test_a_zip_member_outside_the_folder_is_refused(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as out:
        out.writestr(DATABASE, b"")
        out.writestr("../escape.txt", b"x")

    with pytest.raises(CommandError, match="outside"):
        call_command("import_derbynet", str(bad), event="Bad")
    assert not (tmp_path / "escape.txt").exists()
