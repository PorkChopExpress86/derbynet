"""What the Retrospective page shows (#49): `history.php`'s lane bias test
and its events timeline.

The lane bias card is `racing.services.lane_bias`, with PHP's sentences and
numbers. The timeline is `legacy_api.history_formatter.history()`, which
resolves an imported event's legacy ids first (`transfer.action_log`), in
groups by the hour the actions were received, oldest first, as PHP lists
them. The debug view lists every logged action, with its request, also
those that have no sentence.
"""

import json
from dataclasses import dataclass

from django.utils import timezone
from django.utils.safestring import SafeString, mark_safe

from core.models import RaceSettings
from legacy_api.history_formatter import history
from racing.services.lane_bias import lane_bias
from racing.services.polls import format_time


@dataclass(frozen=True)
class LaneRow:
    lane: int
    runs: str
    dnfs: str
    mean: str
    variance: str


@dataclass(frozen=True)
class Bias:
    summary: str
    biased: bool
    lanes: tuple[LaneRow, ...]
    f_line: str
    critical_line: str


@dataclass(frozen=True)
class Event:
    heat: str
    time: str
    # The formatter's sentence: HTML whose text it escaped (`set_action`).
    sentence: SafeString
    request: str = ""


@dataclass(frozen=True)
class Hour:
    label: str
    events: tuple[Event, ...]


@dataclass(frozen=True)
class Retrospective:
    bias: Bias | None
    hours: tuple[Hour, ...]
    count: int
    debug: bool


NOT_ENOUGH = "There isn't enough evidence to assess lane bias."


def _bias(event) -> Bias | None:
    found = lane_bias(event)
    if found is None:
        return None
    fmt = RaceSettings.objects.get(event=event).finish_formatting
    return Bias(
        summary="The track lanes appear to be biased, with 90% confidence."
        if found.biased
        else "There is no evidence of significant lane bias.",
        biased=found.biased,
        lanes=tuple(
            LaneRow(
                lane=n,
                runs=f"{s.runs} run(s)",
                dnfs=f"{s.dnfs} DNF(s)",
                mean=f"{format_time(fmt, s.mean)}s avg.",
                variance="--"
                if s.sample_variance is None
                else f"{format_time(fmt, s.sample_variance)} sample variance.",
            )
            for n, s in enumerate(found.lanes, start=1)
        ),
        f_line=f"F statistic is {found.f_statistic:5.3f} with df1={found.df1}"
        f" and df2={found.df2}",
        # PHP's sprintf() writes a missing critical value as 0.
        critical_line="Critical value for F statistic is"
        f" {(found.critical_value or 0):5.3f}",
    )


def build(event, *, debug: bool = False) -> Retrospective:
    hours: list[tuple[str, list[Event]]] = []
    count = 0
    for entry in history(event, every_row=debug):
        moment = timezone.localtime(entry.received)
        label = moment.strftime("%a %-d %b %Y, %H:00")
        if not hours or hours[-1][0] != label:
            hours.append((label, []))
        hours[-1][1].append(
            Event(
                heat=entry.heat,
                time=moment.strftime("%H:%M:%S"),
                # The formatter escapes every value it puts in a sentence.
                sentence=mark_safe(entry.sentence),  # noqa: S308
                request=json.dumps(entry.request, default=str, ensure_ascii=False)
                if debug
                else "",
            )
        )
        count += 1
    return Retrospective(
        bias=_bias(event),
        hours=tuple(Hour(label, tuple(events)) for label, events in hours),
        count=count,
        debug=debug,
    )
