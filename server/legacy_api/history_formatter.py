"""The race history: logged actions as the sentences `history.php` shows (#36).

Ported from `website/inc/history-formatter.inc`. The formatter reads actions
in the order they were received and replays the ones that create, change or
delete groups, subgroups, partitions, rounds and racers into small tables of
its own, so that a later `racer.delete` or `heat.select` can name what it
acts on. It keys on the raw parameter names PHP logged, so a rename in
another ticket breaks the history of imported events.

PHP's tables return copies, so an `edit` action changes nothing the tables
keep, and `partition.*structure` leaves bare ids behind. Those effects are
kept, since the text for imported rows must match PHP's. PHP's debug mode,
which prints a row for every action that has no sentence, is not ported.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from legacy_api.xml import php_escape

_NUMBER = re.compile(r"\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*")

NO_SENTENCE = (
    "award.*delete award.adhoc award.delete award.edit award.import award.order"
    " award.present award.winner award.xbs kiosk.assign partition.order"
    " photo.assign photo.crop photo.delete photo.rotate photo.upload"
    " replay.message replay.test scene.apply"
).split()


def _s(value) -> str:
    """PHP's text for a value in a string: null is empty."""
    if value is None:
        return ""
    if value is True:
        return "1"
    if value is False:
        return ""
    return str(value)


def _number(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and _NUMBER.fullmatch(value):
        number = float(value)
        return int(number) if number == int(number) and "." not in value else number
    return None


def _equal(a, b) -> bool:
    """PHP 8's `==` for the scalars an action holds."""
    x, y = _number(a), _number(b)
    if x is not None and y is not None:
        return x == y
    return _s(a) == _s(b)


def _id(value) -> int:
    """`intval()` of an id."""
    number = _number(value)
    return 0 if number is None else int(number)


class _Table:
    """`SimulatedSqliteTable`: rows numbered from 1 by id, with holes."""

    def __init__(self, id_column: str):
        self.id_column = id_column
        self.entries: list[dict | None] = []

    def insert_with_id(self, row: dict) -> dict:
        index = _id(row.get(self.id_column))
        if len(self.entries) + 1 <= index:
            while len(self.entries) + 1 < index:
                self.entries.append(None)
            self.entries.append(row)
        elif index >= 1:
            self.entries[index - 1] = row
        return row

    def get(self, id_) -> dict | None:
        number = _number(id_)
        if number is not None and 0 < number <= len(self.entries):
            row = self.entries[int(number) - 1]
            return None if row is None else dict(row)
        return None

    def get_or_error(self, id_) -> dict | None:
        number = _id(id_)
        row = self.get(number) if number > 0 else None
        return row or None

    def find(self, column: str, value) -> dict | None:
        for row in self.entries:
            if row and _equal(row.get(column), value):
                return dict(row)
        return None

    def delete(self, id_) -> None:
        number = _number(id_)
        if number is None or number <= 0:
            return
        if number == len(self.entries):
            self.entries.pop()
        elif number < len(self.entries):
            self.entries[int(number) - 1] = None

    def clear(self) -> None:
        self.entries = []


@dataclass(frozen=True)
class Entry:
    """One row of the history that has a sentence."""

    heat: str
    received: object
    sentence: str


class HistoryFormatter:
    def __init__(self):
        self.racers = _Table("racerid")
        self.classes = _Table("classid")
        self.ranks = _Table("rankid")
        self.partitions = _Table("partitionid")
        self.rounds = _Table("roundid")
        self.settings: dict = {}
        self.round_id = None
        self.heat = None
        self.action: dict = {}
        self.action_string = ""

    # ----- the action being read -----

    def set_action(self, request: Mapping) -> None:
        """PHP json-decodes the stored text and escapes each top-level string."""
        self.action_string = php_escape(
            json.dumps(request, separators=(",", ":")).replace("/", "\\/")
        )
        self.action = {
            key: php_escape(value) if isinstance(value, str) else value
            for key, value in request.items()
        }

    def _get(self, key, default=None):
        return self.action.get(key, default)

    def current_heat_string(self) -> str:
        if (
            self._shows_current_heat()
            and self.round_id is not None
            and self.heat is not None
        ):
            return self._heat(self.round_id, self.heat)
        return ""

    def _shows_current_heat(self) -> bool:
        if self._get("action") == "timer-message":
            return self._get("message") in ("STARTED", "FINISHED", "MALFUNCTION")
        return self._get("action") == "result.write"

    # ----- words -----

    def _partition_label(self, lower: bool = False) -> str:
        label = self.settings.get("partition-label") or "Den"
        return _s(label).lower() if lower else _s(label)

    def _racer(self, racer: Mapping | None) -> str:
        racer = racer or {}
        text = f"<b>{_s(racer.get('firstname'))} {_s(racer.get('lastname'))}</b>"
        if racer.get("carnumber") is not None:
            text += f", {_s(racer['carnumber'])}"
        if racer.get("rankid") is not None:
            rank = self.ranks.get_or_error(racer["rankid"])
            if rank:
                text += ", " + _s(rank.get("rank"))
        elif racer.get("classid") is not None:
            group = self.classes.get_or_error(racer["classid"])
            if group:
                text += ", " + _s(group.get("class"))
        elif racer.get("partition") is not None:
            text += ", " + _s(racer["partition"])
        return text

    def _round(self, round_id) -> str:
        round_ = self.rounds.get_or_error(round_id)
        if not round_:
            return f"Roundid <b>{_s(round_id)}</b>"
        group = self.classes.get_or_error(round_.get("classid"))
        if not group:
            group = {"class": f"<b>No class {_s(round_.get('classid'))}</b>"}
        number = (
            ""
            if _equal(round_.get("round"), 1)
            else f", round {_s(round_.get('round'))}"
        )
        return f"{_s(group.get('class'))}{number}"

    def _heat(self, round_id, heat) -> str:
        return f"{self._round(round_id)}, heat {_s(heat)}"

    def _lane_results(self, message: str) -> str:
        any_times = False
        for key, value in self.action.items():
            if key.startswith("lane"):
                message += f" {key}: {_s(value)}s"
                any_times = True
            elif not any_times and key.startswith("place"):
                message += f" lane{key[5:]}: #{_s(value)}"
        return message

    # ----- the sentence -----

    def process(self) -> str:
        name = _s(self._get("action"))
        if name in NO_SENTENCE or name not in HANDLERS:
            return ""
        return getattr(self, HANDLERS[name])() or ""

    def _do_class_create(self):
        self.classes.insert_with_id(
            {"classid": self._get("classid"), "class": self._get("class")}
        )

    def _do_class_deleted(self):
        self.classes.delete(self._get("classid"))

    def _do_class_delete(self):
        group = self.classes.get_or_error(self._get("classid")) or {}
        return "Delete class " + _s(group.get("class"))

    def _do_class_edit(self):
        pass

    def _do_class_add(self):
        return "Add class " + _s(self._get("name"))

    def _do_class_order(self):
        return "Re-order racing groups"

    def _do_database_execute(self):
        if self._get("script") == "update-schema":
            return "Update Database Schema"

    def _do_database_purge(self):
        return {
            "awards": "Deleted All Awards",
            "racers": "Deleted All Racers",
            "results": "Deleted All Race Results",
            "schedules": "Deleted All Racing Schedules",
        }.get(self._get("purge"))

    def _do_heat_advance(self):
        self.round_id = self._get("roundid")
        self.heat = self._get("heat")

    def _do_heat_select(self):
        now_racing = self._get("now_racing")
        if now_racing is not None and not _truthy(now_racing):
            return "Stop racing"
        if self._get("roundid") is not None:
            if self._get("heat") is not None:
                return "Heat select: " + self._heat(
                    self._get("roundid"), self._get("heat")
                )
            return "Start racing: " + self._round(self._get("roundid"))
        if now_racing is not None:
            return "Start racing"
        return f"<div style='font-weight: bold;'>{self.action_string}</div>"

    def _do_partition_create(self):
        self.partitions.insert_with_id(
            {
                "partitionid": self._get("partitionid"),
                "name": self._get("name"),
                "rankid": self._get("rankid"),
            }
        )

    def _do_partition_structure(self):
        self.classes.clear()
        self.ranks.clear()
        self.rounds.clear()
        for part in self._get("partitions") or []:
            if self.ranks.get(part.get("rankid")) is None:
                self.ranks.insert_with_id({"rankid": part.get("rankid")})
            if self.classes.get(part.get("classid")) is None:
                self.classes.insert_with_id({"classid": part.get("classid")})
            if self.rounds.get(part.get("roundid")) is None:
                self.rounds.insert_with_id({"roundid": part.get("roundid"), "round": 1})

    def _do_partition_add(self):
        return f"Add {self._partition_label(True)} {_s(self._get('name'))}"

    def _do_partition_apply_rule(self):
        rule = self._get("rule")
        if rule == "by-partition":
            text = f"Race each {self._partition_label(True)} as a group."
        elif rule == "one-group":
            text = "Race as one big group."
        else:
            text = "Custom racing groups."
        return "Racing groups: " + text

    def _do_partition_delete(self):
        partition = self.partitions.get(self._get("partitionid")) or {}
        self.partitions.delete(self._get("partitionid"))
        return f"[DID IT] Delete partition {_s(partition.get('name'))}"

    def _do_partition_edit(self):
        partition = self.partitions.get(self._get("partitionid")) or {}
        if self._get("name") is not None:
            old = _s(partition.get("name"))
            return (
                f"[DID IT] Rename {self._partition_label(True)} {old}"
                f" to {_s(self._get('name'))}"
            )

    def _do_partition_move(self):
        partition = self.partitions.get_or_error(self._get("div_id")) or {}
        label = self._partition_label(True)
        if _id(self._get("group_id")) < 0:
            return f"Move {label} {_s(partition.get('name'))} to its own racing group"
        group = self.classes.get_or_error(self._get("group_id")) or {}
        return (
            f"Move {label} {_s(partition.get('name'))}"
            f" to racing group {_s(group.get('class'))}"
        )

    def _do_racer_create(self):
        self.racers.insert_with_id(
            {
                key: self._get(key)
                for key in (
                    "racerid firstname lastname carnumber carname partitionid rankid"
                    " classid"
                ).split()
            }
        )

    def _do_racer_deleted(self):
        self.racers.delete(self._get("racerid"))

    def _do_racer_renumber(self):
        pass

    def _do_racer_bulk(self):
        what = self._get("what")
        if what == "checkin":
            if self._get("value"):
                return "Check in racers in bulk"
            return "Cancel check-ins in bulk"
        if what == "number":
            return "Renumber racers"
        if what == "eligibility":
            return (
                "Make racers "
                + ("eligible" if self._get("value") else "ineligible")
                + " in bulk"
            )
        # PHP's switch has no break here and runs on into racer.delete.
        return self._do_racer_delete()

    def _do_racer_delete(self):
        return "Delete racer " + self._racer(self.racers.get(self._get("racer")))

    def _do_racer_edit(self):
        pass

    def _do_racer_add(self):
        if self._get("carno") is not None:
            self.action["carnumber"] = self.action["carno"]
        first_last = self._get("first-last")
        if first_last is not None:
            first_last = first_last.strip()
            self.action["first-last"] = first_last
            comma = first_last.find(",")
            if comma != -1:
                self.action["firstname"] = first_last[comma + 1 :].strip()
                self.action["lastname"] = first_last[:comma].strip()
            else:
                space = first_last.rfind(" ")
                if space != -1:
                    self.action["firstname"] = first_last[:space].strip()
                    self.action["lastname"] = first_last[space + 1 :].strip()
                else:
                    self.action["firstname"] = ""
                    self.action["lastname"] = None
        verb = "Add" if self._get("action") == "racer.add" else "Import"
        return f"{verb} racer {self._racer(self.action)}"

    def _do_racer_pass(self):
        racer = None
        if self._get("racer") is not None:
            racer = self.racers.get(self._get("racer"))
        elif self._get("barcode") is not None:
            barcode = _s(self._get("barcode"))
            if barcode.startswith("PWDid"):
                racer = self.racers.get(barcode[5:])
            else:
                racer = self.racers.find("carnumber", barcode[3:])
        value = self._get("value")
        verb = (
            "<b>Cancel</b> check-in of "
            if value is not None and not _truthy(value)
            else "Check in "
        )
        if not racer:
            return ""
        return f"{verb} {self._racer(racer)}"

    def _do_rank_create(self):
        self.ranks.insert_with_id(
            {
                "rankid": self._get("rankid"),
                "rank": self._get("rank"),
                "classid": self._get("classid"),
            }
        )

    def _do_rank_deleted(self):
        self.ranks.delete(self._get("rankid"))

    def _do_rank_add(self):
        return f"Add {self._partition_label(True)} {_s(self._get('name'))}"

    def _do_rank_delete(self):
        rank = self.ranks.get_or_error(self._get("rankid")) or {}
        return f"Delete {self._partition_label(True)} {_s(rank.get('rank'))}"

    def _do_rank_edit(self):
        return "Rank.edit " + self.action_string

    def _do_rank_move(self):
        rank = self.ranks.get_or_error(self._get("rankid")) or {}
        label = self._partition_label(True)
        if _id(self._get("classid")) < 0:
            return f"Move {label} {_s(rank.get('rank'))} into its own racing group"
        group = self.classes.get_or_error(self._get("classid")) or {}
        return (
            f"Move {label} {_s(rank.get('rank'))}"
            f" into racing group {_s(group.get('class'))}"
        )

    def _do_rank_order(self):
        return "Re-order subgroups"

    def _do_result_write(self):
        return self._lane_results("Manually-entered results:")

    def _do_round_create(self):
        self.rounds.insert_with_id(
            {
                "roundid": self._get("roundid"),
                "classid": self._get("classid"),
                "round": self._get("round"),
            }
        )

    def _do_schedule_generate(self):
        return "Generate schedule for " + self._round(self._get("roundid"))

    def _do_schedule_reschedule(self):
        if self._get("dry-run"):
            return ""
        return "Adjust schedule for " + self._round(self._get("roundid"))

    def _do_schedule_unschedule(self):
        return "Unschedule round " + self._round(self._get("roundid"))

    def _do_settings_write(self):
        message = "Write setting(s):"
        for key, value in self.action.items():
            if key != "action" and not key.endswith("-checkbox"):
                self.settings[key] = value
                message += f" <b>{key}</b>: " + json.dumps(value)
        return message

    def _do_timer_overdue(self):
        return "Timer contact lost."

    def _do_timer_message(self):
        message = self._get("message")
        if message == "HELLO":
            return "Timer makes contact"
        if message == "IDENTIFIED":
            return "Timer is identified"
        if message == "STARTED":
            return "Heat started"
        if message == "FINISHED":
            return self._lane_results("Heat finished:")


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


# Each action that can have a sentence or an effect, and what handles it.
HANDLERS = {
    "class.*create": "_do_class_create",
    "class.*delete": "_do_class_deleted",
    "class.add": "_do_class_add",
    "class.delete": "_do_class_delete",
    "class.edit": "_do_class_edit",
    "class.order": "_do_class_order",
    "database.execute": "_do_database_execute",
    "database.purge": "_do_database_purge",
    "heat.*advance": "_do_heat_advance",
    "heat.select": "_do_heat_select",
    "partition.*create": "_do_partition_create",
    "partition.*structure": "_do_partition_structure",
    "partition.add": "_do_partition_add",
    "partition.apply-rule": "_do_partition_apply_rule",
    "partition.delete": "_do_partition_delete",
    "partition.edit": "_do_partition_edit",
    "partition.move": "_do_partition_move",
    "racer.*create": "_do_racer_create",
    "racer.*delete": "_do_racer_deleted",
    "racer.*renumber": "_do_racer_renumber",
    "racer.bulk": "_do_racer_bulk",
    "racer.delete": "_do_racer_delete",
    "racer.edit": "_do_racer_edit",
    "racer.add": "_do_racer_add",
    "racer.import": "_do_racer_add",
    "racer.pass": "_do_racer_pass",
    "rank.*create": "_do_rank_create",
    "rank.*delete": "_do_rank_deleted",
    "rank.add": "_do_rank_add",
    "rank.delete": "_do_rank_delete",
    "rank.edit": "_do_rank_edit",
    "rank.move": "_do_rank_move",
    "rank.order": "_do_rank_order",
    "result.write": "_do_result_write",
    "round.*create": "_do_round_create",
    "schedule.generate": "_do_schedule_generate",
    "schedule.reschedule": "_do_schedule_reschedule",
    "schedule.unschedule": "_do_schedule_unschedule",
    "settings.write": "_do_settings_write",
    "timer.*overdue": "_do_timer_overdue",
    "timer-message": "_do_timer_message",
}


def entries(rows: Iterable[tuple[object, Mapping]]) -> list[Entry]:
    """The history of `(received, request)` rows, oldest first: every row that
    has a sentence, with the heat the timer or the scorer was on."""
    formatter = HistoryFormatter()
    found = []
    for received, request in rows:
        formatter.set_action(request)
        heat = formatter.current_heat_string()
        sentence = formatter.process()
        if sentence:
            found.append(Entry(heat, received, sentence))
    return found


def history(event) -> list[Entry]:
    """The event's history from its action log, as `history.php` reads it."""
    from core.models import ActionLog

    return entries(
        ActionLog.objects.filter(event=event)
        .order_by("received", "pk")
        .values_list("received", "request")
    )
