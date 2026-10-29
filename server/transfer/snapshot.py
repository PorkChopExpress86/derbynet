"""`snapshot.get` and `snapshot.put` (#36, S5 to S7).

A snapshot is the legacy database as JSON in a thin XML wrapper: one entry
per table with its rows, the highest id and the ids that are missing. `get`
writes the event's rows under the legacy table and column names, numbering
each table from 1, so an old DerbyNet can read the file. `put` hands an
uploaded file to the importer (#14), which reads only column names it
knows and never builds SQL from the file, and replaces the active event.

A snapshot omits `ntrophies`, `note` and `checkin_time`, as PHP's does.
Round trips lose them, and everything Django keeps that the legacy schema
has no column for (lane closures, allowed racers, the timer audit).
"""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from django.utils import timezone

from awards.models import Award, AwardType, BallotAward, Vote, Voter
from core import race_settings
from core.models import Event
from kiosks.models import Scene, SceneKiosk
from legacy_api.encoding import encode_php_json
from racing.models import HeatLane, PlaylistEntry, RosterEntry, Round
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Racer,
    Subgroup,
)
from transfer.importer import LATEST_SCHEMA, ImportFailed, import_derbynet
from transfer.legacy.source import SourceError

# The names of the export schema, by the lower case the reader keys tables with.
_CANONICAL = {
    name.lower(): name
    for name in (
        "Classes Ranks RegistrationInfo Rounds AwardTypes Awards Roster RaceChart"
        " RaceInfo Scenes SceneKiosk Playlist BallotAwards Voters Votes Partitions"
        " ConstituentClasses Events EventLaneDetails"
    ).split()
}


def _json(value) -> str:
    return encode_php_json(value, numeric_check=False).decode().rstrip("\n")


class _Ids:
    """Legacy ids: each table is numbered from 1 in the order of its rows."""

    def __init__(self):
        self._maps: dict[type, dict[int, int]] = {}

    def of(self, model, queryset) -> list:
        rows = list(queryset.order_by("pk"))
        self._maps[model] = {row.pk: n for n, row in enumerate(rows, 1)}
        return rows

    def __call__(self, model, pk):
        return None if pk is None else self._maps[model][pk]


def _stamp(moment):
    if moment is None:
        return None
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S")


def _csv(numbers) -> str:
    return ",".join(str(n) for n in numbers)


def tables(
    event: Event, clean: bool = False
) -> dict[str, tuple[list[str], list[list]]]:
    """Every table of the snapshot, in PHP's order, as its columns and rows."""
    ids = _Ids()
    mine = {"event": event}
    groups = ids.of(Group, Group.objects.filter(**mine))
    subgroups = ids.of(Subgroup, Subgroup.objects.filter(**mine))
    partitions = ids.of(Partition, Partition.objects.filter(**mine))
    racers = ids.of(Racer, Racer.objects.filter(**mine))
    rounds = ids.of(Round, Round.objects.filter(**mine))
    awards = ids.of(Award, Award.objects.filter(**mine))
    scenes = ids.of(Scene, Scene.objects.filter(**mine))
    voters = ids.of(Voter, Voter.objects.filter(**mine))
    constituents: dict[int, list[int]] = {}
    for edge in GroupConstituent.objects.filter(**mine).order_by("id"):
        constituents.setdefault(edge.aggregate_id, []).append(
            ids(Group, edge.constituent_id)
        )
    filters: dict[int, list[int]] = {}
    for edge in GroupConstituentSubgroup.objects.filter(**mine).order_by("id"):
        filters.setdefault(edge.aggregate_id, []).append(
            ids(Subgroup, edge.subgroup_id)
        )

    def name_of(racer: Racer, n: int):
        if not clean:
            return racer.first_name, racer.last_name
        return racer.first_name[:1], f"{racer.last_name[:1]}-{n}"

    racer_rows = []
    for n, racer in enumerate(racers, 1):
        first, last = name_of(racer, n)
        racer_rows.append(
            [
                n,
                racer.car_number,
                racer.car_name,
                last,
                first,
                ids(Group, racer.group_id),
                ids(Subgroup, racer.subgroup_id),
                int(racer.passed_inspection),
                racer.headshot or None,
                int(racer.exclude),
                racer.car_photo or None,
                ids(Partition, racer.partition_id),
            ]
        )
    group_of_round = {r.pk: r.group_id for r in rounds}
    lanes = HeatLane.objects.filter(**mine).order_by("pk")
    out: dict[str, tuple[list[str], list[list]]] = {
        "Classes": (
            ["classid", "class", "sortorder", "constituents", "durable", "rankids"],
            [
                [
                    n,
                    g.name,
                    g.sort_order,
                    _csv(constituents.get(g.pk, [])),
                    int(g.durable),
                    _csv(filters.get(g.pk, [])),
                ]
                for n, g in enumerate(groups, 1)
            ],
        ),
        "Ranks": (
            ["rankid", "rank", "classid", "sortorder"],
            [
                [n, s.name, ids(Group, s.group_id), s.sort_order]
                for n, s in enumerate(subgroups, 1)
            ],
        ),
        "RegistrationInfo": (
            [
                "racerid",
                "carnumber",
                "carname",
                "lastname",
                "firstname",
                "classid",
                "rankid",
                "passedinspection",
                "imagefile",
                "exclude",
                "carphoto",
                "partitionid",
            ],
            racer_rows,
        ),
        "Rounds": (
            ["roundid", "round", "classid", "charttype", "phase"],
            [
                [n, r.number, ids(Group, r.group_id), None, None]
                for n, r in enumerate(rounds, 1)
            ],
        ),
        "AwardTypes": (
            ["awardtypeid", "awardtype"],
            [[t.pk, t.name] for t in AwardType.objects.order_by("pk")],
        ),
        "Awards": (
            [
                "awardid",
                "awardname",
                "awardtypeid",
                "classid",
                "rankid",
                "racerid",
                "sort",
            ],
            [
                [
                    n,
                    a.name,
                    a.award_type_id,
                    ids(Group, a.group_id),
                    ids(Subgroup, a.subgroup_id),
                    ids(Racer, a.racer_id),
                    a.sort,
                ]
                for n, a in enumerate(awards, 1)
            ],
        ),
        "Roster": (
            ["rosterid", "roundid", "classid", "racerid", "finalist", "grandfinalist"],
            [
                [
                    n,
                    ids(Round, e.round_id),
                    ids(Group, group_of_round[e.round_id]),
                    ids(Racer, e.racer_id),
                    0,
                    0,
                ]
                for n, e in enumerate(
                    RosterEntry.objects.filter(**mine).order_by("pk"), 1
                )
            ],
        ),
        "RaceChart": (
            [
                "resultid",
                "classid",
                "roundid",
                "heat",
                "lane",
                "racerid",
                "chartnumber",
                "finishtime",
                "finishplace",
                "points",
                "completed",
                "ignoretime",
                "masterheat",
            ],
            [
                [
                    n,
                    ids(Group, group_of_round[lane.round_id]),
                    ids(Round, lane.round_id),
                    lane.heat,
                    lane.lane,
                    ids(Racer, lane.racer_id),
                    None,
                    lane.finish_time,
                    lane.finish_place,
                    None,
                    _stamp(lane.completed),
                    None,
                    lane.master_heat,
                ]
                for n, lane in enumerate(lanes, 1)
            ],
        ),
        "RaceInfo": (
            ["raceinfoid", "itemkey", "itemvalue"],
            [
                [n, key, value]
                for n, (key, value) in enumerate(
                    [
                        *race_settings.all_keys(event).items(),
                        ("schema", str(LATEST_SCHEMA)),
                    ],
                    1,
                )
            ],
        ),
        "SceneKiosk": (
            ["scenekioskid", "sceneid", "kiosk_name", "page"],
            [
                [
                    n,
                    ids(Scene, k.scene_id),
                    k.kiosk_name,
                    k.page + (f"#{_json(k.params)}" if k.params else ""),
                ]
                for n, k in enumerate(
                    SceneKiosk.objects.filter(**mine).order_by("pk"), 1
                )
            ],
        ),
        "Scenes": (
            ["sceneid", "name", "sortorder"],
            [[n, s.name, s.sort_order] for n, s in enumerate(scenes, 1)],
        ),
        "Playlist": (
            [
                "queueid",
                "seq",
                "classid",
                "round",
                "bucket_limit",
                "bucketed",
                "n_times_per_lane",
                "sceneid_at_finish",
                "continue_racing",
            ],
            [
                [
                    n,
                    p.seq,
                    ids(Group, p.group_id),
                    p.round_number,
                    p.bucket_limit,
                    int(p.bucketed),
                    p.n_times_per_lane,
                    ids(Scene, p.scene_at_finish_id),
                    int(p.continue_racing),
                ]
                for n, p in enumerate(
                    PlaylistEntry.objects.filter(**mine).order_by("pk"), 1
                )
            ],
        ),
        "BallotAwards": (
            ["ballotid", "awardid", "depth"],
            [
                [n, ids(Award, b.award_id), b.depth]
                for n, b in enumerate(
                    BallotAward.objects.filter(**mine).order_by("pk"), 1
                )
            ],
        ),
        "Voters": (
            ["voterid", "ballotid", "when_cast", "ip_addr", "identifier"],
            [
                [n, None, _stamp(v.cast_at), v.ip_address, v.identifier]
                for n, v in enumerate(voters, 1)
            ],
        ),
        "Votes": (
            ["voteid", "awardid", "racerid", "voterid"],
            [
                [
                    n,
                    ids(Award, v.award_id),
                    ids(Racer, v.racer_id),
                    ids(Voter, v.voter_id),
                ]
                for n, v in enumerate(Vote.objects.filter(**mine).order_by("pk"), 1)
            ],
        ),
        "Partitions": (
            ["partitionid", "name", "rankid", "sortorder"],
            [
                [n, p.name, ids(Subgroup, p.subgroup_id), p.sort_order]
                for n, p in enumerate(partitions, 1)
            ],
        ),
        "ConstituentClasses": (
            ["antecedent", "dependent"],
            [
                [antecedent, ids(Group, group.pk)]
                for group in groups
                for antecedent in constituents.get(group.pk, [])
            ],
        ),
    }
    return out


def _table_json(name: str, columns: list[str], rows: list[list]) -> str:
    present = [row[0] for row in rows]
    top = (
        max(present, default=0) if isinstance(present[0] if present else 0, int) else 0
    )
    missing = []
    expecting = 1
    for number in present:
        while isinstance(number, int) and expecting < number:
            missing.append(expecting)
            expecting += 1
        expecting += 1
    rendered = ",\n".join(
        "    {\n"
        + ",\n".join(
            f'     "{c}": {_json(v)}' for c, v in zip(columns, row, strict=True)
        )
        + "}"
        for row in rows
    )
    return (
        f' {{"table": "{name}",\n  "high-water": {top},\n  "rows": [\n{rendered}],\n'
        f'  "missing": {_json(missing)}\n}}'
    )


def _photo_entries(event: Event, clean_ids: dict[int, int]) -> Iterator[str]:
    settings_row = race_settings.read_many(
        event, ["photo-directory", "car-photo-directory"]
    )
    folders = {
        "imagefile": settings_row.get("photo-directory") or "",
        "carphoto": settings_row.get("car-photo-directory") or "",
    }
    columns = {"imagefile": "headshot", "carphoto": "car_photo"}
    for racer in Racer.objects.filter(event=event).order_by("pk"):
        parts = []
        for legacy, field in columns.items():
            path = getattr(racer, field)
            if not path or not folders[legacy]:
                continue
            try:
                data = (Path(folders[legacy]) / path).read_bytes()
            except OSError:
                continue
            parts.append(
                f',\n  "{legacy}": {{\n    "basename": {_json(os.path.basename(path))},'
                f'\n    "displayed": "{data.hex()}"}}'
            )
        if parts:
            yield f' {{"racerid": {clean_ids[racer.pk]}{"".join(parts)}}}'


def get(event: Event, clean: bool = False, photos: bool = False) -> str:
    """The body of `snapshot.get`, without the XML prolog."""
    entries = [
        _table_json(name, columns, rows)
        for name, (columns, rows) in tables(event, clean).items()
    ]
    if photos:
        numbering = {
            pk: n
            for n, pk in enumerate(
                Racer.objects.filter(event=event)
                .order_by("pk")
                .values_list("pk", flat=True),
                1,
            )
        }
        entries += list(_photo_entries(event, numbering))
    return "<snapshot><![CDATA[\n[\n" + ",\n".join(entries) + "]\n]]></snapshot>\n"


class PutFailure(Exception):
    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


def put(event: Event, content: bytes) -> list[tuple[str, str, int, int]]:
    """Replace `event` with the snapshot `content`. Returns, for each table of
    the file, its name, its id column, the rows the event held before, and the
    rows the file brought. The old event's rows go in the same
    transaction that brings the new ones, so a snapshot that fails changes
    nothing."""
    before = {
        name: (columns[0], len(rows))
        for name, (columns, rows) in tables(event, False).items()
    }
    with tempfile.TemporaryDirectory(prefix="derbynet-snapshot-") as folder:
        path = Path(folder) / "snapshot.xml"
        path.write_bytes(content)
        try:
            from transfer.legacy.source import read_source

            source = read_source(path)
        except SourceError as error:
            raise PutFailure("json-parsing", "Failed to parse JSON text.") from error
        if source.kind != "snapshot":
            raise PutFailure("json-parsing", "Failed to parse JSON text.")
        try:
            import_derbynet(path, event_name=event.name, replace=True)
        except (ImportFailed, SourceError) as error:
            raise PutFailure("import-failed", str(error)) from error
        return [
            (table, *before.get(table, ("", 0)), len(rows))
            for table, rows in (
                (_CANONICAL.get(name, name), rows)
                for name, rows in source.tables.items()
            )
        ]
