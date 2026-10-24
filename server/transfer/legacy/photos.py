"""Rebasing legacy photo paths into the new event's photo repositories.

`RegistrationInfo.imagefile` and `carphoto` hold absolute paths on the machine
that recorded them, on Windows too. A `Racer` keeps a path relative to the
event's repository: `""`, `"<basename>"`, or `"0-cropped/<basename>"`
(`roster.models.PHOTO_PATH_REGEX`). The importer copies the files into the
new event's folder (`legacy.folders`).
"""

import ntpath
import os
import posixpath
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .report import Kind

CROPPED = "0-cropped"
# `Racer.headshot` holds 255 characters, `0-cropped/` included.
MAX_NAME_LENGTH = 255 - len(CROPPED) - 1

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

    def __init__(self, report, folder, *, source_path, legacy_dirs, search_dirs):
        self.report = report
        self.folder = Path(folder)
        self.source_dir = Path(source_path).resolve().parent
        self.legacy_dirs = legacy_dirs  # Repository -> legacy directory or None
        self.search_dirs = search_dirs  # Repository -> --photos-dir style override
        self.snapshot = {}  # (racerid, column) -> SnapshotPhoto
        self.copied = 0
        self.referenced = 0
        # Racer id -> the files to write once the racer is imported.
        self.pending = {}
        # (folder, relative path) -> what was queued there: a source file's
        # path or a snapshot photo's bytes.
        self.claimed = {}
        self.links = set()
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

    def _is_file(self, root, *parts):
        """Whether `root/parts` is a file, reached through no symbolic link.

        A link could point anywhere on this machine, and photos are served to
        anyone (D5). Each link is reported once.
        """
        path = root
        for part in parts:
            path = path / part
            if path.is_symlink():
                if path not in self.links:
                    self.links.add(path)
                    self.report.add(
                        Kind.SYMLINK,
                        "registrationinfo",
                        self._racerid,
                        f"{path} is a symbolic link; skipped",
                    )
                return False
        return path.is_file()

    def _place(self, repository, name, files):
        """Queue `files` under `name`, or under `name-2`, `name-3` and so on
        when another photo already took that name. Returns the name used.

        `files` holds `(cropped, content)` pairs, where `content` is the
        source file's `Path` or the bytes to write.
        """
        stem, extension = os.path.splitext(name)
        candidate, n = _fit(stem, "", extension), 1
        while any(
            self.claimed.get(
                (repository.folder, _relative(cropped, candidate)), content
            )
            != content
            for cropped, content in files
        ):
            n += 1
            candidate = _fit(stem, f"-{n}", extension)
        for cropped, content in files:
            relative = _relative(cropped, candidate)
            self.claimed[(repository.folder, relative)] = content
            self.pending.setdefault(self._racerid, []).append(
                (content, repository, relative)
            )
        reasons = []
        if _fit(stem, "", extension) != name:
            reasons.append("is too long for a file name")
        if n > 1:
            reasons.append("is taken by another photo")
        if reasons:
            self.report.add(
                Kind.PHOTO_PATH,
                "registrationinfo",
                self._racerid,
                f"{repository.column}: {name!r} {' and '.join(reasons)}; saved as"
                f" {candidate!r}",
            )
        return candidate

    def stage(self, racerids):
        """Write the files of the imported racers into the event's folder."""
        for racerid in racerids:
            for content, repository, relative in self.pending.pop(racerid, []):
                target = self.folder / repository.folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, Path):
                    # O_NOFOLLOW: a file swapped for a link since is refused.
                    fd = os.open(content, os.O_RDONLY | os.O_NOFOLLOW)
                    with open(fd, "rb") as f, target.open("wb") as out:
                        shutil.copyfileobj(f, out)
                else:
                    target.write_bytes(content)
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
            files = [(False, photo.original), (True, photo.displayed)]
        else:
            files = [(False, photo.displayed)]
        return _relative(
            photo.original is not None, self._place(repository, name, files)
        )

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
            has_original = self._is_file(root, name)
            if cropped and self._is_file(root, CROPPED, name):
                files = [(True, crop)]
                if has_original:
                    files.append((False, original))
                return _relative(True, self._place(repository, name, files))
            if has_original:
                name = self._place(repository, name, [(False, original)])
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


def _fit(stem, suffix, extension):
    """`stem + suffix + extension`, with the stem cut so the name fits a file
    system (255 bytes) and a `Racer` path under `0-cropped/` (255 characters).
    """
    if len(extension) > 16:
        stem, extension = stem + extension, ""
    name = stem + suffix + extension
    while len(name) > MAX_NAME_LENGTH or len(name.encode()) > 255:
        stem = stem[:-1]
        name = stem + suffix + extension
    return name


def _relative(cropped, name):
    return f"{CROPPED}/{name}" if cropped else name
