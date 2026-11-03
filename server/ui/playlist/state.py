"""What the rounds playlist page shows (#44): the racing scene, the queue of
steps, the rounds that can be added, and the scheduling partial.

Reads only. The page loads its state once and never polls (`playlist.php`
neither); every change answers with the queue again.
"""

from dataclasses import dataclass, field

from core.models import RaceSettings, RaceState
from kiosks.models import Scene
from racing.engine.scheduling.lanes import count_one_bits
from racing.models import PlaylistEntry
from racing.services import lanes as lane_service
from racing.services.finals import allowed_for_step
from racing.services.playlist import MAX_RUNS_PER_LANE, NEXT_ROUND, STOP, needs_cut
from racing.services.rounds import round_counts
from roster.models import Group, GroupConstituent, GroupConstituentSubgroup
from roster.services.groups import labels
from ui.responses import racer_name

RUNS_CHOICES = tuple(range(1, MAX_RUNS_PER_LANE + 1))


@dataclass
class EntryRow:
    id: int
    position: int
    group_id: int
    round_number: int
    name: str
    bucket_limit: int
    bucketed: bool
    runs: int
    then: int
    then_label: str
    waits: bool
    # The intermission waits on this step (D12).
    waiting: bool
    # "raced", "partial", "current", or "".
    status: str
    # The round picks its racers when it starts: its top N shows (D43).
    cut: bool
    bucket_label: str
    allowed: list[str] = field(default_factory=list)

    @property
    def waits_id(self) -> str:
        return f"pl-waits-{self.id}"


@dataclass
class AvailableRow:
    group_id: int
    round_number: int
    name: str
    status: str
    cut: bool
    bucket_label: str
    # A gap before the first round of each round number after the first.
    gap_before: bool = False


@dataclass
class Playlist:
    scenes: list[tuple[int, str]]
    racing_scene_id: int | None
    use_master_sched: bool
    entries: list[EntryRow]
    available: list[AvailableRow]
    waiting_id: int | None
    # A new step waits for the operator unless it is the first (D12).
    default_waits: bool
    runs_choices: tuple[int, ...] = RUNS_CHOICES
    stop: int = STOP
    next_round: int = NEXT_ROUND


def _status(counts, current_round_id) -> str:
    if counts is None:
        return ""
    if counts.round.pk == current_round_id:
        return "current"
    if counts.heats_scheduled and counts.heats_run >= counts.heats_scheduled:
        return "raced"
    if counts.heats_run:
        return "partial"
    return ""


def _aggregates(event) -> set[int]:
    return set(
        GroupConstituent.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    ) | set(
        GroupConstituentSubgroup.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    )


def _bucket_label(settings, round_number: int, aggregate: bool) -> str:
    """The "racers from" choice of `playlist.php`'s modal: each group for an
    aggregate's first round, each subgroup for a follow-on round when
    subgroups are on, or none."""
    found = labels(settings)
    if round_number == 1 and aggregate:
        return f"Each {found.group.lower()}"
    if settings.use_subgroups:
        return f"Each {found.subgroup.lower()}"
    return ""


def then_label(then: int, scenes: dict[int, str]) -> str:
    if then == NEXT_ROUND:
        return "Start next round"
    if then > 0:
        return f"{scenes.get(then, 'A')} scene"
    return "Stop"


def _then(entry: PlaylistEntry) -> int:
    """`append_playlist_entry_li()`: a scene, else the next round if it
    continues racing, else stop."""
    if entry.scene_at_finish_id:
        return entry.scene_at_finish_id
    return NEXT_ROUND if entry.continue_racing else STOP


def build(event) -> Playlist:
    settings = RaceSettings.objects.get(event=event)
    state = RaceState.objects.get(event=event)
    scenes = list(
        Scene.objects.filter(event=event)
        .order_by("sort_order", "id")
        .values_list("id", "name")
    )
    scene_names = dict(scenes)
    groups = list(Group.objects.filter(event=event).order_by("sort_order", "name"))
    with_group = len(groups) > 1
    aggregates = _aggregates(event)
    counts = {(c.round.group_id, c.round.number): c for c in round_counts(event)}

    def name(group: Group, number: int) -> str:
        return f"{group.name}, Round {number}" if with_group else f"Round {number}"

    entries = []
    steps = (
        PlaylistEntry.objects.filter(event=event)
        .select_related("group")
        .order_by("seq")
    )
    for position, step in enumerate(steps, 1):
        then = _then(step)
        cut = needs_cut(step.group_id, step.round_number)
        allowed = allowed_for_step(event.id, step.group_id, step.round_number)
        entries.append(
            EntryRow(
                id=step.pk,
                position=position,
                group_id=step.group_id,
                round_number=step.round_number,
                name=name(step.group, step.round_number),
                bucket_limit=step.bucket_limit,
                bucketed=step.bucketed,
                runs=step.n_times_per_lane,
                then=then,
                then_label=then_label(then, scene_names),
                waits=step.waits_for_operator,
                waiting=state.waiting_entry_id == step.pk,
                status=_status(
                    counts.get((step.group_id, step.round_number)),
                    state.current_round_id,
                ),
                cut=cut,
                bucket_label=_bucket_label(
                    settings, step.round_number, step.group_id in aggregates
                ),
                allowed=[racer_name(r.first_name, r.last_name) for r in allowed],
            )
        )

    return Playlist(
        scenes=scenes,
        racing_scene_id=settings.racing_scene_id,
        use_master_sched=settings.use_master_sched,
        entries=entries,
        available=_available(
            event, groups, entries, counts, state, settings, aggregates, name
        ),
        waiting_id=state.waiting_entry_id,
        default_waits=bool(entries),
    )


def _available(event, groups, entries, counts, state, settings, aggregates, name):
    """`build_rounds()` of `playlist.js`: from round 1 up to one past the
    highest round queued or made, each group's round that is not queued,
    if it is made, or if every round of the group is queued or raced and
    it is the next one."""
    queued = {(e.group_id, e.round_number) for e in entries}
    highest = {g.pk: 0 for g in groups}
    for group_id, number in [*queued, *counts]:
        if group_id in highest:
            highest[group_id] = max(highest[group_id], number)
    top = max(highest.values(), default=0)
    offer_more = {g.pk: True for g in groups}
    found = []
    for number in range(1, top + 2):
        first_of_number = True
        for group in groups:
            key = (group.pk, number)
            if key in queued:
                continue
            made = counts.get(key)
            if made and made.heats_run < made.heats_scheduled:
                offer_more[group.pk] = False
            if not (made or (offer_more[group.pk] and highest[group.pk] == number - 1)):
                continue
            found.append(
                AvailableRow(
                    group_id=group.pk,
                    round_number=number,
                    name=name(group, number),
                    status=_status(made, state.current_round_id),
                    cut=needs_cut(group.pk, number),
                    bucket_label=_bucket_label(
                        settings, number, group.pk in aggregates
                    ),
                    gap_before=first_of_number and number > 1 and bool(found),
                )
            )
            first_of_number = False
    return found


# ----- the scheduling partial ------------------------------------------------------

METHODS = (
    ("normal", "Normal", "Prefer this unless you have a reason to need another."),
    (
        "abbreviated",
        "One run per car",
        "Each car runs just once in a round. Sometimes useful for preliminary "
        "rounds with a very large field.",
    ),
    (
        "rotation",
        "Lane rotation",
        "Cars shift over one lane for each heat, as A-B-C-D, then B-C-D-E. "
        "Racers meet fewer others head to head, but a new race crew may find "
        "it easier to run.",
    ),
)


@dataclass
class LaneRow:
    lane: int
    # "open", "unused" (set aside on the settings page) or "closed" (closed
    # as damaged from the race dashboard, D36).
    state: str

    @property
    def off(self) -> bool:
        return self.state != "open"


@dataclass
class Scheduling:
    method: str
    methods: tuple = METHODS
    lane_count: int = 0
    lanes: list[LaneRow] = field(default_factory=list)
    usable: int = 0
    closed: list[int] = field(default_factory=list)
    unused: list[int] = field(default_factory=list)
    # Lanes cannot change while unraced heats exist (`already-scheduled`).
    locked: bool = False


def method_of(settings: RaceSettings) -> str:
    """`settings.php`: abbreviated when max-runs-per-car is set, else
    rotation when rotation-schedule is on, else normal."""
    if settings.max_runs_per_car:
        return "abbreviated"
    if settings.rotation_schedule:
        return "rotation"
    return "normal"


def scheduling(event) -> Scheduling:
    """The method, and the lanes: each one open, unused, or closed (D36,
    D45). A closed lane is in the unused-lane mask too; the poll of closed
    lanes says which ones were closed as damaged."""
    from racing.models import HeatLane

    settings = RaceSettings.objects.get(event=event)
    closed = {row["lane"] for row in lane_service.poll_lanes(event)["closed-lanes"]}
    mask = settings.unused_lane_mask
    rows = []
    for lane in range(1, settings.lane_count + 1):
        if lane in closed:
            state = "closed"
        elif mask & (1 << (lane - 1)):
            state = "unused"
        else:
            state = "open"
        rows.append(LaneRow(lane, state))
    in_range = (1 << settings.lane_count) - 1
    closed_mask = sum(1 << (lane - 1) for lane in closed)
    off = count_one_bits((mask | closed_mask) & in_range)
    return Scheduling(
        method=method_of(settings),
        lane_count=settings.lane_count,
        lanes=rows,
        usable=settings.lane_count - off,
        closed=[r.lane for r in rows if r.state == "closed"],
        unused=[r.lane for r in rows if r.state == "unused"],
        locked=HeatLane.objects.filter(event=event, completed__isnull=True).exists(),
    )
