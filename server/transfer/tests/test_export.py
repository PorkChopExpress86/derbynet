"""`manage.py export_event` writes a zip that `import_derbynet` reads back
with the same racers, results and media (#212; #9, Archive)."""

import zipfile
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import Event, RaceSettings
from core.services import event_dir
from kiosks.models import Kiosk
from racing.models import HeatLane
from roster.models import Group, Racer, Subgroup
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


def test_the_round_trip_keeps_what_the_importer_reads_beyond_a_snapshot(
    raced, tmp_path
):
    """#212 review: a racer's note and check-in time, the classes' and ranks'
    trophy counts, the kiosks, and photos no racer shows all come back."""
    folders = RaceSettings.objects.get(event=raced)
    (Path(folders.photo_directory) / "unassigned.jpg").write_bytes(b"spare")
    (Path(folders.car_photo_directory) / "spare-car.jpg").write_bytes(b"car")
    checked_in = timezone.now().replace(microsecond=0)
    Racer.objects.filter(event=raced, car_number=101).update(
        note="Bring a spare axle", checked_in_at=checked_in
    )
    Group.objects.filter(event=raced, name="Wolves").update(n_trophies=2)
    Subgroup.objects.filter(event=raced, group__name="Wolves").update(n_trophies=1)
    Kiosk.objects.create(
        event=raced, address="10.0.0.9", name="Lobby TV", page="kiosks/flag.kiosk"
    )
    target = tmp_path / "e.zip"

    call_command("export_event", raced.slug, output=target)
    call_command("import_derbynet", str(target), event="Restored")

    restored = Event.objects.get(name="Restored")
    racer = Racer.objects.get(event=restored, car_number=101)
    assert (racer.note, racer.checked_in_at) == ("Bring a spare axle", checked_in)
    assert Group.objects.get(event=restored, name="Wolves").n_trophies == 2
    assert {
        s.n_trophies
        for s in Subgroup.objects.filter(event=restored, group__name="Wolves")
    } == {1}
    assert Kiosk.objects.filter(
        event=restored, address="10.0.0.9", name="Lobby TV", page="kiosks/flag.kiosk"
    ).exists()
    new = RaceSettings.objects.get(event=restored)
    assert (Path(new.photo_directory) / "unassigned.jpg").read_bytes() == b"spare"
    assert (Path(new.car_photo_directory) / "spare-car.jpg").read_bytes() == b"car"


def test_an_import_does_not_replace_a_read_only_event(raced, tmp_path):
    """README: an import's --replace refuses a read-only event (D14, D81)."""
    target = tmp_path / "e.zip"
    call_command("export_event", raced.slug, output=target)
    Event.objects.filter(pk=raced.pk).update(read_only=True)
    racers_before = racers(raced)

    with pytest.raises(CommandError, match="read-only"):
        call_command(
            "import_derbynet",
            str(target),
            event=raced.name,
            replace=True,
            discard_videos=True,
        )

    assert Event.objects.get(pk=raced.pk).read_only
    assert racers(raced) == racers_before
    assert Event.objects.filter(name=raced.name).count() == 1
