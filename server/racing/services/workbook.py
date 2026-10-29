"""`export_all()` of `website/inc/export-all.inc`: the workbook behind
`export-json.php`, and the spreadsheet export of #49.

A workbook is a list of `[sheet name, rows]` pairs: Roster, Results, one
Standings sheet per catalog entry, and Awards. Values keep the types PHP's
PDO gives them, since `export-json.php` encodes without numeric coercion: a
car number is a number, a car name that looks like one stays text.
"""

from dataclasses import dataclass

from django.utils import timezone

from awards.models import Award
from core.models import Event, RaceSettings
from racing.engine.standings.oracle import FULL_FIELD, round_key, round_subgroup_key
from racing.engine.standings.ordinals import ordinal
from racing.models import HeatLane
from racing.services.polls import format_time, mangled_name
from racing.services.standings import standings_snapshot
from roster.models import Group, Racer
from roster.services.groups import Labels, labels


@dataclass(frozen=True)
class Layout:
    """The settings that shape the Standings and Awards sheets, apart from
    the event's rows, so the sheets can be checked against PHP's captures."""

    names: Labels
    name_style: int = 0
    use_subgroups: bool = False
    use_points: bool = False
    scoring: int = 0
    lane_count: int = 0
    finish_formatting: str = "%5.3f"
    show_car_name: bool = False

    @classmethod
    def of(cls, event: Event, settings: RaceSettings) -> Layout:
        return cls(
            names=labels(settings),
            name_style=settings.name_style,
            use_subgroups=settings.use_subgroups,
            use_points=settings.use_points,
            scoring=settings.scoring,
            lane_count=settings.lane_count,
            finish_formatting=settings.finish_formatting,
            show_car_name=Racer.objects.filter(event=event)
            .exclude(car_name="")
            .exists(),
        )


SCALE_MPH_PER_FPS = 3600 * 25 / 5280
SHEET_NAME_WIDTH = 31


def export_all(event: Event, *, show_racer_id: bool = False) -> list:
    settings = RaceSettings.objects.get(event=event)
    snapshot = standings_snapshot(event)
    standings = snapshot.standings
    layout = Layout.of(event, settings)
    workbook = [
        ["Roster", roster(event, settings, show_racer_id)],
        ["Results", results(event, settings)],
    ]
    workbook += standings_sheets(layout, standings)
    workbook.append(["Awards", awards(event, layout, snapshot)])
    # PHP's closing loop that makes names safe for XLSX edits a copy, so a
    # sheet keeps the name above.
    return workbook


def _group_count(event: Event) -> int:
    return Group.objects.filter(event=event).count()


def standings_sheets(layout: Layout, standings) -> list:
    sheets = []
    for entry in standings.catalog():
        title = "Standings " + entry.name.replace("/", "-")
        # XLSX won't allow a sheet name past 31 characters, and PHP counts bytes.
        if len(title.encode()) > SHEET_NAME_WIDTH:
            title = f"Standings {entry.key}"
        if entry.presentation == "ff":
            title = "Standings"
        sheet = [[f"Standings for {entry.name}"]]
        sheet += standings_rows(layout, standings, entry)
        sheets.append([title, sheet])
    return sheets


def roster(event: Event, settings: RaceSettings, show_racer_id: bool = False) -> list:
    use_groups = _group_count(event) > 1
    names = labels(settings)
    full_name = settings.name_style == 0
    headings = ["RacerID"] if show_racer_id else []
    headings += ["First Name", "Last Name"] if full_name else ["Name"]
    headings += ["From", "Car Number", "Car Name"]
    if use_groups:
        headings.append(names.group)
    if settings.use_subgroups:
        headings.append(names.subgroup)
    headings.append("Excluded?")
    rows = [headings]
    racers = (
        Racer.objects.filter(event=event, passed_inspection=True)
        .select_related("group", "subgroup")
        .order_by("last_name", "first_name", "pk")
    )
    for racer in sorted(racers, key=lambda r: (r.last_name, r.first_name, r.pk)):
        line = [racer.pk] if show_racer_id else []
        if full_name:
            line += [racer.first_name, racer.last_name]
        else:
            line.append(
                mangled_name(racer.first_name, racer.last_name, settings.name_style)
            )
        line += [racer.note, racer.car_number, racer.car_name]
        if use_groups:
            line.append(racer.group.name)
        if settings.use_subgroups:
            line.append(racer.subgroup.name)
        line.append(int(racer.exclude))
        rows.append(line)
    return rows


def _scale_mph(settings: RaceSettings, finish_time) -> float | str:
    if not finish_time:
        return "--"
    return float(f"{settings.track_length / finish_time * SCALE_MPH_PER_FPS:4.1f}")


def _stamp(moment) -> str | None:
    if moment is None:
        return None
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S")


def results(event: Event, settings: RaceSettings) -> list:
    full_name = settings.name_style == 0
    headings = ["Class", "Round", "Heat", "Lane"]
    headings += ["FirstName", "LastName"] if full_name else ["Name"]
    headings += [
        "CarNumber",
        "CarName",
        "FinishTime",
        "Scale MPH",
        "FinishPlace",
        "Completed",
    ]
    rows = [headings]
    lanes = HeatLane.objects.filter(event=event).select_related("round__group", "racer")
    # SQLite sorts a missing completion time first.
    ordered = sorted(
        lanes,
        key=lambda lane: (
            lane.completed is not None,
            lane.completed.timestamp() if lane.completed else 0,
            lane.heat,
            lane.lane,
            lane.pk,
        ),
    )
    for lane in ordered:
        racer = lane.racer
        line = [lane.round.group.name, lane.round.number, lane.heat, lane.lane]
        if full_name:
            line += [racer.first_name, racer.last_name]
        else:
            line.append(
                mangled_name(racer.first_name, racer.last_name, settings.name_style)
            )
        line += [
            racer.car_number,
            racer.car_name,
            float(lane.finish_time or 0),
            _scale_mph(settings, lane.finish_time),
            lane.finish_place,
            _stamp(lane.completed),
        ]
        rows.append(line)
    return rows


def standings_rows(layout: Layout, standings, entry) -> list:
    use_groups = len(standings.event.groups) > 1
    names = layout.names
    heading = (
        "Scoring by points"
        if layout.use_points
        else {
            0: "Averaging all heat times",
            1: "Dropping each racer's slowest time",
            2: "Using each racer's fastest time only",
        }.get(layout.scoring, "Unknown scoring method")
    )
    rows = [[heading]]
    headers = ["Place", "Car Number", "Name"]
    if layout.show_car_name:
        headers.append("Car Name")
    if use_groups:
        headers += [names.group, f"In {names.group}"]
    if layout.use_subgroups:
        headers += [names.subgroup, f"In {names.subgroup}"]
    if entry.presentation == "subgroup":
        headers.append(f"In {names.supergroup}")
    headers.append("Heats")
    headers.append(
        f"Total Points (1st = {layout.lane_count})" if layout.use_points else "Average"
    )
    headers += ["Best", "Worst"]
    rows.append(headers)

    for row in standings.rows:
        outcome = row.outcome
        if entry.key not in outcome:
            continue
        racer = row.racer
        line = [
            outcome[entry.key],
            racer.car_number,
            mangled_name(racer.first_name, racer.last_name, layout.name_style),
        ]
        if layout.show_car_name:
            line.append(racer.car_name)
        if use_groups:
            line.append(standings.groups[racer.group_id].name)
            in_round = entry.presentation in ("ff", "class", "subgroup")
            line.append(outcome.get(round_key(row.round.id)) if in_round else "")
        if layout.use_subgroups:
            line.append(standings.subgroups[racer.subgroup_id].name)
            line.append(
                outcome.get(round_subgroup_key(row.round.id, racer.subgroup_id))
            )
        if entry.presentation == "subgroup":
            line.append(outcome.get(FULL_FIELD, ""))
        summary = row.summary
        line.append(summary.base)
        if layout.use_points:
            line.append(summary.avg)
            line.append(ordinal(summary.best))
            line.append(ordinal(summary.worst))
        else:
            for value in (summary.avg, summary.best, summary.worst):
                line.append(float(format_time(layout.finish_formatting, value)))
        rows.append(line)
    return rows


def _award_line(layout: Layout, use_groups, name, kind, group, subgroup, racer):
    full_name = layout.name_style == 0
    out = [name, kind]
    if use_groups:
        out.append(group)
    if layout.use_subgroups:
        out.append(subgroup)
    if racer is None:
        # PHP mangles the missing name: a null pair, or " ." in short style.
        out += [None, None] if full_name else [mangled_name("", "", 1)]
        return out + [None, None]
    if full_name:
        out += [racer.first_name, racer.last_name]
    else:
        out.append(mangled_name(racer.first_name, racer.last_name, layout.name_style))
    return out + [racer.car_number, racer.car_name]


def speed_award_rows(layout: Layout, standings, speed_awards) -> list:
    use_groups = len(standings.event.groups) > 1
    rows = []
    for award in speed_awards:
        group = standings.groups.get(award.group_id)
        subgroup = standings.subgroups.get(award.subgroup_id)
        rows.append(
            _award_line(
                layout,
                use_groups,
                award.name,
                "Speed Trophy",
                group.name if group else None,
                subgroup.name if subgroup else None,
                standings.racers[award.racer_id],
            )
        )
    return rows


def awards(event: Event, layout: Layout, snapshot) -> list:
    standings = snapshot.standings
    use_groups = len(standings.event.groups) > 1
    names = layout.names
    headings = ["Award Name", "Award Type"]
    if use_groups:
        headings.append(names.group)
    if layout.use_subgroups:
        headings.append(names.subgroup)
    headings += ["First Name", "Last Name"] if layout.name_style == 0 else ["Name"]
    headings += ["Car Number", "Car Name"]
    rows = [headings]
    rows += speed_award_rows(layout, standings, snapshot.speed_awards.awards)

    judged = Award.objects.filter(event=event).select_related(
        "award_type", "racer", "group", "subgroup"
    )
    for award in sorted(
        judged, key=lambda a: (a.sort, a.award_type.name, a.name, a.pk)
    ):
        rows.append(
            _award_line(
                layout,
                use_groups,
                award.name,
                award.award_type.name,
                award.group.name if award.group else None,
                award.subgroup.name if award.subgroup else None,
                award.racer,
            )
        )
    return rows
