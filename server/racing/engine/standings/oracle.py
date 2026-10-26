"""The standings: `StandingsOracle` and `unfinished_rounds` from
`website/inc/standings.inc`, computed in Python from the event's rows.

Every heat result of a racer in a round becomes one row per racer and round,
ordered best first. Each row adds a place to the ladders it bears on, keyed
as PHP keys them:

- `st-r<round>`: the round.
- `st-rr<round>-<subgroup>`: the racer's subgroup within the round, with
  subgroups on, for a finished round.
- `st-q<group>`: an aggregate group's qualifier standings, for the final
  round of each group it draws from, filtered by its subgroups.
- `st-ff`: the full field, for the final round of every group.

A group's final round is its highest-numbered round, finished or not, so
while round 2 is scheduled and unraced, round 1 adds no `st-q` or `st-ff`
place. A round is finished when it has a chart and every lane has a result.

A racer who cannot win a ladder's speed awards keeps the row and takes no
place in that ladder (D33, D34; see `eligibility`). Every place, ladder and
`T` mark is then what PHP gives with that racer removed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cached_property
from types import MappingProxyType

from racing.engine.phpcompat import ascii_fold
from racing.engine.standings.eligibility import ELIGIBLE, AwardSettings, NotEligible
from racing.engine.standings.scoring import Scoring, Summary, summarize
from racing.engine.standings.tracker import Ladder, StandingsTracker

FULL_FIELD = "st-ff"

Place = int | str
"""A place in a ladder: 3, or "T3" for a rung shared by a tie."""


def round_key(round_id: int) -> str:
    return f"st-r{round_id}"


def round_subgroup_key(round_id: int, subgroup_id: int) -> str:
    return f"st-rr{round_id}-{subgroup_id}"


def qualifier_key(group_id: int) -> str:
    return f"st-q{group_id}"


@dataclass(frozen=True)
class Group:
    """A group, legacy class. An aggregate has `constituents`, the groups
    it draws from in their stored order, and, when it draws from subgroups,
    `subgroup_ids`, which filter its qualifier standings."""

    id: int
    name: str
    sort_order: int
    n_trophies: int | None = None
    constituents: tuple[int, ...] = ()
    subgroup_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class Subgroup:
    id: int
    group_id: int
    name: str
    sort_order: int
    n_trophies: int | None = None


@dataclass(frozen=True)
class Round:
    id: int
    group_id: int
    number: int
    finished: bool


@dataclass(frozen=True)
class Racer:
    id: int
    first_name: str
    last_name: str
    car_number: int
    car_name: str
    group_id: int
    subgroup_id: int
    awards: AwardSettings = ELIGIBLE


@dataclass(frozen=True)
class Result:
    round_id: int
    racer_id: int
    finish_time: float | None
    finish_place: int | None


@dataclass(frozen=True)
class Settings:
    """`full_field_calc` is the legacy setting: 0 for standard full-field
    standings, -1 for none, or the id of the aggregate group whose
    standings decide the pack's awards."""

    use_points: bool = False
    scoring: Scoring = Scoring.AVERAGE
    lane_count: int = 0
    use_subgroups: bool = False
    full_field_calc: int = 0
    supergroup_label: str = "Pack"

    @property
    def pack_group_id(self) -> int | None:
        return self.full_field_calc if self.full_field_calc > 0 else None


@dataclass(frozen=True)
class Event:
    """What the standings read. `racers` are the racers who passed check-in,
    and `results` the results of those racers in rounds whose roster holds
    them."""

    groups: tuple[Group, ...]
    subgroups: tuple[Subgroup, ...]
    rounds: tuple[Round, ...]
    racers: tuple[Racer, ...]
    results: tuple[Result, ...]
    settings: Settings = Settings()


@dataclass(frozen=True)
class Row:
    """One racer's showing in one round. `outcome` maps each ladder the row
    takes a place in to that place, in PHP's key order. `not_eligible` maps
    each ladder the row would bear on but takes no place in to the reason.
    A row with no score in the scoring mode takes no place anywhere."""

    round: Round
    racer: Racer
    summary: Summary
    nonfinal: bool = False
    outcome: Mapping[str, Place] = MappingProxyType({})
    not_eligible: Mapping[str, NotEligible] = MappingProxyType({})


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    key: str
    presentation: str


class Standings:
    def __init__(self, event: Event):
        self.event = event
        self.settings = event.settings
        self.groups = {g.id: g for g in event.groups}
        self.subgroups = {s.id: s for s in event.subgroups}
        self.racers = {r.id: r for r in event.racers}
        self.rounds_of: dict[int, list[Round]] = {g.id: [] for g in event.groups}
        for round in sorted(event.rounds, key=lambda r: -r.number):
            self.rounds_of.setdefault(round.group_id, []).append(round)
        self.rolls_up_to = _rolls_up_to(event.groups)
        self.ladders: dict[str, Ladder] = {}
        self.catalog_counts: dict[str, int] = {}
        self.rows: tuple[Row, ...] = self._place_rows(_rows(event))

    def _keys(self, row: Row) -> tuple[list[str], bool]:
        """The ladders a row bears on, in PHP's order, and whether its round
        is a finished round other than its group's final one."""
        keys = [round_key(row.round.id)]
        if not row.round.finished:
            return keys, False
        subgroup = round_subgroup_key(row.round.id, row.racer.subgroup_id)
        rounds = self.rounds_of[row.round.group_id]
        if rounds and rounds[0].id == row.round.id:
            if self.settings.use_subgroups:
                keys.append(subgroup)
            for aggregate_id in self.rolls_up_to.get(row.round.group_id, ()):
                only = self.groups[aggregate_id].subgroup_ids
                if not only or row.racer.subgroup_id in only:
                    keys.append(qualifier_key(aggregate_id))
            keys.append(FULL_FIELD)
            return keys, False
        if self.settings.use_subgroups:
            keys.append(subgroup)
        return keys, True

    def _is_pack_ladder(self, key: str, row: Row) -> bool:
        pack = self.settings.pack_group_id
        if key == FULL_FIELD:
            return True
        if pack is None:
            return False
        return key == qualifier_key(pack) or (
            row.round.group_id == pack and not key.startswith("st-q")
        )

    def _place_rows(self, drafts: list[Row]) -> tuple[Row, ...]:
        trackers: dict[str, StandingsTracker] = {}
        marks: list[tuple[bool, dict[str, int], dict[str, NotEligible]]] = []
        for row in drafts:
            placed: dict[str, int] = {}
            not_eligible: dict[str, NotEligible] = {}
            nonfinal = False
            if row.summary.score is not None:
                keys, nonfinal = self._keys(row)
                for key in keys:
                    why = row.racer.awards.why_not(
                        all_partitions=self._is_pack_ladder(key, row)
                    )
                    if why is not None:
                        not_eligible[key] = why
                        continue
                    tracker = trackers.setdefault(key, StandingsTracker())
                    placed[key] = tracker.add(row.summary.score, row.racer.id)
            marks.append((nonfinal, placed, not_eligible))
        for key, tracker in trackers.items():
            self.ladders[key] = tracker.rungs
            self.catalog_counts[key] = tracker.count

        def place(key: str, rung: int) -> Place:
            return f"T{rung + 1}" if len(self.ladders[key][rung]) > 1 else rung + 1

        return tuple(
            replace(
                row,
                nonfinal=nonfinal,
                outcome=MappingProxyType({k: place(k, r) for k, r in placed.items()}),
                not_eligible=MappingProxyType(not_eligible),
            )
            for row, (nonfinal, placed, not_eligible) in zip(drafts, marks, strict=True)
        )

    @property
    def has_full_field_standings(self) -> bool:
        return self.settings.full_field_calc == 0

    def _highest_finished_round(self, group_id: int) -> Round | None:
        return next((r for r in self.rounds_of.get(group_id, ()) if r.finished), None)

    def class_key(self, group_id: int) -> str | None:
        """The ladder of a group's standings: its highest finished round,
        else, for an aggregate, its qualifier standings."""
        round = self._highest_finished_round(group_id)
        if round is not None:
            return round_key(round.id)
        if self.groups[group_id].constituents:
            return qualifier_key(group_id)
        return None

    def rank_key(self, subgroup_id: int) -> str | None:
        round = self._highest_finished_round(self.subgroups[subgroup_id].group_id)
        if round is None:
            return None
        return round_subgroup_key(round.id, subgroup_id)

    def award_ladder_full_field(self) -> Ladder:
        return self.ladders.get(FULL_FIELD, [])

    def award_ladder_class(self, group_id: int) -> Ladder:
        """A group's highest round, finished or not, else its qualifier
        standings."""
        rounds = self.rounds_of.get(group_id, [])
        key = round_key(rounds[0].id) if rounds else qualifier_key(group_id)
        return self.ladders.get(key, [])

    def award_ladder_subgroup(self, subgroup_id: int) -> Ladder:
        rounds = self.rounds_of.get(self.subgroups[subgroup_id].group_id, [])
        if not rounds:
            return []
        return self.ladders.get(round_subgroup_key(rounds[0].id, subgroup_id), [])

    @cached_property
    def group_order(self) -> list[Group]:
        """`classseq`: groups in the order `classes_and_ranks` lists them."""
        seen: dict[int, Group] = {}
        for group, _subgroup in self._group_subgroup_rows:
            seen.setdefault(group.id, group)
        return list(seen.values())

    @cached_property
    def subgroup_order(self) -> list[Subgroup]:
        """`rankseq`."""
        return [s for _g, s in self._group_subgroup_rows if s is not None]

    @cached_property
    def _group_subgroup_rows(self) -> list[tuple[Group, Subgroup | None]]:
        """`Classes LEFT JOIN Ranks ORDER BY Classes.sortorder,
        Ranks.sortorder, class, rank`, names compared as `NOCASE`."""
        rows: list[tuple[Group, Subgroup | None]] = []
        by_group: dict[int, list[Subgroup]] = {}
        for subgroup in self.event.subgroups:
            by_group.setdefault(subgroup.group_id, []).append(subgroup)
        for group in self.event.groups:
            rows += [(group, s) for s in by_group.get(group.id, [])] or [(group, None)]

        def order(row):
            group, subgroup = row
            return (
                group.sort_order,
                subgroup is not None,
                subgroup.sort_order if subgroup else 0,
                ascii_fold(group.name),
                ascii_fold(subgroup.name) if subgroup else "",
                group.id,
                subgroup.id if subgroup else 0,
            )

        return sorted(rows, key=order)

    def catalog(self) -> list[CatalogEntry]:
        """`standings_catalog()`: every standings presentation, in order."""
        use_groups = len(self.event.groups) > 1
        use_subgroups = self.settings.use_subgroups
        catalog = []
        if self.has_full_field_standings:
            catalog.append(
                CatalogEntry(self.settings.supergroup_label, FULL_FIELD, "ff")
            )
        subgroups = self.subgroup_order
        next_subgroup = 0
        for group in self.group_order:
            rounds = list(self.rounds_of.get(group.id, []))
            if not rounds:
                catalog.append(
                    CatalogEntry(group.name, qualifier_key(group.id), "qual")
                )
                continue
            scoring = None
            while rounds:
                candidate = rounds.pop(0)
                if candidate.finished:
                    scoring = candidate
                    break
            if scoring is None:
                continue
            if use_groups:
                catalog.append(CatalogEntry(group.name, round_key(scoring.id), "class"))
            while next_subgroup < len(subgroups):
                subgroup = subgroups[next_subgroup]
                if subgroup.group_id != group.id:
                    break
                if use_subgroups:
                    catalog.append(
                        CatalogEntry(
                            f"{group.name} / {subgroup.name}",
                            round_subgroup_key(scoring.id, subgroup.id),
                            "subgroup",
                        )
                    )
                next_subgroup += 1
            for round in rounds:
                name = (
                    f"{group.name}, Round {round.number}"
                    if use_groups
                    else f"Round {round.number}"
                )
                catalog.append(CatalogEntry(name, round_key(round.id), "round"))
                if use_subgroups:
                    for subgroup in subgroups:
                        if subgroup.group_id == group.id:
                            catalog.append(
                                CatalogEntry(
                                    f"{name} / {subgroup.name}",
                                    round_subgroup_key(round.id, subgroup.id),
                                    "round-subgroup",
                                )
                            )
            if group.constituents:
                catalog.append(
                    CatalogEntry(
                        f"{group.name} Qualifying Rounds",
                        qualifier_key(group.id),
                        "qual",
                    )
                )
        return catalog


def _rolls_up_to(groups: Sequence[Group]) -> dict[int, list[int]]:
    """`add_rolls_up_to_relation()`: for each group, the aggregates whose
    qualifier standings its final round bears on.

    PHP means to carry the relation through nonracing aggregates only, but
    it tests for rounds before it has read any, so the relation runs through
    every aggregate. The fixtures show it: an aggregate of two racing
    aggregates holds their constituents' round 1 rows too.
    """
    ordered = sorted(groups, key=lambda g: g.id)
    ups: dict[int, list[int]] = {g.id: [] for g in ordered}
    for group in ordered:
        for constituent in group.constituents:
            ups.setdefault(constituent, []).append(group.id)
    repeat = True
    while repeat:
        repeat = False
        for group in ordered:
            found = ups[group.id]
            for up in list(found):
                for further in ups.get(up, ()):
                    if further not in found:
                        found.append(further)
                        repeat = True
    return ups


def _rows(event: Event) -> list[Row]:
    """`make_result_summary()`: one row per racer and round, best score
    first, then by last and first name as `NOCASE` compares them. A row
    with no score sorts first, as SQLite sorts NULL."""
    racers = {r.id: r for r in event.racers}
    rounds = {r.id: r for r in event.rounds}
    heats: dict[tuple[int, int], list[Result]] = {}
    for result in event.results:
        if result.racer_id in racers and result.round_id in rounds:
            heats.setdefault((result.round_id, result.racer_id), []).append(result)
    settings = event.settings
    rows = [
        Row(
            round=rounds[round_id],
            racer=racers[racer_id],
            summary=summarize(
                [r.finish_time for r in results],
                [r.finish_place for r in results],
                use_points=settings.use_points,
                scoring=settings.scoring,
                lane_count=settings.lane_count,
            ),
        )
        for (round_id, racer_id), results in heats.items()
    ]

    def order(row: Row):
        score = row.summary.score
        return (
            score is not None,
            score if score is not None else 0.0,
            ascii_fold(row.racer.last_name),
            ascii_fold(row.racer.first_name),
            row.round.id,
            row.racer.id,
        )

    return sorted(rows, key=order)
