"""How each legacy table becomes rows of a Django model (#12 table mapping).

`TABLES` lists the legacy tables in the order their references need. Each
`Table` names its model, its legacy id column, and a `build` function that
turns one schema-10 row into the model's field values, or raises `Skip` to
drop the row with a reason for the report. `load_tables()` runs them, writes
the rows, and records each legacy id in the context, which later tables and
`transfer.LegacyId` read.
"""

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.db import connection, models
from django.utils import timezone

from awards.models import Award, AwardType, BallotAward, Vote, Voter
from core.models import ActionLog
from kiosks.models import Kiosk, Scene, SceneKiosk
from racing.models import HeatLane, PlaylistEntry, RosterEntry, Round
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Racer,
    Subgroup,
)

from . import values
from .photos import CAR_PHOTOS, HEADSHOTS
from .report import Kind
from .values import BadValue

EVENT_CHECKIN = 1  # `website/inc/events.inc`


class Skip(Exception):
    """Drop this row. The message says why, for the report."""


def label(model):
    return model._meta.label_lower


class Context:
    """One import's state: the event, the upgraded rows, and the id map."""

    def __init__(self, event, tables, report, zone, photos):
        self.event = event
        self.tables = tables
        self.report = report
        self.zone = zone
        self.photos = photos
        self.ids = defaultdict(dict)  # model label -> {legacy id: new id}
        self.checkins = _checkins_from_events(self)

    def ref(self, model, value, column):
        """The new id for a required legacy reference, or `Skip`."""
        legacy = values.ref_id(value)
        if legacy is None:
            raise Skip(f"{column} is empty")
        try:
            return self.ids[label(model)][legacy]
        except KeyError:
            raise Skip(f"{column} {legacy} names no imported row") from None

    def optional_ref(self, model, value):
        """`(new id, legacy id)` for a reference that may be empty.

        The new id is None when the reference is empty or names a row that
        was not imported.
        """
        legacy = values.ref_id(value)
        if legacy is None:
            return None, None
        return self.ids[label(model)].get(legacy), legacy

    def time(self, value, table, legacy_id, column):
        moment, ambiguous = values.local_time(value, self.zone)
        if ambiguous:
            self.report.add(
                Kind.AMBIGUOUS_TIME,
                table,
                legacy_id,
                f"{column} {value!r} happens twice at the end of daylight time;"
                " read as the first",
            )
        return moment


def _checkins_from_events(ctx):
    """Racer id -> check-in time from the latest EVENT_CHECKIN row.

    PHP lists check-ins by each racer's `MAX(seq)` (`query.racer.list.inc`).
    The times come from `tstamp`, moved just after the previous racer's when
    the clock went back, so they keep that order. Each move is reported.
    """
    latest = {}  # racer id -> (seq, tstamp)
    for event in ctx.tables["events"]:
        try:
            if values.integer(event["action"]) != EVENT_CHECKIN:
                continue
            racerid = values.integer(event["racerid"])
            seq = values.integer(event["seq"])
        except BadValue as error:
            ctx.report.add(Kind.DROPPED_ROW, "events", event.get("seq"), str(error))
            continue
        if racerid is not None and (racerid not in latest or seq > latest[racerid][0]):
            latest[racerid] = (seq, event["tstamp"])
    checkins, previous = {}, None
    for racerid, (seq, tstamp) in sorted(latest.items(), key=lambda item: item[1][0]):
        try:
            moment = ctx.time(tstamp, "events", seq, "tstamp")
        except BadValue as error:
            ctx.report.add(Kind.DROPPED_ROW, "events", seq, str(error))
            continue
        if moment is None:
            continue
        if previous is not None and moment <= previous:
            moved = previous + timedelta(microseconds=1)
            if moment.replace(microsecond=0) != previous.replace(microsecond=0):
                ctx.report.add(
                    Kind.CHANGED_VALUE,
                    "events",
                    seq,
                    f"tstamp {tstamp!r} is before the previous check-in; racer"
                    f" {racerid} keeps its place in PHP's order at {moved}",
                )
            moment = moved
        checkins[racerid] = previous = moment
    return checkins


@dataclass(frozen=True)
class Table:
    name: str
    model: type[models.Model]
    id_column: str | None
    build: Callable[[dict, Context], dict]
    rows: Callable[[Context], list[dict]] | None = None
    # Fields whose values must be unique within the event. A later duplicate
    # is dropped.
    unique: tuple[str, ...] = ()
    # The report's name for a row of a table without an id column.
    key: Callable[[dict], object] | None = None
    # Runs with the legacy ids of the rows that were written.
    after: Callable[[Context, list], None] | None = None

    def legacy_key(self, row):
        if self.id_column:
            return row.get(self.id_column)
        return self.key(row) if self.key else None


def _non_negative(value):
    number = values.integer(value)
    return None if number is None or number < 0 else number


# Rows with a NULL sort order: classes and ranks at schema 1, which PHP's
# `query.database.check` flags. Each gets the next free value in id order.
def _fill_sort_order(ctx, table, id_column, scope=None):
    rows = ctx.tables[table]
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(scope) if scope else None].append(row)
    for members in groups.values():
        taken = [values.integer_or_none(r["sortorder"]) for r in members]
        next_value = max((v for v in taken if v is not None), default=0) + 1
        for row in sorted(members, key=lambda r: values.id_order(r[id_column])):
            if values.integer_or_none(row["sortorder"]) is None:
                ctx.report.add(
                    Kind.CHANGED_VALUE,
                    table,
                    row[id_column],
                    f"sortorder {row['sortorder']!r} is empty or unreadable;"
                    f" set to {next_value}",
                )
                row["sortorder"] = next_value
                next_value += 1
    return rows


def _rename_duplicates(ctx, rows, table, id_column, column, fold, max_length):
    """Rename later rows whose name collides, as a unique constraint needs.

    Names are cut to the column's length first, so the cut cannot make two
    of them collide again.
    """
    seen = set()
    for row in sorted(rows, key=lambda r: values.id_order(r[id_column])):
        name, n = values.text(row[column]), 1
        candidate = name[:max_length]
        while fold(candidate) in seen:
            n += 1
            suffix = f" ({n})"
            candidate = name[: max_length - len(suffix)] + suffix
        if candidate != name:
            reasons = []
            if len(name) > max_length:
                reasons.append(f"is longer than {max_length} characters")
            if n > 1:
                reasons.append("matches an earlier name with case ignored")
            ctx.report.add(
                Kind.CHANGED_VALUE,
                table,
                row[id_column],
                f"{column} {name!r} {' and '.join(reasons)}; renamed {candidate!r}",
            )
            row[column] = candidate
        seen.add(fold(candidate))
    return rows


def _classes(ctx):
    rows = _fill_sort_order(ctx, "classes", "classid")
    # Postgres folds non-ASCII case for the unique index, PHP's NOCASE does not.
    return _rename_duplicates(
        ctx,
        rows,
        "classes",
        "classid",
        "class",
        str.lower,
        Group._meta.get_field("name").max_length,
    )


def _group(row, ctx):
    return {
        "name": values.text(row["class"]),
        "sort_order": values.integer(row["sortorder"]),
        "durable": values.truthy(row["durable"]),
        "n_trophies": _non_negative(row["ntrophies"]),
    }


def _ranks(ctx):
    return _fill_sort_order(ctx, "ranks", "rankid", scope="classid")


def _subgroup(row, ctx):
    return {
        "group_id": ctx.ref(Group, row["classid"], "classid"),
        "name": values.text(row["rank"]),
        "sort_order": values.integer(row["sortorder"]),
        "n_trophies": _non_negative(row["ntrophies"]),
    }


def _partitions(ctx):
    rows = _fill_sort_order(ctx, "partitions", "partitionid")
    # PHP's UNIQUE compares the exact name; Django ignores case (D31).
    return _rename_duplicates(
        ctx,
        rows,
        "partitions",
        "partitionid",
        "name",
        Partition.name_key,
        Partition._meta.get_field("name").max_length,
    )


def _partition(row, ctx):
    return {
        "name": values.text(row["name"]),
        "subgroup_id": ctx.ref(Subgroup, row["rankid"], "rankid"),
        "sort_order": values.integer(row["sortorder"]),
    }


def _constituent_edges(ctx):
    """The union of the `constituents` CSV and the `ConstituentClasses` rows.

    A restore from a snapshot can merge `ConstituentClasses` edges (#14), so
    the importer keeps both and reports each aggregate where they differ.
    """
    from_csv = defaultdict(set)
    for group in ctx.tables["classes"]:
        try:
            dependent = values.integer(group["classid"])
            for antecedent in values.id_list(group["constituents"]):
                from_csv[dependent].add(antecedent)
        except BadValue as error:
            ctx.report.add(
                Kind.DROPPED_ROW,
                "classes",
                group["classid"],
                f"constituents {group['constituents']!r}: {error}",
            )
    from_rows = defaultdict(set)
    for edge in ctx.tables["constituentclasses"]:
        try:
            from_rows[values.integer(edge["dependent"])].add(
                values.integer(edge["antecedent"])
            )
        except BadValue as error:
            ctx.report.add(Kind.DROPPED_ROW, "constituentclasses", None, str(error))
    edges = []
    for dependent in sorted(set(from_csv) | set(from_rows), key=lambda d: d or 0):
        csv, rows = from_csv[dependent], from_rows[dependent]
        if csv != rows:
            ctx.report.add(
                Kind.CONSTITUENT_MISMATCH,
                "classes",
                dependent,
                f"constituents CSV {sorted(csv, key=str)} but ConstituentClasses"
                f" rows {sorted(rows, key=str)}; importing both",
            )
        edges += [
            {"antecedent": antecedent, "dependent": dependent}
            for antecedent in sorted(csv | rows, key=lambda a: a or 0)
        ]
    return edges


def _group_constituent(row, ctx):
    if row["antecedent"] == row["dependent"]:
        raise Skip("a group cannot be its own constituent")
    return {
        "aggregate_id": ctx.ref(Group, row["dependent"], "dependent"),
        "constituent_id": ctx.ref(Group, row["antecedent"], "antecedent"),
    }


def _subgroup_edges(ctx):
    edges = []
    for group in ctx.tables["classes"]:
        try:
            edges += [
                {"classid": group["classid"], "rankid": rankid}
                for rankid in values.id_list(group["rankids"])
            ]
        except BadValue as error:
            ctx.report.add(
                Kind.DROPPED_ROW,
                "classes",
                group["classid"],
                f"rankids {group['rankids']!r}: {error}",
            )
    return edges


def _group_constituent_subgroup(row, ctx):
    return {
        "aggregate_id": ctx.ref(Group, row["classid"], "classid"),
        "subgroup_id": ctx.ref(Subgroup, row["rankid"], "rankid"),
    }


def _required(value, column):
    number = values.integer(value)
    if number is None:
        raise Skip(f"{column} is empty")
    return number


def _racer(row, ctx):
    racerid = values.integer(row["racerid"])
    fields = {
        "car_number": _required(row["carnumber"], "carnumber"),
        "car_name": values.text(row["carname"]),
        "first_name": values.text(row["firstname"]),
        "last_name": values.text(row["lastname"]),
        "group_id": ctx.ref(Group, row["classid"], "classid"),
        "subgroup_id": ctx.ref(Subgroup, row["rankid"], "rankid"),
        "partition_id": ctx.ref(Partition, row["partitionid"], "partitionid"),
        "passed_inspection": values.truthy(row["passedinspection"]),
        "checked_in_at": (
            values.unix_time(row["checkin_time"], scale=1000)
            or ctx.checkins.get(racerid)
        ),
        "note": values.text(row["note"]),
        "exclude": values.truthy(row["exclude"]),
    }
    # Last, so a racer dropped here queues no photo. Photos are written only
    # for the racers that are imported.
    fields["headshot"] = ctx.photos.rebase(racerid, HEADSHOTS, row["imagefile"])
    fields["car_photo"] = ctx.photos.rebase(racerid, CAR_PHOTOS, row["carphoto"])
    return fields


def _round(row, ctx):
    number = _required(row["round"], "round")
    if number < 1:
        raise Skip(f"round number {number} is below 1")
    return {"group_id": ctx.ref(Group, row["classid"], "classid"), "number": number}


def _roster_entry(row, ctx):
    return {
        "round_id": ctx.ref(Round, row["roundid"], "roundid"),
        "racer_id": ctx.ref(Racer, row["racerid"], "racerid"),
    }


def _heat_lane(row, ctx):
    if values.ref_id(row["racerid"]) is None:
        raise Skip("racerid is empty, which a schema-1 database allows")
    return {
        "round_id": ctx.ref(Round, row["roundid"], "roundid"),
        "heat": _required(row["heat"], "heat"),
        "lane": _required(row["lane"], "lane"),
        "racer_id": ctx.ref(Racer, row["racerid"], "racerid"),
        "finish_time": values.number(row["finishtime"]),
        "finish_place": values.integer(row["finishplace"]),
        "completed": ctx.time(
            row["completed"], "racechart", row["resultid"], "completed"
        ),
        "master_heat": values.integer(row["masterheat"]) or 0,
    }


def _scenes(ctx):
    # The importer brings the legacy scenes, so new_event()'s default ones go.
    Scene.objects.filter(event=ctx.event).delete()
    return _fill_sort_order(ctx, "scenes", "sceneid")


def _scene(row, ctx):
    return {
        "name": values.text(row["name"]),
        "sort_order": values.integer(row["sortorder"]),
    }


def _page(ctx, table, legacy_id, raw):
    try:
        page, params, reload = values.kiosk_page(raw)
    except BadValue as error:
        page, params, reload = values.kiosk_page(values.text(raw).partition("#")[0])
        ctx.report.add(
            Kind.CHANGED_VALUE, table, legacy_id, f"page {raw!r}: {error}; dropped"
        )
    if page.startswith("local/"):
        ctx.report.add(
            Kind.LOCAL_KIOSK_PAGE,
            table,
            legacy_id,
            f"page {page!r} is a local PHP kiosk, which Django cannot show",
        )
    return page, params, reload


def _scene_kiosk(row, ctx):
    scene_id = ctx.ref(Scene, row["sceneid"], "sceneid")
    page, params, _ = _page(ctx, "scenekiosk", row["scenekioskid"], row["page"])
    return {
        "scene_id": scene_id,
        "kiosk_name": values.text(row["kiosk_name"]),
        "page": page,
        "params": params,
    }


def _playlist_entry(row, ctx):
    round_number = _required(row["round"], "round")
    scene, legacy_scene = ctx.optional_ref(Scene, row["sceneid_at_finish"])
    fields = {
        "seq": _required(row["seq"], "seq"),
        "group_id": ctx.ref(Group, row["classid"], "classid"),
        "round_number": round_number,
        "bucket_limit": max(values.integer(row["bucket_limit"]) or 0, 0),
        "bucketed": values.truthy(row["bucketed"]),
        # PHP schedules the round only when this is positive (`playlist.inc`),
        # and playlist.add stores -1 for "don't".
        "n_times_per_lane": max(values.integer(row["n_times_per_lane"]) or 0, 0),
        "scene_at_finish_id": scene,
        "continue_racing": values.truthy(row["continue_racing"]),
    }
    if legacy_scene is not None and scene is None:
        ctx.report.add(
            Kind.CHANGED_VALUE,
            "playlist",
            row["queueid"],
            f"sceneid_at_finish {legacy_scene} names no scene; cleared",
        )
    return fields


def _award(row, ctx):
    award_type = values.integer(row["awardtypeid"])
    if award_type not in ctx.award_types:
        raise Skip(f"awardtypeid {award_type} is not an award type")
    group, legacy_group = ctx.optional_ref(Group, row["classid"])
    subgroup, legacy_subgroup = ctx.optional_ref(Subgroup, row["rankid"])
    racer, legacy_racer = ctx.optional_ref(Racer, row["racerid"])
    if (legacy_group is not None and group is None) or (
        legacy_subgroup is not None and subgroup is None
    ):
        # D15: an award of a deleted group or subgroup becomes pack-wide.
        ctx.report.add(
            Kind.UNSCOPED_AWARD,
            "awards",
            row["awardid"],
            f"classid {legacy_group} and rankid {legacy_subgroup} name a deleted"
            " group or subgroup; imported pack-wide",
        )
        group = subgroup = None
    if legacy_racer is not None and racer is None:
        ctx.report.add(
            Kind.CHANGED_VALUE,
            "awards",
            row["awardid"],
            f"racerid {legacy_racer} names no racer; imported with no winner",
        )
    return {
        "name": values.text(row["awardname"]),
        "award_type_id": award_type,
        "group_id": group,
        "subgroup_id": subgroup,
        "racer_id": racer,
        "sort": values.integer(row["sort"]) or 0,
    }


def _ballot_award(row, ctx):
    return {
        "award_id": ctx.ref(Award, row["awardid"], "awardid"),
        "depth": _required(row["depth"], "depth"),
    }


def _voter(row, ctx):
    return {
        "identifier": values.text(row["identifier"]),
        "ip_address": values.text(row["ip_addr"]),
        "cast_at": ctx.time(row["when_cast"], "voters", row["voterid"], "when_cast"),
    }


def _vote(row, ctx):
    return {
        "voter_id": ctx.ref(Voter, row["voterid"], "voterid"),
        "award_id": ctx.ref(Award, row["awardid"], "awardid"),
        "racer_id": ctx.ref(Racer, row["racerid"], "racerid"),
    }


def _kiosk(row, ctx):
    address = values.text(row["address"])
    if not address:
        raise Skip("address is empty")
    page, params, reload = _page(ctx, "kiosks", address, row["page"])
    return {
        "address": address,
        "name": values.text(row["name"]),
        "page": page,
        "params": params,
        "reload_requested": reload,
        "last_contact": values.unix_time(row["last_contact"]),
    }


def _action_log(row, ctx):
    request = values.text(row["request"])
    try:
        # Postgres's jsonb refuses an escaped NUL character.
        parsed = json.loads(request.replace("\\u0000", ""))
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        ctx.report.add(
            Kind.UNPARSED_ACTION,
            "actionhistory",
            row["historyid"],
            "request is not a JSON object; kept as {'unparsed': text}",
        )
        parsed = {"unparsed": request}
    received = ctx.time(row["received"], "actionhistory", row["historyid"], "received")
    # after_update_schema() logs the partition structure at upgrade time.
    return {"received": received or timezone.now(), "request": parsed}


TABLES = [
    Table("classes", Group, "classid", _group, rows=_classes),
    Table("ranks", Subgroup, "rankid", _subgroup, rows=_ranks),
    Table("partitions", Partition, "partitionid", _partition, rows=_partitions),
    Table(
        "constituentclasses",
        GroupConstituent,
        None,
        _group_constituent,
        rows=_constituent_edges,
        unique=("aggregate_id", "constituent_id"),
        key=lambda row: f"{row['antecedent']}->{row['dependent']}",
    ),
    Table(
        "classes.rankids",
        GroupConstituentSubgroup,
        None,
        _group_constituent_subgroup,
        rows=_subgroup_edges,
        unique=("aggregate_id", "subgroup_id"),
        key=lambda row: f"{row['classid']}:{row['rankid']}",
    ),
    Table(
        "registrationinfo",
        Racer,
        "racerid",
        _racer,
        after=lambda ctx, ids: ctx.photos.stage(ids),
    ),
    Table("rounds", Round, "roundid", _round, unique=("group_id", "number")),
    Table(
        "roster",
        RosterEntry,
        "rosterid",
        _roster_entry,
        unique=("round_id", "racer_id"),
    ),
    Table(
        "racechart",
        HeatLane,
        "resultid",
        _heat_lane,
        unique=("round_id", "heat", "lane"),
    ),
    Table("scenes", Scene, "sceneid", _scene, rows=_scenes),
    Table(
        "scenekiosk",
        SceneKiosk,
        "scenekioskid",
        _scene_kiosk,
        unique=("scene_id", "kiosk_name"),
    ),
    Table("playlist", PlaylistEntry, "queueid", _playlist_entry, unique=("seq",)),
    Table("awards", Award, "awardid", _award),
    Table(
        "ballotawards",
        BallotAward,
        None,
        _ballot_award,
        unique=("award_id",),
        key=lambda row: row["awardid"],
    ),
    Table("voters", Voter, "voterid", _voter),
    Table(
        "votes",
        Vote,
        "voteid",
        _vote,
        unique=("voter_id", "award_id", "racer_id"),
    ),
    Table(
        "kiosks",
        Kiosk,
        None,
        _kiosk,
        unique=("address",),
        key=lambda row: row["address"],
    ),
    Table("actionhistory", ActionLog, "historyid", _action_log),
]


def _fit(model, fields, ctx, table, legacy_id):
    """Truncate text past a column's length, and refuse out-of-range integers.

    SQLite never enforced either, and Postgres does.
    """
    for field in model._meta.concrete_fields:
        value = fields.get(field.attname)
        if value is None:
            continue
        if isinstance(field, models.CharField) and len(value) > field.max_length:
            ctx.report.add(
                Kind.CHANGED_VALUE,
                table,
                legacy_id,
                f"{field.name} {value!r} is longer than {field.max_length}; truncated",
            )
            fields[field.attname] = value[: field.max_length]
        elif isinstance(field, models.IntegerField) and not field.is_relation:
            low, high = connection.ops.integer_field_range(field.get_internal_type())
            if (low is not None and value < low) or (high is not None and value > high):
                raise Skip(f"{field.name} {value} is out of range")
    return fields


def load_tables(ctx):
    """Write every table's rows, and return the rows for `transfer.LegacyId`."""
    ctx.award_types = set(AwardType.objects.values_list("id", flat=True))
    legacy_ids = []
    for table in TABLES:
        rows = table.rows(ctx) if table.rows else ctx.tables.get(table.name, [])
        built, seen = [], set()
        for row in rows:
            legacy_id = table.legacy_key(row)
            try:
                fields = _fit(
                    table.model, table.build(row, ctx), ctx, table.name, legacy_id
                )
                if table.id_column:
                    legacy_id = _required(row[table.id_column], table.id_column)
            except (Skip, BadValue) as reason:
                ctx.report.add(Kind.DROPPED_ROW, table.name, legacy_id, str(reason))
                continue
            if table.unique:
                key = tuple(fields[name] for name in table.unique)
                if key in seen:
                    ctx.report.add(
                        Kind.DROPPED_ROW,
                        table.name,
                        legacy_id,
                        f"duplicates an earlier row on {', '.join(table.unique)}",
                    )
                    continue
                seen.add(key)
            if table.id_column and legacy_id in ctx.ids[label(table.model)]:
                ctx.report.add(
                    Kind.DROPPED_ROW, table.name, legacy_id, "duplicates an earlier id"
                )
                continue
            built.append((legacy_id, table.model(event=ctx.event, **fields)))
            if table.id_column:
                # Reserved now, so a later duplicate id is caught above.
                ctx.ids[label(table.model)][legacy_id] = None
        table.model.objects.bulk_create([obj for _, obj in built])
        for legacy_id, obj in built:
            if table.id_column:
                ctx.ids[label(table.model)][legacy_id] = obj.pk
                legacy_ids.append((label(table.model), legacy_id, obj.pk))
        if table.after:
            table.after(ctx, [legacy_id for legacy_id, _ in built])
        ctx.report.count(table.name, len(rows), len(built))
    return legacy_ids
