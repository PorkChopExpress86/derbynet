"""The folder an import writes its event's files into, and crash recovery.

An import writes the new event's files into a new folder,
`events/<name>.import-<token>`, and points the new event's `RaceSettings`
directories at it in the transaction that writes the rows. The commit is the
switch. A process that dies before it leaves the old event with its own
folders. One that dies after it leaves the new event with the new folder.

What a dead import leaves behind, `recover()` removes at the start of the
next import:

- An import folder still marked `.pending` that no event points at. The
  import died before its commit, and the folder holds only copies.
- The old event's folders, photos, videos and logs alike, that a committed
  `--replace` import listed in its `.replaced` note before the commit. Each
  goes only when no event points at it any more. The new event starts with
  empty `videos/` and `logs/`, as its rows start from the source.

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
from pathlib import Path

from django.utils.text import slugify

from core.models import RaceSettings

MARK = ".import-"
PENDING = ".pending"
REPLACED = ".replaced"
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

    def note_replaced(self, old_directories):
        """Record the replaced event's folders, for removal after the commit.

        Only folders of the usual shape, `events/<folder>/<subfolder>`, are
        recorded. Any other directory an event was set to is left alone.
        """
        replaced = sorted(
            {
                f"{path.parent.name}/{path.name}"
                for path in map(Path, old_directories)
                if path.name in FOLDERS.values()
                and path.parent.parent == self.events_root
            }
        )
        (self.path / REPLACED).write_text(json.dumps(replaced))

    def finish(self):
        """After the commit: drop the pending mark and the replaced folders.
        Returns the folders removed."""
        (self.path / PENDING).unlink()
        removed = _remove_replaced(self.path)
        os.close(self._lock)
        return removed

    def discard(self):
        """Before or instead of the commit: remove everything this wrote."""
        shutil.rmtree(self.path, ignore_errors=True)
        os.close(self._lock)


def recover(events_root):
    """Remove what dead imports left under `events_root`. Returns the paths
    removed."""
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
            if any(parent == folder.name for parent, _ in used):
                if (folder / PENDING).exists():
                    (folder / PENDING).unlink()
                removed += _remove_replaced(folder)
            elif (folder / PENDING).exists():
                shutil.rmtree(folder)
                removed.append(folder)
        finally:
            os.close(lock)
    return removed


def _used_folders():
    """`(folder, subfolder)` names of every directory an event points at."""
    used = set()
    for directories in RaceSettings.objects.values_list(*FOLDERS):
        for directory in directories:
            path = Path(directory)
            used.add((path.parent.name, path.name))
    return used


def _remove_replaced(folder):
    note = folder / REPLACED
    if not note.exists():
        return []
    used = _used_folders()
    removed = []
    for entry in json.loads(note.read_text()):
        parent, name = entry.split("/")
        old = folder.parent / parent / name
        if (parent, name) in used or old.is_symlink() or not old.is_dir():
            continue
        shutil.rmtree(old)
        removed.append(old)
        try:
            old.parent.rmdir()
        except OSError:
            pass  # It holds other files.
    note.unlink()
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
