from core.models import Event, RaceSettings
from racing.engine.standings.lane_bias import LaneBias, lane_bias_analysis
from racing.models import HAS_RESULT, HeatLane


def lane_bias(event: Event) -> LaneBias | None:
    """The test over every heat result of the event on lanes 1 to
    `lane_count`, leaving out times that are missing or 0, as PHP does."""
    nlanes = RaceSettings.objects.get(event=event).lane_count
    times_by_lane = [[] for _ in range(nlanes)]
    rows = (
        HeatLane.objects.filter(event=event, lane__range=(1, nlanes))
        .filter(HAS_RESULT, finish_time__isnull=False)
        .exclude(finish_time=0)
        .order_by("lane", "id")
        .values_list("lane", "finish_time")
    )
    for lane, finish_time in rows:
        times_by_lane[lane - 1].append(finish_time)
    return lane_bias_analysis(times_by_lane)
