"""`result.import`: enter one result line, creating what it names (#36).

Ported from `website/ajax/action.result.import.inc`. Every step is
get-or-create, so importing a line twice changes nothing. A line that cannot
be stored fails with `unable`; the rest of the file's lines are separate
calls, each in its own transaction.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from core.models import Event
from core.services import lock_race_state
from racing.models import HeatLane, RosterEntry, Round
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups


@dataclass(frozen=True)
class ImportFailure(Exception):
    code: str
    description: str


def _int(raw, name: str) -> int:
    try:
        return int(str(raw).strip())
    except ValueError:
        raise ImportFailure("unable", f"{name} is not a number.") from None


def _float(raw, name: str) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        raise ImportFailure("unable", f"{name} is not a number.") from None


def _completed(raw) -> datetime | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        moment = datetime.strptime(str(raw).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise ImportFailure("unable", "completed is not a time.") from None
    return timezone.make_aware(moment)


def _group(event: Event, name: str) -> Group:
    found = Group.objects.filter(event=event, name=name).first()
    return found or groups.find_or_create_group(event.id, name, with_first_round=False)


def _subgroup(group: Group) -> Subgroup:
    """Any subgroup of the group will do, so long as there is one."""
    found = Subgroup.objects.filter(group=group).order_by("id").first()
    return found or groups.find_or_create_subgroup(group, group.name, False)


def _partition(event: Event, subgroup: Subgroup, name: str) -> Partition:
    found = Partition.objects.filter(subgroup=subgroup).order_by("id").first()
    if found:
        return found
    taken = Partition.objects.filter(event=event)
    candidate, n = name, 0
    while groups._partition_named(taken, candidate):
        n += 1
        candidate = f"{name}-{n}"
    return groups.create_partition_in_subgroup(subgroup, candidate)


def import_result(event: Event, fields: dict) -> ImportFailure | None:
    try:
        name = str(fields.get("class") or "").strip()
        if not name:
            raise ImportFailure("unable", "Unable to create entry in Classes table.")
        number = _int(fields.get("round"), "round")
        heat = _int(fields.get("heat"), "heat")
        lane = _int(fields.get("lane"), "lane")
        car_number = _int(fields.get("carnumber"), "carnumber")
        finish_time = _float(fields.get("finishtime"), "finishtime")
        finish_place = fields.get("finishplace")
        finish_place = (
            None if finish_place in (None, "") else _int(finish_place, "finishplace")
        )
        completed = _completed(fields.get("completed"))
        with transaction.atomic():
            lock_race_state(event.id)
            group = _group(event, name)
            subgroup = _subgroup(group)
            partition = _partition(event, subgroup, name)
            racer = Racer.objects.filter(
                event=event,
                group=group,
                last_name=str(fields.get("lastname", "")),
                first_name=str(fields.get("firstname", "")),
                car_number=car_number,
            ).first() or Racer.objects.create(
                event=event,
                group=group,
                subgroup=subgroup,
                partition=partition,
                last_name=str(fields.get("lastname", "")),
                first_name=str(fields.get("firstname", "")),
                car_number=car_number,
                car_name=str(fields.get("carname") or ""),
                passed_inspection=True,
            )
            round, _ = Round.objects.get_or_create(
                event=event, group=group, number=number
            )
            HeatLane.objects.get_or_create(
                event=event,
                round=round,
                heat=heat,
                lane=lane,
                racer=racer,
                finish_time=finish_time,
                finish_place=finish_place,
                completed=completed,
            )
            RosterEntry.objects.get_or_create(event=event, round=round, racer=racer)
    except ImportFailure as failure:
        return failure
    except IntegrityError, DatabaseError, groups.GroupFailure:
        return ImportFailure("unable", "Unable to create entry in RaceChart table.")
    return None
