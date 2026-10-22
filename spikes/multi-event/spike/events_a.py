"""Option (a) event lifecycle: open (create + migrate) and switch."""
import time
from contextlib import contextmanager

from django.conf import settings
from django.core.management import call_command
from django.db import connections, transaction

from spike.settings import event_db


def ensure_database(slug):
    name = event_db(slug)["NAME"]
    if settings.DB == "pg":
        with connections["site"].cursor() as c:
            c.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
            if c.fetchone() is None:
                c.execute(f'CREATE DATABASE "{name}"')
    else:
        from pathlib import Path
        Path(name).parent.mkdir(parents=True, exist_ok=True)


def drop_database(slug):
    name = event_db(slug)["NAME"]
    if settings.DB == "pg":
        with connections["site"].cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    else:
        from pathlib import Path
        for suffix in ("", "-wal", "-shm"):
            Path(name + suffix).unlink(missing_ok=True)


@contextmanager
def event_alias(slug):
    """A temporary connection alias for one event database, separate from `event`."""
    alias = f"event_open_{slug}"
    connections.settings[alias] = {**connections.settings["event"], "NAME": event_db(slug)["NAME"]}
    try:
        yield alias
    finally:
        if hasattr(connections._connections, alias):
            connections[alias].close()
            del connections[alias]
        del connections.settings[alias]


def open_event(slug, target=None):
    """Create the database if missing and migrate it. Returns seconds spent in migrate."""
    ensure_database(slug)
    with event_alias(slug) as alias:
        t0 = time.perf_counter()
        args = ["eventapp"] + ([target] if target else [])
        call_command("migrate", *args, database=alias, verbosity=0)
        return time.perf_counter() - t0


def switch(slug):
    from siteapp.models import SiteState

    migrate_s = open_event(slug)
    with transaction.atomic(using="site"):
        state = SiteState.objects.select_for_update().get(pk=1)
        previous = state.active_slug
        state.active_slug = slug
        state.version += 1
        state.save()
    return {"from": previous, "to": slug, "commit_ts": time.time(), "migrate_s": migrate_s}
