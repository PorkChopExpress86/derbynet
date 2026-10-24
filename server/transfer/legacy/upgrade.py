"""The legacy schema upgrade, as pure functions over row dicts.

PHP upgrades a database by running `website/sql/sqlite/update-schema.inc` and
then `after_update_schema()` (`website/inc/update_schema.inc`). `upgrade()`
does the same to the rows of a `Source`, without DDL, and returns them in the
schema-10 shape. A column that a source lacks gets the default its
`CREATE TABLE` or `ALTER TABLE ... ADD COLUMN` gives it. Snapshots also lack
some columns at schema 10 (`export-schema.inc`), and get the same defaults.
"""

import json

from core.services import KIOSK_NAMES, LEGACY_SCENES

# Every column the importer reads, with its default when a source lacks it.
COLUMNS = {
    "classes": {
        "classid": None,
        "class": "",
        "constituents": "",
        "rankids": "",
        "durable": None,
        "ntrophies": -1,
        "sortorder": None,
    },
    "ranks": {
        "rankid": None,
        "rank": "",
        "classid": None,
        "ntrophies": -1,
        "sortorder": None,
    },
    "partitions": {
        "partitionid": None,
        "name": None,
        "rankid": None,
        "sortorder": None,
    },
    "constituentclasses": {"antecedent": None, "dependent": None},
    "registrationinfo": {
        "racerid": None,
        "carnumber": None,
        "carname": None,
        "lastname": "",
        "firstname": "",
        "classid": None,
        "rankid": None,
        "partitionid": None,
        "passedinspection": 0,
        "imagefile": None,
        "carphoto": None,
        "checkin_time": None,
        "note": None,
        "exclude": 0,
    },
    "rounds": {"roundid": None, "round": None, "classid": None},
    "roster": {"rosterid": None, "roundid": None, "racerid": None},
    "racechart": {
        "resultid": None,
        "roundid": None,
        "heat": None,
        "lane": None,
        "racerid": None,
        "finishtime": None,
        "finishplace": None,
        "completed": None,
        "masterheat": 0,
    },
    "playlist": {
        "queueid": None,
        "seq": None,
        "classid": None,
        "round": None,
        "bucket_limit": None,
        "bucketed": None,
        "n_times_per_lane": None,
        "sceneid_at_finish": None,
        "continue_racing": None,
    },
    "awards": {
        "awardid": None,
        "awardname": "",
        "awardtypeid": 1,
        "classid": None,
        "rankid": None,
        "racerid": 0,
        "sort": 0,
    },
    "ballotawards": {"ballotid": None, "awardid": None, "depth": None},
    "voters": {
        "voterid": None,
        "identifier": None,
        "when_cast": None,
        "ip_addr": None,
    },
    "votes": {"voteid": None, "awardid": None, "racerid": None, "voterid": None},
    "kiosks": {"address": None, "name": None, "page": None, "last_contact": None},
    "scenes": {"sceneid": None, "name": None, "sortorder": None},
    "scenekiosk": {
        "scenekioskid": None,
        "sceneid": None,
        "kiosk_name": None,
        "page": None,
    },
    "actionhistory": {"historyid": None, "received": None, "request": None},
    "raceinfo": {"raceinfoid": None, "itemkey": None, "itemvalue": None},
    "events": {"seq": None, "tstamp": None, "action": None, "racerid": None},
}

# `website/inc/partitions.inc`
DEFAULT_PARTITION_NAME = "Default"
NOT_SPECIFIED_RANK = "* Not specified"
PARTITION_SCHEMA = 8


def upgrade(tables, version):
    """Return a copy of `tables` (from a schema-`version` source) at schema 10."""
    tables = {name: [dict(row) for row in rows] for name, rows in tables.items()}

    # update-schema.inc creates these tables only when they are missing, and
    # drops and recreates the rest, so a table it recreates starts empty.
    if version < 4:
        if "scenes" not in tables:
            tables["scenes"], tables["scenekiosk"] = _default_scenes()
        tables.setdefault("playlist", [])
    if version < 6:
        for name in ("ballotawards", "voters", "votes"):
            tables[name] = []
    if version < 8:
        tables["partitions"] = []
        tables.setdefault("constituentclasses", [])
    if version < 10:
        tables["actionhistory"] = []

    for name, defaults in COLUMNS.items():
        for row in tables.setdefault(name, []):
            for column, default in defaults.items():
                row.setdefault(column, default)

    if version < PARTITION_SCHEMA:
        after_update_schema(tables)
    return tables


def _default_scenes():
    """The rows of `website/sql/sqlite/scene-tables.inc`."""
    scenes, kiosks = [], []
    for sceneid, (name, *pages) in enumerate(LEGACY_SCENES, start=1):
        scenes.append({"sceneid": sceneid, "name": name, "sortorder": sceneid})
        for kiosk_name, page in zip(KIOSK_NAMES, pages, strict=True):
            if page or kiosk_name == "Main":
                kiosks.append(
                    {
                        "scenekioskid": len(kiosks) + 1,
                        "sceneid": sceneid,
                        "kiosk_name": kiosk_name,
                        "page": page,
                    }
                )
    return scenes, kiosks


def after_update_schema(tables):
    """`after_update_schema()` for a source older than schema 8.

    Each subgroup becomes a partition with the subgroup's id, every racer joins
    its subgroup's partition, the partition structure is logged as PHP's
    `record_action_partition_structure()` logs it, and the `constituents` CSV
    becomes `ConstituentClasses` rows.
    """
    tables["partitions"] = [
        {
            "partitionid": rank["rankid"],
            "name": (
                DEFAULT_PARTITION_NAME
                if rank["rank"] == NOT_SPECIFIED_RANK
                else rank["rank"]
            ),
            "rankid": rank["rankid"],
            "sortorder": rank["sortorder"],
        }
        for rank in tables["ranks"]
    ]
    for racer in tables["registrationinfo"]:
        racer["partitionid"] = racer["rankid"]

    tables["actionhistory"].append(
        {
            "historyid": _next_id(tables["actionhistory"], "historyid"),
            "received": None,
            "request": _partition_structure(tables),
        }
    )

    for group in tables["classes"]:
        if group["constituents"]:
            for antecedent in str(group["constituents"]).split(","):
                edge = {"antecedent": antecedent, "dependent": group["classid"]}
                if edge not in tables["constituentclasses"]:
                    tables["constituentclasses"].append(edge)


def _next_id(rows, column):
    return max((_int(row[column]) or 0 for row in rows), default=0) + 1


def _int(value):
    """An id as PHP compares it. Snapshots from PHP before 8.1 hold strings."""
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _partition_structure(tables):
    """The request that `record_action_partition_structure()` writes, as JSON."""
    classes = {_int(group["classid"]) for group in tables["classes"]}
    round1 = {}
    for round_ in sorted(tables["rounds"], key=lambda r: _int(r["roundid"]) or 0):
        if _int(round_["round"]) == 1:
            round1.setdefault(_int(round_["classid"]), []).append(
                _int(round_["roundid"])
            )
    ranks = {_int(rank["rankid"]): rank for rank in tables["ranks"]}

    partitions = []
    for partition in sorted(
        tables["partitions"], key=lambda p: _int(p["partitionid"]) or 0
    ):
        rank = ranks.get(_int(partition["rankid"]))
        if rank is None or _int(rank["classid"]) not in classes:
            continue
        for roundid in round1.get(_int(rank["classid"]), []):
            partitions.append(
                {
                    "partitionid": _int(partition["partitionid"]),
                    "classid": _int(rank["classid"]),
                    "rankid": _int(rank["rankid"]),
                    "roundid": roundid,
                }
            )
    request = {"action": "partition.*structure"}
    if _raceinfo(tables, "group-formation-rule", "by-partition") == "one-group":
        request["class"] = "All Racers"
    # Every subgroup has a partition by now, so no subgroup lacks one.
    request["partitions"] = partitions
    request["ranks"] = []
    return json.dumps(request, separators=(",", ":"), ensure_ascii=False)


def _raceinfo(tables, key, default):
    rows = [row for row in tables["raceinfo"] if row["itemkey"] == key]
    return rows[0]["itemvalue"] if rows else default
