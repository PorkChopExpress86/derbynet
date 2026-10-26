import random
from dataclasses import replace

from racing.engine.standings import oracle
from racing.engine.standings.eligibility import AwardSettings

RED, BLUE, BOTH = 1, 2, 3
SUBGROUPS = {RED: (11, 12), BLUE: (21, 22)}
ROUND_RED, ROUND_BLUE, ROUND_BOTH = 101, 102, 103


def random_event(seed: int, racers_per_group: int = 8, **settings) -> oracle.Event:
    rng = random.Random(seed)
    groups = (
        oracle.Group(RED, "Red", 1),
        oracle.Group(BLUE, "Blue", 2),
        oracle.Group(BOTH, "Both", 3, constituents=(RED, BLUE)),
    )
    subgroups = tuple(
        oracle.Subgroup(s, g, f"{name} {i}", i)
        for g, name in ((RED, "Red"), (BLUE, "Blue"))
        for i, s in enumerate(SUBGROUPS[g], 1)
    )
    racers = []
    for group in (RED, BLUE):
        for i in range(racers_per_group):
            racers.append(
                oracle.Racer(
                    id=group * 100 + i,
                    first_name=f"F{i}",
                    last_name=rng.choice(["Ames", "ames", "Bell", "Cole"]),
                    car_number=group * 100 + i,
                    car_name="",
                    group_id=group,
                    subgroup_id=SUBGROUPS[group][i % 2],
                )
            )
    results = []
    for round_id, group in ((ROUND_RED, RED), (ROUND_BLUE, BLUE)):
        for racer in (r for r in racers if r.group_id == group):
            for heat in range(3):
                results.append(
                    oracle.Result(
                        round_id,
                        racer.id,
                        rng.choice([3.0, 3.1, 3.2]) + rng.randrange(4) / 1000,
                        heat % 4 + 1,
                    )
                )
    finalists = rng.sample(racers, 6)
    for racer in finalists:
        for heat in range(2):
            results.append(
                oracle.Result(
                    ROUND_BOTH, racer.id, 3.0 + rng.randrange(30) / 100, heat + 1
                )
            )
    return oracle.Event(
        groups=groups,
        subgroups=subgroups,
        rounds=(
            oracle.Round(ROUND_RED, RED, 1, True),
            oracle.Round(ROUND_BLUE, BLUE, 1, True),
            oracle.Round(ROUND_BOTH, BOTH, 1, True),
        ),
        racers=tuple(racers),
        results=tuple(results),
        settings=oracle.Settings(lane_count=4, **settings),
    )


def with_awards(event: oracle.Event, settings: dict[int, AwardSettings]):
    return replace(
        event,
        racers=tuple(
            replace(r, awards=settings[r.id]) if r.id in settings else r
            for r in event.racers
        ),
    )


def without(event: oracle.Event, racer_ids) -> oracle.Event:
    return replace(
        event,
        racers=tuple(r for r in event.racers if r.id not in racer_ids),
        results=tuple(r for r in event.results if r.racer_id not in racer_ids),
    )
