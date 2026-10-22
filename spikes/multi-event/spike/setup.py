"""Reset all spike data and create N seeded events. Usage: python -m spike.setup e1,e2,e3 [racers]"""
import shutil
import sys

import spike.bootstrap  # noqa: F401
from django.conf import settings
from django.core.management import call_command
from django.db import transaction

from spike import events_a


def reset_pg_database(name):
    import psycopg
    with psycopg.connect(host="127.0.0.1", port=settings.PG_PORT, user="postgres", password="spike",
                         dbname="postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')
        if settings.MODE == "a":
            for (ev,) in c.execute("SELECT datname FROM pg_database WHERE datname LIKE 'spike_ev_%'").fetchall():
                c.execute(f'DROP DATABASE "{ev}" WITH (FORCE)')


def seed(models, n_racers, slug, using, **scope):
    Racer, Heat, Result = models
    racers = Racer.objects.using(using).bulk_create(
        [Racer(name=f"{slug}-racer-{i}", car_number=100 + i, **scope) for i in range(n_racers)])
    heats = Heat.objects.using(using).bulk_create(
        [Heat(number=h + 1, status="running" if h == 0 else "scheduled", **scope) for h in range(n_racers)])
    Result.objects.using(using).bulk_create(
        [Result(heat=heat, racer=racers[(h * 4 + lane) % n_racers], lane=lane + 1, time=None, **scope)
         for h, heat in enumerate(heats) for lane in range(4)])


def setup_a(slugs, n_racers):
    from eventapp.models import EventInfo, Heat, Racer, Result, TimerState
    from siteapp.models import SiteState
    call_command("migrate", database="site", verbosity=0)
    for slug in slugs:
        events_a.drop_database(slug)
        events_a.open_event(slug)
        with events_a.event_alias(slug) as alias, transaction.atomic(using=alias):
            EventInfo.objects.using(alias).create(slug=slug)
            TimerState.objects.using(alias).create(pk=1)
            seed((Racer, Heat, Result), n_racers, slug, alias)
    SiteState.objects.create(pk=1, active_slug=slugs[0], version=1)


def setup_b(slugs, n_racers):
    from bapp.models import Event, Heat, Racer, Result, SiteState, TimerState
    call_command("migrate", verbosity=0)
    with transaction.atomic():
        for slug in slugs:
            ev = Event.objects.create(slug=slug)
            TimerState.objects.create(event=ev)
            seed((Racer, Heat, Result), n_racers, slug, "default", event=ev)
        SiteState.objects.create(pk=1, active_event=Event.objects.get(slug=slugs[0]))


def main():
    slugs = sys.argv[1].split(",")
    n_racers = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    if settings.DB == "pg":
        reset_pg_database("spike_site" if settings.MODE == "a" else "spike_b")
    else:
        shutil.rmtree(settings.DATA_DIR, ignore_errors=True)
        settings.DATA_DIR.mkdir(parents=True)
    (setup_a if settings.MODE == "a" else setup_b)(slugs, n_racers)
    print(f"setup mode={settings.MODE} db={settings.DB} events={slugs} racers={n_racers}")


main()
