"""The folder an import writes its event's files into, and crash recovery.

An import writes the new event's files into a new folder,
`events/<name>.import-<token>`, and points the new event's `RaceSettings`
directories at it in the transaction that writes the rows. The commit is the
switch. A process that dies before it leaves the old event with its own
folders. One that dies after it leaves the new event with the new folder.

A replace that keeps the old event's folders (`snapshot.put`, #134) then
brings the new files to the old paths, one folder at a time. A folder is
swapped when it is the event's own, `events/<folder>/<subfolder>`, a real
folder that no other event points at:

1. Link (or copy) its files into `.switch/<subfolder>` of the import folder.
2. Rename the old folder to `events/.removing-<token>`. No event points at it
   since the commit.
3. Rename the linked folder to the old path, and delete the old files.

Any other folder (outside `events/`, under a `DATA_DIR` the server no longer
uses, a symlink, or one another event shares) keeps its files: each new file
is linked or copied to a temporary name inside it, then renamed over the
file of the same name. The videos and logs folders are always kept this
way, so a restore never deletes a recording. The import brings no videos or
logs, so nothing is added to them.

Once every folder is done, one update points the new event at the old paths,
and the import folder is removed. The event points at the import folder
until that update and at the old paths after it, and both hold every file,
so a process that dies at any step leaves the event's settings naming
folders that exist. The steps before the update may be run again from the
start, because the import folder keeps its files until then.

What a dead import leaves behind, `recover()` removes at the start of the
next import:

- An import folder still marked `.pending` that no event points at. The
  import died before its commit, and the folder holds only copies.
- The old event's folders, photos, videos and logs alike, that a committed
  `--replace` import listed in its `.replaced` note before the commit. Each
  goes only when no event points at it any more. The new event starts with
  empty `videos/` and `logs/`, as its rows start from the source.
- The rest of a committed import's move to the old paths, which `recover()`
  finishes from the `.replaced` note.
- A `.removing-<token>` folder that no event points at. A removal renames the
  folder to that name first, because `rmtree` may delete the `.pending` mark
  before the rest, and a folder without the mark would never be cleaned up.

Each import holds an exclusive `flock` on its folder until it finishes, so
`recover()` skips an import that is still running. Folders are matched to
`RaceSettings` by name, not by absolute path, so a moved `DATA_DIR` makes
`recover()` keep folders, never remove them.
"""

import fcntl
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import NamedTuple

from django.utils.text import slugify

from core.models import RaceSettings

MARK = ".import-"
REMOVING = ".removing-"
PENDING = ".pending"
REPLACED = ".replaced"
SWITCH = ".switch"
PLACING = ".placing-"
# RaceSettings field -> the subfolder it names.
FOLDERS = {
    "photo_directory": "racers",
    "car_photo_directory": "cars",
    "video_directory": "videos",
    "logs_directory": "logs",
}
# Never swapped, so their files are never deleted by a restore.
RECORDINGS = {"video_directory", "logs_directory"}


class FolderName(NamedTuple):
    """A directory as `<folder>/<subfolder>`, the names `recover()` matches
    `RaceSettings` by."""

    parent: str
    name: str

    @classmethod
    def of(cls, path):
        path = Path(path)
        return cls(path.parent.name, path.name)

    @classmethod
    def parse(cls, text):
        parent, name = text.split("/")
        return cls(parent, name)

    def __str__(self):
        return f"{self.parent}/{self.name}"


class Kept(NamedTuple):
    """An old event's directory that the new event keeps."""

    path: str
    swap: bool


class Note(NamedTuple):
    """The `.replaced` note: what to do with the old event's folders after
    the commit."""

    event_id: int | None
    kept: dict[str, Kept]  # By RaceSettings field.
    remove: list[FolderName]

    def dumps(self):
        return json.dumps(
            {
                "event": self.event_id,
                "kept": {f: k._asdict() for f, k in self.kept.items()},
                "remove": [str(name) for name in self.remove],
            }
        )

    @classmethod
    def loads(cls, text):
        data = json.loads(text)
        if isinstance(data, list):  # The remove list of an older import.
            return cls(None, {}, [FolderName.parse(e) for e in data])
        return cls(
            data["event"],
            {f: Kept(**k) for f, k in data["kept"].items()},
            [FolderName.parse(e) for e in data["remove"]],
        )


class ImportFolder:
    """A new import's folder, locked and marked pending until `finish()`."""

    def __init__(self, events_root, name):
        self.events_root = Path(events_root)
        self.events_root.mkdir(parents=True, exist_ok=True)
        prefix = f"{slugify(name) or 'event'}{MARK}"
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=self.events_root))
        self._lock = _lock(self.path, wait=True)
        (self.path / PENDING).touch()
        for folder in FOLDERS.values():
            (self.path / folder).mkdir()

    def point(self, event):
        """Point `event`'s directories at this folder."""
        RaceSettings.objects.filter(event=event).update(
            **{field: str(self.path / folder) for field, folder in FOLDERS.items()}
        )

    def note_replaced(self, event, old_directories, *, keep=False):
        """Record what happens to the replaced event's folders after the
        commit. `event` is the new event, and `old_directories` maps each
        `FOLDERS` field to the old event's path.

        By default, the old folders of the usual shape,
        `events/<folder>/<subfolder>`, are removed, and any other directory is
        left alone. With `keep`, `event` takes every old path, and each is
        marked for a swap or for keeping its files.
        """
        if keep:
            used = _used_folders()  # The old event is gone; the new one is here.
            kept = {
                field: Kept(
                    path,
                    swap=field not in RECORDINGS
                    and _swappable(field, Path(path), self.events_root)
                    and FolderName.of(path) not in used,
                )
                for field, path in old_directories.items()
            }
            remove = []
        else:
            kept = {}
            remove = sorted(
                {
                    FolderName.of(path)
                    for path in old_directories.values()
                    if _usual(Path(path), self.events_root)
                }
            )
        note = Note(event.pk, kept, remove)
        (self.path / REPLACED).write_text(note.dumps())

    def finish(self):
        """After the commit: drop the pending mark, then bring the files to
        the kept folders or remove the replaced ones. Returns the folders
        removed."""
        (self.path / PENDING).unlink()
        removed = _finish_replace(self.path)
        os.close(self._lock)
        return removed

    def discard(self):
        """Before or instead of the commit: remove everything this wrote."""
        _remove_folder(self.path, ignore_errors=True)
        os.close(self._lock)


def recover(events_root):
    """Remove what dead imports left under `events_root`, and finish the
    moves to kept folders they started. Returns the paths removed."""
    events_root = Path(events_root)
    if not events_root.is_dir():
        return []
    removed = []
    for folder in sorted(events_root.glob(f"*{MARK}*")):
        if folder.is_symlink() or not folder.is_dir():
            continue
        lock = _lock(folder, wait=False)
        if lock is None:
            continue  # A running import.
        try:
            # Read after the lock, so an import that committed and finished
            # since is seen.
            used = _used_folders()
            if _points_at(folder, used) or _repointed(folder, used):
                if (folder / PENDING).exists():
                    (folder / PENDING).unlink()
                removed += _finish_replace(folder)
            elif (folder / PENDING).exists():
                _remove_folder(folder)
                removed.append(folder)
        finally:
            os.close(lock)
    used = _used_folders()
    for folder in sorted(events_root.glob(f"{REMOVING}*")):
        if not folder.is_symlink() and folder.is_dir() and not _points_at(folder, used):
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder)
    return removed


def _usual(path, events_root):
    return path.name in FOLDERS.values() and path.parent.parent == events_root


def _swappable(field, path, events_root):
    return (
        _usual(path, events_root)
        and path.name == FOLDERS[field]
        and not path.is_symlink()
    )


def _points_at(folder, used):
    """True when an event points at a subfolder of `folder`."""
    return any(name.parent == folder.name for name in used)


def _read_note(folder):
    note = folder / REPLACED
    return Note.loads(note.read_text()) if note.exists() else None


def _repointed(folder, used):
    """True when a committed import pointed its event at every kept folder,
    but died before it removed its own folder."""
    note = None if (folder / PENDING).exists() else _read_note(folder)
    if note is None or not note.kept:
        return False
    return all(FolderName.of(k.path) in used for k in note.kept.values())


def _finish_replace(folder):
    """Carry out the `.replaced` note of the committed import in `folder`.
    Safe to run again after a crash at any step. Returns the folders
    removed."""
    note = _read_note(folder)
    if note is None:
        return []
    if note.kept:
        _keep_folders(folder, note)
    removed = _remove_replaced(folder.parent, note.remove)
    if note.kept:
        if not _points_at(folder, _used_folders()):
            _remove_folder(folder)
        return removed
    (folder / REPLACED).unlink()
    return removed


def _keep_folders(folder, note):
    """Bring the new files to each kept folder the event does not point at
    yet, then point the event at all of them in one update."""
    row = RaceSettings.objects.filter(event_id=note.event_id).first()
    if row is None:
        return  # The event was deleted since.
    still_here = {
        field: kept
        for field, kept in note.kept.items()
        if getattr(row, field) == str(folder / FOLDERS[field])
    }
    for field, kept in still_here.items():
        _keep_one(folder, field, kept)
    _repoint(note.event_id, still_here)


def _keep_one(folder, field, kept):
    """Bring the files of `field`'s subfolder of `folder` to `kept.path`."""
    if not kept.path:
        return  # The old event had no folder here, and keeps none.
    source = folder / FOLDERS[field]
    target = Path(kept.path)
    if kept.swap and _swappable(field, target, folder.parent):
        linked = folder / SWITCH / FOLDERS[field]
        _link_copy(source, linked)
        aside = _set_aside(target, folder.parent)
        _move_in(linked, target)
        if aside is not None:
            shutil.rmtree(aside, ignore_errors=True)
    else:
        _merge(source, target)


def _link_copy(source, target):
    """`source` as a new folder `target`, its files hard links where the file
    system allows them."""
    if target.exists():
        shutil.rmtree(target)  # Left by a crash.
    target.parent.mkdir(exist_ok=True)
    shutil.copytree(source, target, symlinks=True, copy_function=_link_or_copy)


def _link_or_copy(source, target):
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError:
        shutil.copy2(source, target, follow_symlinks=False)


def _set_aside(path, events_root):
    """Rename the folder at `path` to a `.removing-<token>` folder, and return
    that, or None when there is no folder at `path`."""
    if not path.exists():
        return None
    return _rename_to_removing(path, events_root)


def _move_in(linked, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    linked.rename(path)


def _merge(source, target):
    """Link or copy every file of `source` into `target`, over any file of the
    same name, and leave its other files alone."""
    target.mkdir(parents=True, exist_ok=True)
    for leftover in target.rglob(f".*{PLACING}*"):
        leftover.unlink()  # A temporary file a crash left.
    for path in sorted(source.rglob("*")):
        destination = target / path.relative_to(source)
        if path.is_dir() and not path.is_symlink():
            destination.mkdir(exist_ok=True)
        else:
            _place_file(path, destination)


def _place_file(source, destination):
    """Link or copy `source` to a temporary name beside `destination`, then
    rename it over `destination`, so the file is never seen half written."""
    if destination.exists() and os.path.samefile(source, destination):
        # Linked by a run a crash stopped. A rename between two links of one
        # file does nothing, and would leave the temporary name behind.
        return
    temporary = destination.with_name(f".{destination.name}{PLACING}{uuid.uuid4().hex}")
    _link_or_copy(source, temporary)
    os.replace(temporary, destination)


def _repoint(event_id, directories):
    """Point the event at `directories`, by RaceSettings field."""
    if directories:
        RaceSettings.objects.filter(event_id=event_id).update(
            **{field: kept.path for field, kept in directories.items()}
        )


def _rename_to_removing(path, events_root):
    """Rename `path` to a new `.removing-<token>` folder of `events_root`,
    which `recover()` deletes if a crash stops the delete, and return it."""
    doomed = Path(events_root) / f"{REMOVING}{uuid.uuid4().hex}"
    path.rename(doomed)
    return doomed


def _remove_folder(folder, *, ignore_errors=False):
    """Rename `folder` to `.removing-<token>`, then delete it. A crash partway
    through the delete leaves a name `recover()` cleans up."""
    try:
        doomed = _rename_to_removing(folder, folder.parent)
    except OSError:
        if not ignore_errors:
            raise
        doomed = folder
    shutil.rmtree(doomed, ignore_errors=ignore_errors)


def _used_folders():
    """The `FolderName` of every directory an event points at."""
    return {
        FolderName.of(directory)
        for directories in RaceSettings.objects.values_list(*FOLDERS)
        for directory in directories
    }


def _remove_replaced(events_root, names):
    """Remove each folder of `names` that no event points at."""
    used = _used_folders()
    removed = []
    for name in names:
        old = events_root / name.parent / name.name
        if name in used or old.is_symlink() or not old.is_dir():
            continue
        shutil.rmtree(old)
        removed.append(old)
        try:
            old.parent.rmdir()
        except OSError:
            pass  # It holds other files.
    return removed


def _lock(folder, *, wait):
    """An exclusive lock on `folder`, or None when another process holds it
    and `wait` is false."""
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd
