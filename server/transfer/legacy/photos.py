"""Rebasing legacy photo paths into the new event's photo repositories.

`RegistrationInfo.imagefile` and `carphoto` hold absolute paths on the machine
that recorded them, on Windows too. A `Racer` keeps a path relative to the
event's repository: `""`, `"<basename>"`, or `"0-cropped/<basename>"`
(`roster.models.PHOTO_PATH_REGEX`). The importer copies the files into a
staging folder, and `publish()` moves them into the event's folder after the
database transaction commits.
"""

import ntpath
import posixpath
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.services import event_dir

from .report import Kind

CROPPED = "0-cropped"

# `photo.upload` keeps these characters of a file name and replaces each run of
# others with `_` (`action.photo.upload.inc`).
UNSAFE_NAME_CHARACTERS = re.compile(r"[^-a-zA-Z0-9 .]+")


@dataclass(frozen=True)
class Repository:
    """One legacy photo column and the event folder it lands in."""

    column: str  # legacy column
    folder: str  # folder in the event's directory
    setting: str  # the legacy RaceInfo key of its directory
    sibling_folders: tuple[str, ...]


HEADSHOTS = Repository("imagefile", "racers", "photo-directory", ("racers", "Racers"))
CAR_PHOTOS = Repository("carphoto", "cars", "car-photo-directory", ("cars", "Cars"))
REPOSITORIES = (HEADSHOTS, CAR_PHOTOS)


def basename(path):
    """The last component of a Windows or a POSIX path."""
    return ntpath.basename(posixpath.basename(path))


def is_valid_name(name):
    """A basename that stays inside its folder: not empty and not all dots."""
    return (
        bool(name)
        and "/" not in name
        and "\\" not in name
        and name.strip(".") != ""
        # The name of the cropped photos' folder.
        and name != CROPPED
    )


def relative_parts(path, directory):
    """The path below `directory`, as components, under Windows or POSIX rules.

    A path outside `directory`, or with no `directory`, gives its basename,
    under `0-cropped` when that is the folder it is in.
    """
    if directory:
        for rules in (posixpath, ntpath):
            root = rules.normpath(directory).rstrip(rules.sep)
            full = rules.normpath(path)
            # normcase folds case for Windows paths only.
            if rules.normcase(full).startswith(rules.normcase(root + rules.sep)):
                rest = full[len(root) + 1 :]
                return [part for part in re.split(r"[\\/]", rest) if part]
    folder = basename(ntpath.dirname(path.replace("/", "\\")))
    return [CROPPED, basename(path)] if folder == CROPPED else [basename(path)]


class Photos:
    """Stages photo files for one import and rebases each racer's paths."""

    def __init__(self, report, staging, *, source_path, legacy_dirs, search_dirs):
        self.report = report
        self.staging = Path(staging)
        self.source_dir = Path(source_path).resolve().parent
        self.legacy_dirs = legacy_dirs  # Repository -> legacy directory or None
        self.search_dirs = search_dirs  # Repository -> --photos-dir style override
        self.snapshot = {}  # (racerid, column) -> SnapshotPhoto
        self.copied = 0
        self.referenced = 0
        # Racer id -> the files to write once the racer is imported.
        self.pending = {}
        self._racerid = None

    def add_snapshot_photos(self, photos):
        for photo in photos:
            self.snapshot[(photo.racerid, photo.column)] = photo

    def _roots(self, repository):
        roots = [self.search_dirs.get(repository)]
        roots += [self.source_dir / folder for folder in repository.sibling_folders]
        if repository is CAR_PHOTOS:
            # PHP keeps car photos with the headshots when no car photo
            # directory is set (`car_photo_directory()`).
            roots += self._roots(HEADSHOTS)
        return [Path(root) for root in roots if root]

    def _copy(self, source, repository, relative):
        self.pending.setdefault(self._racerid, []).append(
            (lambda target: shutil.copyfile(source, target), repository, relative)
        )

    def _write(self, data, repository, relative):
        self.pending.setdefault(self._racerid, []).append(
            (lambda target: target.write_bytes(data), repository, relative)
        )

    def stage(self, racerids):
        """Write the files of the imported racers into the staging folder."""
        for racerid in racerids:
            for write, repository, relative in self.pending.pop(racerid, []):
                target = self.staging / repository.folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                write(target)
                self.copied += 1

    def rebase(self, racerid, repository, legacy_path):
        """The racer's new relative path. Copies the files it names."""
        if not legacy_path:
            return ""
        self.referenced += 1
        self._racerid = racerid
        photo = self.snapshot.get((racerid, repository.column))
        if photo is not None:
            return self._from_snapshot(racerid, repository, photo)
        return self._from_files(racerid, repository, str(legacy_path))

    def _from_snapshot(self, racerid, repository, photo):
        name = UNSAFE_NAME_CHARACTERS.sub("_", basename(photo.basename))
        if not is_valid_name(name):
            self.report.add(
                Kind.PHOTO_PATH,
                "registrationinfo",
                racerid,
                f"{repository.column}: snapshot basename {photo.basename!r} has no"
                " usable file name; the photo is dropped",
            )
            return ""
        if name != photo.basename:
            self.report.add(
                Kind.PHOTO_PATH,
                "registrationinfo",
                racerid,
                f"{repository.column}: snapshot basename {photo.basename!r}"
                f" saved as {name!r}",
            )
        if photo.original is not None:
            self._write(photo.original, repository, name)
            self._write(photo.displayed, repository, f"{CROPPED}/{name}")
            return f"{CROPPED}/{name}"
        self._write(photo.displayed, repository, name)
        return name

    def _from_files(self, racerid, repository, legacy_path):
        parts = relative_parts(legacy_path, self.legacy_dirs.get(repository))
        name = parts[-1]
        cropped = parts[:-1] == [CROPPED]
        if not is_valid_name(name):
            self.report.add(
                Kind.PHOTO_PATH,
                "registrationinfo",
                racerid,
                f"{repository.column}: {legacy_path!r} has no usable file name;"
                " the photo is dropped",
            )
            return ""
        if len(parts) > 1 and not cropped:
            self.report.add(
                Kind.PHOTO_PATH,
                "registrationinfo",
                racerid,
                f"{repository.column}: {legacy_path!r} is in a subfolder"
                f" {'/'.join(parts[:-1])!r}; using the original {name!r}",
            )

        for root in self._roots(repository):
            original, crop = root / name, root / CROPPED / name
            if cropped and crop.is_file():
                if original.is_file():
                    self._copy(original, repository, name)
                self._copy(crop, repository, f"{CROPPED}/{name}")
                return f"{CROPPED}/{name}"
            if original.is_file():
                self._copy(original, repository, name)
                if cropped:
                    self.report.add(
                        Kind.PHOTO_PATH,
                        "registrationinfo",
                        racerid,
                        f"{repository.column}: no cropped copy of {name!r} under"
                        f" {root}; using the original",
                    )
                return name

        wanted = f"{CROPPED}/{name}" if cropped else name
        self.report.add(
            Kind.MISSING_PHOTO,
            "registrationinfo",
            racerid,
            f"{repository.column}: {legacy_path!r} not found as {wanted!r} under"
            f" {', '.join(str(root) for root in self._roots(repository))}",
        )
        return wanted


def publish(staging, slug):
    """Move the staged photo folders into the event's folder.

    Folders already there move into `staging`, and the returned function moves
    them back, for when the transaction that published them rolls back.
    """
    staging = Path(staging)
    target = event_dir(slug)
    target.mkdir(parents=True, exist_ok=True)
    moved = []

    def restore():
        for folder, kept in reversed(moved):
            if folder.exists():
                shutil.rmtree(folder)
            if kept is not None:
                shutil.move(kept, folder)

    try:
        for repository in REPOSITORIES:
            folder = target / repository.folder
            kept = None
            if folder.exists():
                kept = staging / f"replaced-{repository.folder}"
                shutil.move(folder, kept)
            moved.append((folder, kept))
            staged = staging / repository.folder
            if staged.exists():
                shutil.move(staged, folder)
            else:
                folder.mkdir()
    except BaseException:
        restore()
        raise
    return restore
