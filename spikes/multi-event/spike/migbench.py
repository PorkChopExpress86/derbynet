"""Option (a) migrate-on-open: build an event at 0001, then time `migrate` to head.

Usage: python -m spike.migbench SIZES REPS   (e.g. 100,1000 5). Prints one JSON line per run.
"""
import json
import sys
import time

import spike.bootstrap  # noqa: F401
from django.conf import settings
from django.db import connections, transaction

from spike import events_a


def seed_at_0001(alias, slug, n):
    with transaction.atomic(using=alias), connections[alias].cursor() as c:
        c.execute("INSERT INTO eventapp_eventinfo (slug) VALUES (%s)", [slug])
        c.execute("INSERT INTO eventapp_timerstate (last_contact) VALUES (0)")
        c.executemany("INSERT INTO eventapp_racer (name, car_number) VALUES (%s, %s)",
                      [(f"{slug}-racer-{i}", 100 + i) for i in range(n)])
        c.executemany("INSERT INTO eventapp_heat (number, status) VALUES (%s, %s)",
                      [(h + 1, "scheduled") for h in range(n)])
        c.execute("SELECT min(id) FROM eventapp_racer")
        r0 = c.fetchone()[0]
        c.execute("SELECT min(id) FROM eventapp_heat")
        h0 = c.fetchone()[0]
        c.executemany("INSERT INTO eventapp_result (heat_id, racer_id, lane, time) VALUES (%s, %s, %s, %s)",
                      [(h0 + h, r0 + (h * 4 + lane) % n, lane + 1, 3.0) for h in range(n) for lane in range(4)])


def main():
    sizes = [int(s) for s in sys.argv[1].split(",")]
    reps = int(sys.argv[2])
    for rep in range(reps):
        for n in sizes:
            slug = f"old{n}r{rep}"
            events_a.drop_database(slug)
            t0 = time.perf_counter()
            events_a.ensure_database(slug)
            create = time.perf_counter() - t0
            events_a.open_event(slug, target="0001")
            with events_a.event_alias(slug) as alias:
                seed_at_0001(alias, slug, n)
            from_0001 = events_a.open_event(slug)
            noop = events_a.open_event(slug)
            with events_a.event_alias(slug) as alias, connections[alias].cursor() as c:
                c.execute("SELECT count(*) FROM eventapp_racer WHERE note = %s", ["car 100"])
                filled = c.fetchone()[0]
            print(json.dumps({"db": settings.DB, "racers": n, "results": 4 * n, "rep": rep,
                              "create_db_s": round(create, 4),
                              "migrate_0001_to_head_s": round(from_0001, 4), "migrate_noop_s": round(noop, 4),
                              "data_migration_ran": filled == 1}), flush=True)
            events_a.drop_database(slug)
            fresh = f"new{n}r{rep}"
            events_a.drop_database(fresh)
            t0 = time.perf_counter()
            events_a.open_event(fresh)
            print(json.dumps({"db": settings.DB, "rep": rep, "new_event_create_and_migrate_s":
                              round(time.perf_counter() - t0, 4)}), flush=True)
            events_a.drop_database(fresh)


if __name__ == "__main__":
    main()
