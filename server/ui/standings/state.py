"""What the standings page shows (#49): one presentation of the standings
catalog as a ranked list, read from `racing.services.standings`.

The places, the ladders and who is not eligible are the oracle's
(`standings_snapshot()`). A racer not eligible for a ladder's speed awards
(D33), or whose partition the ladder's award list leaves out (D35), is
listed in score order among the others, marked, with no place: the oracle
numbers the rest as if that racer were absent. Each card shows the racer's
heats in the round, with a time dropped by a closed lane struck through
beside the times that count (D37, #108). A time of `DID_NOT_FINISH` shows
"DNF" (D42). Car numbers are padded to three digits (D47).
"""

from dataclasses import dataclass

from core.models import RaceSettings
from racing.engine.results import DID_NOT_FINISH
from racing.engine.standings.ordinals import ordinal
from racing.services.polls import format_time, mangled_name
from racing.services.standings import standings_snapshot

# The heading of `standings.php`, by points or times, then by `scoring`.
SCORING = {
    (True, 0): "Scoring by points",
    (True, 1): "Scoring by points, dropping each racer's worst heat",
    (True, 2): "Scoring by points, best heat",
    (False, 0): "Averaging all heat times",
    (False, 1): "Dropping each racer's worst heat",
    (False, 2): "Selecting best heat",
}


@dataclass(frozen=True)
class Time:
    heat: int
    lane: int
    shown: str
    dropped: bool
    dnf: bool


@dataclass(frozen=True)
class Card:
    racer_id: int
    place: str
    car_number: int
    name: str
    car_name: str
    group: str
    subgroup: str
    not_eligible: str
    heats: int
    score: str
    best: str
    worst: str
    times: tuple[Time, ...]


@dataclass(frozen=True)
class Standings:
    heading: str
    catalog: tuple[tuple[str, str], ...]
    selected: str
    title: str
    cards: tuple[Card, ...]
    use_points: bool
    use_groups: bool
    use_subgroups: bool
    score_label: str


def _time(settings: RaceSettings, value) -> str:
    if value is None:
        return "--"
    return format_time(settings.finish_formatting, value)


def build(event, view: str = "") -> Standings:
    settings = RaceSettings.objects.get(event=event)
    snapshot = standings_snapshot(event)
    standings = snapshot.standings
    catalog = standings.catalog()
    keys = [e.key for e in catalog]
    selected = view if view in keys else (keys[0] if keys else "")
    title = next((e.name for e in catalog if e.key == selected), "")
    points = settings.use_points
    cards = []
    for row in standings.rows if selected else ():
        if selected in row.outcome:
            place, why = str(row.outcome[selected]), ""
        elif selected in row.not_eligible:
            place, why = "", str(row.not_eligible[selected])
        else:
            continue
        racer = row.racer
        summary = row.summary
        times = tuple(
            Time(
                h.heat,
                h.lane,
                (ordinal(h.finish_place) if h.finish_place else "--")
                if points
                else _time(settings, h.finish_time),
                h.dropped,
                not points
                and h.finish_time is not None
                and h.finish_time >= DID_NOT_FINISH,
            )
            for h in snapshot.heat_times(row.round.id, racer.id)
        )
        cards.append(
            Card(
                racer_id=racer.id,
                place=place,
                car_number=racer.car_number,
                name=mangled_name(
                    racer.first_name, racer.last_name, settings.name_style
                ),
                car_name=racer.car_name,
                group=standings.groups[racer.group_id].name,
                subgroup=standings.subgroups[racer.subgroup_id].name,
                not_eligible=why,
                heats=summary.base,
                score=str(summary.avg) if points else _time(settings, summary.avg),
                best=ordinal(summary.best) if points else _time(settings, summary.best),
                worst=ordinal(summary.worst)
                if points
                else _time(settings, summary.worst),
                times=times,
            )
        )
    return Standings(
        heading=SCORING.get((points, settings.scoring), ""),
        catalog=tuple((e.key, e.name) for e in catalog),
        selected=selected,
        title=title,
        cards=tuple(cards),
        use_points=points,
        use_groups=len(standings.event.groups) > 1,
        use_subgroups=settings.use_subgroups,
        # Under D46 a round scores out of the lanes it raced on, so the label
        # names no count for first place.
        score_label="Points" if points else "Average",
    )
