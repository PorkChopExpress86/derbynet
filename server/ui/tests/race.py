"""A small race for the dashboard tests (#41): racers in groups, passed,
and round 1 of each group, through the roster and racing services."""

from core.models import RaceSettings, RaceState
from racing.models import HeatLane, Round
from racing.services import heats, results, schedule
from roster.models import Racer
from roster.services.racers import insert_new_racer, pass_racer


def set_lanes(event, n: int = 4) -> None:
    RaceSettings.objects.filter(event=event).update(lane_count=n)


def add_racers(event, group: str, n: int, first_car: int, passed=True) -> list[Racer]:
    made = []
    for i in range(n):
        racer = insert_new_racer(
            event,
            first_name=f"{group}{i + 1}",
            last_name="Racer",
            car_number=first_car + i,
            partition_name=group,
        ).racer
        if passed:
            pass_racer(racer, True)
        made.append(racer)
    return made


def round_of(event, group: str, number: int = 1) -> Round:
    return Round.objects.get(event=event, group__name=group, number=number)


def race_setup(event, groups=(("Wolves", 8),), lanes: int = 4) -> dict[str, Round]:
    """Groups of passed racers, car numbers 101 up for the first group, 201
    up for the second, and so on. Round 1 of each group, not scheduled."""
    set_lanes(event, lanes)
    for i, (name, n) in enumerate(groups, 1):
        add_racers(event, name, n, 100 * i + 1)
    return {name: round_of(event, name) for name, _ in groups}


def scheduled(round: Round, runs: int = 1, race: bool = True) -> Round:
    schedule.generate_schedule(round, runs)
    if race:
        heats.select_heat(round.event, round_id=round.pk, heat=1, now_racing=True)
    return round


def heat_cars(round: Round, heat: int) -> list[int | None]:
    """The car numbers of a heat by lane, None for an empty lane."""
    lanes = RaceSettings.objects.get(event=round.event).lane_count
    rows = dict(
        HeatLane.objects.filter(round=round, heat=heat).values_list(
            "lane", "racer__car_number"
        )
    )
    return [rows.get(lane) for lane in range(1, lanes + 1)]


def chart(round: Round) -> list[list[int | None]]:
    n = max(HeatLane.objects.filter(round=round).values_list("heat", flat=True))
    return [heat_cars(round, h) for h in range(1, n + 1)]


def car_times(event) -> list[str]:
    """Times for the current heat's lanes that depend on the car alone, so
    the lower car number is always faster and no two racers tie."""
    state = RaceState.objects.get(event=event)
    rows = HeatLane.objects.filter(
        round_id=state.current_round_id, heat=state.current_heat
    ).values_list("lane", "racer__car_number")
    return [(f"lane{lane}", f"{2 + car / 1000:.3f}") for lane, car in rows]


def run_heat(event, times=None) -> None:
    """A result for the current heat, as the race coordinator types it:
    `times` by lane, else `car_times`."""
    if times is None:
        fields = car_times(event)
    else:
        fields = [(f"lane{i}", str(t)) for i, t in enumerate(times, 1)]
    recorded = results.record_manual_result(event, fields)
    assert recorded.failure is None, recorded.failure


def current(event) -> tuple[int | None, int | None, bool]:
    state = RaceState.objects.get(event=event)
    return state.current_round_id, state.current_heat, state.racing
