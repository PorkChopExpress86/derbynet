"""Time `standings_snapshot()` and the speed awards on a 200-racer event
with 1200 results (#26, D29: measured on a laptop, not a Raspberry Pi).

    DATABASE_URL=sqlite:////tmp/standings-timing.sqlite3 \\
        uv run python manage.py migrate -v 0
    DATABASE_URL=sqlite:////tmp/standings-timing.sqlite3 \\
        uv run python -m tools.time_standings
"""

import os
import random
import statistics
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "derbynet.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from core.models import RaceSettings  # noqa: E402
from core.services import new_event  # noqa: E402
from racing.engine.standings.oracle import Standings  # noqa: E402
from racing.engine.standings.speed_awards import speed_awards  # noqa: E402
from racing.models import HeatLane, Round  # noqa: E402
from racing.services import standings  # noqa: E402
from roster.models import Racer, Subgroup  # noqa: E402
from roster.services import groups  # noqa: E402


def build_event():
    rng = random.Random(26)
    event = new_event(f"timing-{time.time_ns()}")
    RaceSettings.objects.filter(event=event).update(lane_count=6, use_subgroups=True)
    racers = []
    for g in range(5):
        partition = groups.find_or_create_partition(event.id, f"Den {g}")
        group = partition.subgroup.group
        second = groups.find_or_create_subgroup(group, f"Den {g} B")
        for i in range(40):
            subgroup = partition.subgroup if i % 2 else second
            racers.append(
                Racer(
                    event=event,
                    car_number=100 * (g + 1) + i,
                    first_name=f"First{i}",
                    last_name=rng.choice(["Ames", "Bell", "Cole", "Dunn"]),
                    group=group,
                    subgroup=subgroup,
                    partition=partition,
                    passed_inspection=True,
                )
            )
    Racer.objects.bulk_create(racers)
    groups.fill_in_missing_roster_entries(event.id)
    lanes = []
    now = timezone.now()
    for racer in Racer.objects.filter(event=event):
        round = Round.objects.get(group_id=racer.group_id, number=1)
        for heat in range(6):
            lanes.append(
                HeatLane(
                    event=event,
                    round=round,
                    heat=racer.car_number * 10 + heat,
                    lane=1,
                    racer=racer,
                    finish_time=3 + rng.randrange(1000) / 1000,
                    completed=now,
                )
            )
    HeatLane.objects.bulk_create(lanes)
    assert Racer.objects.filter(event=event).count() == 200
    assert HeatLane.objects.filter(event=event).count() == 1200
    assert (
        Subgroup.objects.filter(event=event, racers__isnull=False).distinct().count()
        == 10
    )
    return event


def timed(call, runs=20):
    seconds = []
    for _ in range(runs):
        start = time.perf_counter()
        call()
        seconds.append(time.perf_counter() - start)
    return statistics.median(seconds) * 1000, min(seconds) * 1000


def main():
    event = build_event()
    inputs = standings._read(event)
    snapshot = standings.standings_snapshot(event)
    print(f"{len(snapshot.speed_awards.awards)} speed awards")
    for name, call in (
        (
            "snapshot and speed awards",
            lambda: standings.standings_snapshot(event).speed_awards,
        ),
        ("reading the rows", lambda: standings._read(event)),
        (
            "computing from rows read once",
            lambda: speed_awards(Standings(inputs.event), inputs.trophies),
        ),
    ):
        median, best = timed(call)
        print(f"{name}: median {median:.1f} ms, best {best:.1f} ms")


if __name__ == "__main__":
    main()
