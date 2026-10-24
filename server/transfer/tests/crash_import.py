"""Run `import_derbynet` and kill the process with `os._exit(9)` at one step.

Usage: python -m transfer.tests.crash_import STEP DATABASE_NAME IMPORT_ARGUMENT...

`os._exit` skips every `finally` and `except` block, as SIGKILL, an OOM kill
or `docker stop` past its grace period do.
"""

import os
import shutil
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "derbynet.settings")
import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.db import connections  # noqa: E402

from transfer import importer  # noqa: E402
from transfer.legacy.folders import ImportFolder  # noqa: E402
from transfer.legacy.photos import Photos  # noqa: E402


def die(*args, **kwargs):
    os._exit(9)


def before(owner, name):
    setattr(owner, name, die)


def after(owner, name, calls=1):
    real = getattr(owner, name)
    count = 0

    def wrapped(*args, **kwargs):
        nonlocal count
        result = real(*args, **kwargs)
        count += 1
        if count == calls:
            die()
        return result

    setattr(owner, name, wrapped)


def in_finish(hook):
    """Set `hook` once `finish()` starts. Earlier steps call rmtree too."""
    real = ImportFolder.finish

    def wrapped(self):
        hook()
        return real(self)

    ImportFolder.finish = wrapped


STEPS = {
    "staged": lambda: after(Photos, "stage"),
    "pointed": lambda: after(ImportFolder, "point"),
    "ids-written": lambda: after(importer.LegacyId.objects, "bulk_create"),
    "checked": lambda: after(importer, "check_invariants"),
    "noted": lambda: after(ImportFolder, "note_replaced"),
    "committed": lambda: before(ImportFolder, "finish"),
    "removing": lambda: in_finish(lambda: before(shutil, "rmtree")),
    "removed-one": lambda: in_finish(lambda: after(shutil, "rmtree")),
    "finished": lambda: after(ImportFolder, "finish"),
}

if __name__ == "__main__":
    step, database, *arguments = sys.argv[1:]
    connections["default"].settings_dict["NAME"] = database
    STEPS[step]()
    call_command("import_derbynet", *arguments)
    sys.exit(f"the import finished without reaching step {step!r}")
