"""Time each eventapp migration step on a 1000-racer event at 0001. Diagnostic for migbench."""
import json
import time

import spike.bootstrap  # noqa: F401
from django.conf import settings
from django.core.management import call_command

from spike import events_a

from spike.migbench import seed_at_0001
n = 1000
for rep in range(3):
    slug = f"step{rep}"
    events_a.drop_database(slug)
    events_a.open_event(slug, target="0001")
    with events_a.event_alias(slug) as alias:
        seed_at_0001(alias, slug, n)
        steps = {}
        for target in ("0002", "0003", "0004", "0005"):
            t0 = time.perf_counter()
            call_command("migrate", "eventapp", target, database=alias, verbosity=0)
            steps[target] = round(1000 * (time.perf_counter() - t0), 1)
    print(json.dumps({"db": settings.DB, "rep": rep, "ms_per_step": steps}))
    events_a.drop_database(slug)
