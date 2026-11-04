"""Results by racer (#49): `racer-results.php`'s query, as rounds of racers
with the heats each ran.

PHP lists every round in `all_rounds()` order, and in each round the racers
of its roster who have a heat in its chart, by last name, first name and car
number, each heat in result order. A heat shows the time with
`finish-formatting`, or the place under points racing, or "--" before it
has a result. A time of `DID_NOT_FINISH` shows "DNF" beside the stored value
(D42). `racer_id` keeps one racer; PHP puts the raw `racerid` into the SQL
(S13), so the caller passes an integer or nothing.

The whole event takes a fixed number of queries, however many rounds it has.
"""

from dataclasses import dataclass

from django.db.models import Exists, OuterRef

from core.models import Event, RaceSettings, RaceState
from racing.engine.results import DID_NOT_FINISH
from racing.engine.standings.ordinals import ordinal
from racing.models import HeatLane, RosterEntry, Round
from racing.services.polls import format_time, mangled_name
from roster.services import groups
from roster.services.racers import CAR_PHOTOS, HEADSHOTS, photo_url

# `RENDER_RACER_RESULTS` in `website/inc/photo-config.inc`.
RENDER_RACER_RESULTS = "200x200"


@dataclass(frozen=True)
class Heat:
    heat: int
    lane: int
    result_id: int
    # The time with `finish-formatting`, the place, or "--".
    shown: str
    dnf: bool = False


@dataclass(frozen=True)
class RacerRound:
    racer_id: int
    name: str
    car_number: int
    headshot: str
    car_photo: str
    heats: tuple[Heat, ...]


@dataclass(frozen=True)
class ResultsRound:
    round_id: int
    group_name: str
    number: int
    racers: tuple[RacerRound, ...]


@dataclass(frozen=True)
class ResultsByRacer:
    rounds: tuple[ResultsRound, ...]
    use_points: bool
    use_groups: bool
    current_round_id: int | None
    lane_count: int


def heat_text(settings: RaceSettings, finish_time, finish_place) -> tuple[str, bool]:
    """How a heat's result reads: the place under points racing, else the
    time with `finish-formatting`, or "--" with neither; and whether it is a
    time of `DID_NOT_FINISH` (D42), which a page marks "DNF"."""
    if settings.use_points:
        return (ordinal(finish_place) if finish_place else "--"), False
    if finish_time is None:
        return "--", False
    shown = format_time(settings.finish_formatting, finish_time)
    return shown, finish_time >= DID_NOT_FINISH


def results_by_racer(event: Event, racer_id: int | None = None) -> ResultsByRacer:
    settings = RaceSettings.objects.get(event=event)
    rounds = list(
        Round.objects.filter(event=event)
        .select_related("group")
        .order_by("group__sort_order", "group__name", "number", "id")
    )
    entered = RosterEntry.objects.filter(
        round=OuterRef("round"), racer=OuterRef("racer")
    )
    rows = (
        HeatLane.objects.filter(event=event)
        .filter(Exists(entered))
        .select_related("racer")
        .order_by(
            "round_id",
            "racer__last_name",
            "racer__first_name",
            "racer__car_number",
            "racer_id",
            "id",
            "lane",
        )
    )
    if racer_id is not None:
        rows = rows.filter(racer_id=racer_id)
    by_round: dict[int, dict[int, list]] = {}
    racers = {}
    for row in rows:
        racers[row.racer_id] = row.racer
        shown, dnf = heat_text(settings, row.finish_time, row.finish_place)
        by_round.setdefault(row.round_id, {}).setdefault(row.racer_id, []).append(
            Heat(row.heat, row.lane, row.pk, shown, dnf)
        )
    out = []
    for round in rounds:
        cards = []
        for rid, heats in by_round.get(round.id, {}).items():
            racer = racers[rid]
            cards.append(
                RacerRound(
                    racer_id=rid,
                    name=mangled_name(
                        racer.first_name, racer.last_name, settings.name_style
                    ),
                    car_number=racer.car_number,
                    headshot=photo_url(
                        HEADSHOTS,
                        settings.photo_directory,
                        racer.headshot,
                        RENDER_RACER_RESULTS,
                    )
                    if settings.show_racer_photos_rr
                    else "",
                    car_photo=photo_url(
                        CAR_PHOTOS,
                        settings.car_photo_directory,
                        racer.car_photo,
                        RENDER_RACER_RESULTS,
                    )
                    if settings.show_car_photos_rr
                    else "",
                    heats=tuple(heats),
                )
            )
        out.append(ResultsRound(round.id, round.group.name, round.number, tuple(cards)))
    return ResultsByRacer(
        rounds=tuple(out),
        use_points=settings.use_points,
        use_groups=groups.use_groups(event),
        current_round_id=RaceState.objects.get(event=event).current_round_id,
        lane_count=settings.lane_count,
    )
