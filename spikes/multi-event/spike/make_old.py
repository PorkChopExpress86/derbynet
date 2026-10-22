"""Create option (a) events at migration 0001. Usage: python -m spike.make_old SLUG[,SLUG...] RACERS"""
import sys

import spike.bootstrap  # noqa: F401

from spike import events_a
from spike.migbench import seed_at_0001

for slug in sys.argv[1].split(","):
    events_a.drop_database(slug)
    events_a.open_event(slug, target="0001")
    with events_a.event_alias(slug) as alias:
        seed_at_0001(alias, slug, int(sys.argv[2]))
