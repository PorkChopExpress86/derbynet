"""The folder an import writes its event's files into, and crash recovery.

An import writes the new event's files into a new folder,
`events/<name>.import-<token>`, and points the new event's `RaceSettings`
directories at it in the transaction that writes the rows. The commit is the
switch. A process that dies before it leaves the old event with its own
folders. One that dies after it leaves the new event with the new folder.

A replace that keeps the old event's folders (`snapshot.put`, #134) then
moves the new files to the old paths, one folder at a time:

1. Link (or copy) the folder's files into `.switch/<subfolder>` of the import
   folder.
2. Rename the old folder to `events/.removing-<token>`. No event points at it
   since the commit.
3. Rename the linked folder to the old path, and delete the old files.
4. Once every folder is moved, point the new event at the old paths, in one
   update, and remove the import folder.

The event points at the import folder until step 4 and at the old paths
after it, and both hold every file, so a process that dies at any step
leaves the event's settings naming folders that exist. Steps 1 to 3 may be
run again from the start: the import folder keeps its files until step 4.
Folders that are not of the usual shape, `events/<folder>/<subfolder>`, or
that another event points at, keep the import folder's path instead.

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

from django.utils.text import slugify

from core.models import RaceSettings

MARK = ".import-"
REMOVING = ".removing-"
PENDING = ".pending"
REPLACED = ".replaced"
SWITCH = ".switch"
# RaceSettings field -> the subfolder it names.
FOLDERS = {
    "photo_directory": "racers",
    "car_photo_directory": "cars",
    "video_directory": "videos",
    "logs_directory": "logs",
}


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

    def note_replaced(self, old_directories, *, keep=False):
        """Record what happens to the replaced event's folders after the
        commit. `old_directories` maps each `FOLDERS` field to its path.

        By default the folders are removed. With `keep`, the new event's
        files move to them and the new event points at them. Either way only
        folders of the usual shape, `events/<folder>/<subfolder>`, are
        recorded, and with `keep` only those no other event points at. Any
        other directory an event was set to is left alone.
        """
        usual = {
            field: path
            for field, path in old_directories.items()
            if _usual(Path(path), self.events_root)
        }
        if keep:
            used = _used_folders()
            switch = {
                field: path
                for field, path in usual.items()
                if Path(path).name == FOLDERS[field]
                and not Path(path).is_symlink()
                and (Path(path).parent.name, Path(path).name) not in used
            }
            remove = []
        else:
            switch = {}
            remove = sorted({_name(Path(path)) for path in usual.values()})
        (self.path / REPLACED).write_text(
            json.dumps({"switch": switch, "remove": remove})
        )

    def finish(self):
        """After the commit: drop the pending mark, then move the files to
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
            if any(parent == folder.name for parent, _ in used) or _switched(
                folder, used
            ):
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
        if (
            not folder.is_symlink()
            and folder.is_dir()
            and not any(parent == folder.name for parent, _ in used)
        ):
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder)
    return removed


def _usual(path, events_root):
    return path.name in FOLDERS.values() and path.parent.parent == events_root


def _name(path):
    return f"{path.parent.name}/{path.name}"


def _read_note(folder):
    """The `.replaced` note as `(switch, remove)`, or None when there is none.
    A note that is a plain list is the remove list of an older import."""
    note = folder / REPLACED
    if not note.exists():
        return None
    plan = json.loads(note.read_text())
    if isinstance(plan, list):
        return {}, plan
    return plan["switch"], plan["remove"]


def _switched(folder, used):
    """True when a committed import pointed its event at every kept folder,
    but died before it removed its own folder."""
    plan = None if (folder / PENDING).exists() else _read_note(folder)
    if not plan or not plan[0]:
        return False
    return all(
        (Path(path).parent.name, Path(path).name) in used for path in plan[0].values()
    )


def _finish_replace(folder):
    """Carry out the `.replaced` note of the committed import in `folder`.
    Safe to run again after a crash at any step. Returns the folders
    removed."""
    plan = _read_note(folder)
    if plan is None:
        return []
    switch, remove = plan
    events_root = folder.parent
    switch = {
        field: path for field, path in switch.items() if _usual(Path(path), events_root)
    }
    if switch:
        used = _used_folders()
        moving = {
            field: path
            for field, path in switch.items()
            if (folder.name, FOLDERS[field]) in used
        }
        for field, path in moving.items():
            source = folder / FOLDERS[field]
            linked = folder / SWITCH / FOLDERS[field]
            _link_copy(source, linked)
            aside = _set_aside(Path(path), events_root)
            _move_in(linked, Path(path))
            if aside is not None:
                shutil.rmtree(aside, ignore_errors=True)
        _repoint(folder, moving)
    removed = _remove_replaced(events_root, remove)
    if switch:
        if any(parent == folder.name for parent, _ in _used_folders()):
            # Some folders kept this folder's path: drop only the moved ones.
            for field in switch:
                _discard(folder / FOLDERS[field], events_root)
            _discard(folder / SWITCH, events_root)
        else:
            _remove_folder(folder)
            return removed
    (folder / REPLACED).unlink()
    return removed


def _link_copy(source, target):
    """`source` as a new folder `target`, its files hard links where the file
    system allows them."""
    if target.exists():
        shutil.rmtree(target)  # Left by a crash.
    target.parent.mkdir(exist_ok=True)
    shutil.copytree(source, target, symlinks=True, copy_function=_link_or_copy)


def _link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _set_aside(path, events_root):
    """Rename the folder at `path` to a `.removing-<token>` folder, and return
    that, or None when there is nothing at `path`."""
    if not path.exists() and not path.is_symlink():
        return None
    aside = events_root / f"{REMOVING}{uuid.uuid4().hex}"
    path.rename(aside)
    if aside.is_symlink():
        aside.unlink()
        return None
    return aside


def _move_in(linked, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    linked.rename(path)


def _repoint(folder, moving):
    """Point the event that points at `folder` at the kept folders."""
    if moving:
        field = next(iter(moving))
        RaceSettings.objects.filter(**{field: str(folder / FOLDERS[field])}).update(
            **moving
        )


def _discard(path, events_root):
    if path.exists():
        aside = events_root / f"{REMOVING}{uuid.uuid4().hex}"
        path.rename(aside)
        shutil.rmtree(aside)


def _remove_folder(folder, *, ignore_errors=False):
    """Rename `folder` to `.removing-<token>`, then delete it. A crash partway
    through the delete leaves a name `recover()` cleans up."""
    doomed = folder.with_name(f"{REMOVING}{uuid.uuid4().hex}")
    try:
        folder.rename(doomed)
    except OSError:
        if not ignore_errors:
            raise
        doomed = folder
    shutil.rmtree(doomed, ignore_errors=ignore_errors)


def _used_folders():
    """`(folder, subfolder)` names of every directory an event points at."""
    used = set()
    for directories in RaceSettings.objects.values_list(*FOLDERS):
        for directory in directories:
            path = Path(directory)
            used.add((path.parent.name, path.name))
    return used


def _remove_replaced(events_root, entries):
    """Remove each `<folder>/<subfolder>` of `entries` that no event points
    at."""
    used = _used_folders()
    removed = []
    for entry in entries:
        parent, name = entry.split("/")
        old = events_root / parent / name
        if (parent, name) in used or old.is_symlink() or not old.is_dir():
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
