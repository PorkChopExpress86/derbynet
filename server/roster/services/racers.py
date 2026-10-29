"""Racers: registration, edits, check-in, and the racer lists (#29).

Ported from `website/inc/newracer.inc`, `checkin-table.inc` and the
`action.racer.*` and `query.racer.list` endpoints. Every write runs in one
transaction that first takes the `RaceState` lock, because the racing engine
rechecks the roster and the inspection status under it. The caller resolves
model instances inside the active event and checks the permission. A domain
failure raises `RacerFailure` with PHP's code.

PHP keeps `checkin_time` as epoch milliseconds; Django stores
`checked_in_at` and reports the same milliseconds as `seq`.
"""

import math
import random
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import quote_plus

from django.db import transaction
from django.utils import timezone

from awards.models import Award
from awards.services import reconcile_partition_lists
from core.models import ActionLog, RaceSettings, RaceState
from core.services import lock_race_state
from racing.engine.phpcompat import php_intval, php_numeric, php_truthy
from racing.models import HAS_RESULT, HeatLane, RosterEntry, Round
from roster.fake_names import FAKE_NAMES
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import car_numbers, groups

FAKE_IMAGES: Final = Path(__file__).resolve().parent.parent / "fake_racer_images"
WALK_IN_FIRST_NAME: Final = "Walk-In"
# Car numbers are stored in an IntegerField, which Postgres bounds.
CAR_NUMBER_RANGE: Final = range(-(2**31), 2**31)


class Failure(StrEnum):
    NO_RACER = "noracer"
    NO_PARTITION = "nopartition"
    NO_CLASS = "noclass"
    SCHEDULED = "scheduled"
    ROSTERS = "rosters"
    NOT_UNIQUE = "notunique"
    ALREADY_SCHEDULED = "alreadyscheduled"
    NOT_AUTHORIZED = "notauthorized"
    WHAT = "what"
    WHO = "who"
    MISSING_REQUIRED = "missingrequired"
    # Django only: PHP stores any text in the car number column.
    INVALID_CAR_NUMBER = "invalid-carnumber"
    # Django only: PHP stores a null car number when a partition is full.
    NO_CAR_NUMBER = "no-carnumber"
    # Django only: every car number range of the partition is taken (D48).
    RANGE_FULL = "range-full"


class RacerFailure(Exception):
    def __init__(self, code: Failure, description: str, partition_id=None):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description
        self.partition_id = partition_id


def _record(event_id, request: dict) -> None:
    """A synthetic history entry, as PHP's `record_action()` writes one."""
    ActionLog.objects.create(event_id=event_id, request=request)


def _epoch_ms(moment) -> int:
    return math.floor(moment.timestamp() * 1000) if moment else 0


def _car_number(text: str) -> int:
    """A posted car number, which SQLite stores as an integer when it reads
    as one. Django refuses anything else."""
    number = php_numeric(text)
    if number is None or number != int(number) or int(number) not in CAR_NUMBER_RANGE:
        raise RacerFailure(
            Failure.INVALID_CAR_NUMBER, f"Car number {text} is not a whole number."
        )
    return int(number)


# ----- Registration ----------------------------------------------------------


@dataclass
class NewRacer:
    racer: Racer
    warnings: list[str] = field(default_factory=list)


def insert_new_racer(
    event,
    *,
    first_name: str,
    last_name: str,
    car_number: str | int | None = None,
    car_name: str = "",
    note: str = "",
    partition: Partition | None = None,
    partition_name: str = "",
    exclude: bool = False,
) -> NewRacer:
    """`insert_new_racer()`: a racer in `partition`, else in the partition
    named `partition_name` (D34: a name that differs only in case joins the
    existing partition), else in `Default`. A blank car number takes the
    partition's next one. A car number another racer has is allowed, with a
    warning. The racer is entered in round 1 of its group, and the history
    gets `racer.*create`."""
    with transaction.atomic():
        lock_race_state(event.id)
        if partition is None:
            partition = groups.find_or_create_partition(
                event.id, partition_name or groups.DEFAULT_PARTITION_NAME
            )
        if car_number is None or (isinstance(car_number, str) and car_number == ""):
            car_number = car_numbers.next_car_number_for_partition(partition)
            if car_number is None and car_numbers.has_ranges(partition):
                raise RacerFailure(
                    Failure.RANGE_FULL,
                    f"Every car number range of {partition.name} is taken.",
                    partition_id=partition.pk,
                )
            if car_number is None:
                # PHP stores a null car number; the column here has none.
                raise RacerFailure(
                    Failure.NO_CAR_NUMBER,
                    f"Every car number of {partition.name} is taken.",
                )
        warnings = []
        if isinstance(car_number, str):
            number = _car_number(car_number.strip())
        else:
            number = car_number
        if Racer.objects.filter(event=event, car_number=number).exists():
            warnings.append(f"Duplicate car number {car_number}")
        if outside := car_numbers.outside_warning(partition, number):
            warnings.append(outside)
        subgroup = partition.subgroup
        racer = Racer.objects.create(
            event=event,
            car_number=number,
            car_name=car_name.strip(),
            note=note.strip(),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            partition=partition,
            subgroup=subgroup,
            group_id=subgroup.group_id,
            exclude=exclude,
        )
        groups.fill_in_missing_roster_entries(event.id)
        _record(
            event.id,
            {
                "action": "racer.*create",
                "racerid": racer.pk,
                "firstname": racer.first_name,
                "lastname": racer.last_name,
                "carnumber": str(car_number).strip(),
                "carname": racer.car_name,
                "partitionid": partition.pk,
                "rankid": subgroup.pk,
                "classid": subgroup.group_id,
            },
        )
        return NewRacer(racer, warnings)


def split_first_last(whole: str) -> tuple[str, str]:
    """`racer.import`'s `first-last`: "Last, First" at the first comma, else
    "First Last" at the last space, else a last name only. The caller trims
    each part."""
    whole = whole.strip(" \t\n\r\0\x0b")
    if "," in whole:
        last, first = whole.split(",", 1)
        return first, last
    if " " in whole:
        first, last = whole.rsplit(" ", 1)
        return first, last
    return "", whole


def as_boolean(value: str) -> bool:
    """`as_boolean()` in `action.racer.import.inc`: 1 and 0, blank as false,
    and otherwise anything that does not start with n."""
    value = value.strip(" \t\n\r\0\x0b")
    if value == "1":
        return True
    if value in ("0", ""):
        return False
    return value.lower()[0] != "n"


def find_or_create_walk_in(event, car_number: int) -> tuple[Racer | None, bool]:
    """The racer a `PWDwi<carno>` barcode names, made if no racer has that
    car number. Returns the racer and whether it was made.

    The walk-in racer is `Walk-In <carno>`. With a car-numbering multiplier
    the partition is the one whose range holds the number, or the last one
    before it; with multiplier 0 it is the partition with the highest id,
    as PHP's `COALESCE(MAX(partitionid), 1)` means (PHP assigns the wrong
    variable and leaves it undefined, C14). With no
    such partition it is `Default`. The check and the insert share one
    locked transaction, so two scanners posting the same barcode make one
    racer.
    """
    if car_number not in CAR_NUMBER_RANGE:
        return None, False
    with transaction.atomic():
        lock_race_state(event.id)
        existing = (
            Racer.objects.filter(event=event, car_number=car_number)
            .order_by("pk")
            .first()
        )
        if existing is not None:
            return existing, False
        partitions = Partition.objects.filter(event=event)
        rule = car_numbers.numbering(event.id)
        if rule.multiplier > 0:
            # intdiv() cuts toward zero.
            century = int((car_number - rule.smallest) / rule.multiplier) + 1
            partitions = partitions.filter(sort_order__lte=century)
        if rule.multiplier > 0:
            partition = partitions.order_by("-sort_order", "-id").first()
        else:
            partition = partitions.order_by("-id").first()
        new = insert_new_racer(
            event,
            first_name=WALK_IN_FIRST_NAME,
            last_name=str(car_number),
            car_number=car_number,
            partition=partition,
        )
        return new.racer, True


# ----- Edits ------------------------------------------------------------------


@dataclass
class EditResult:
    # `partition-sortorder`: absent unless a partition was posted, and False
    # for an unknown one, as PHP's read_single_value() gives.
    partition_sort_order: int | bool | None = None
    warning: list[str] | None = None


@dataclass(frozen=True)
class Edit:
    """The fields `racer.edit` posts. None leaves a field unchanged. A
    partition or subgroup that the post named but that does not exist is
    `MISSING`."""

    partition: Partition | object | None = None
    subgroup: Subgroup | object | None = None
    posted_partition: str = ""
    posted_subgroup: str = ""
    first_name: str | None = None
    last_name: str | None = None
    car_number: str | None = None
    car_name: str | None = None
    note: str | None = None
    exclude: bool | None = None


MISSING: Final = object()


def edit_racer(racer: Racer, edit: Edit) -> EditResult:
    """`racer.edit`. A new partition moves the racer to its subgroup and
    group; a new subgroup moves the racer to it and its group, leaving the
    partition. When the group changes, the racer's round 1 entry moves to the
    new group's round 1, and the award lists are reconciled (D38)."""
    result = EditResult()
    with transaction.atomic():
        lock_race_state(racer.event_id)
        racer = Racer.objects.select_for_update().get(pk=racer.pk)
        old_place = (racer.group_id, racer.subgroup_id, racer.partition_id)
        if edit.partition is not None:
            if edit.partition is MISSING:
                result.partition_sort_order = False
                raise EditFailure(
                    result,
                    Failure.NO_PARTITION,
                    f"Invalid segment number {edit.posted_partition}",
                )
            result.partition_sort_order = edit.partition.sort_order
            racer.partition = edit.partition
            racer.subgroup = edit.partition.subgroup
            racer.group_id = edit.partition.subgroup.group_id
        if edit.subgroup is not None:
            if edit.subgroup is MISSING:
                raise EditFailure(
                    result,
                    Failure.NO_CLASS,
                    f"No class for rankid {edit.posted_subgroup}",
                )
            racer.subgroup = edit.subgroup
            racer.group_id = edit.subgroup.group_id
        for name in ("first_name", "last_name", "car_name", "note"):
            value = getattr(edit, name)
            if value is not None:
                setattr(racer, name, value.strip())
        if edit.car_number is not None:
            racer.car_number = _car_number(edit.car_number.strip())
            if (
                Racer.objects.filter(
                    event_id=racer.event_id, car_number=racer.car_number
                )
                .exclude(pk=racer.pk)
                .exists()
            ):
                # PHP interpolates an unset key, so the number is missing.
                result.warning = ["Duplicate car number "]
            if racer.partition_id and (
                outside := car_numbers.outside_warning(
                    racer.partition, racer.car_number
                )
            ):
                result.warning = (result.warning or []) + [outside]
        if edit.exclude is not None:
            racer.exclude = edit.exclude
        racer.save()
        if racer.group_id != old_place[0]:
            _move_round_1_entry(racer, old_place[0])
        if (racer.group_id, racer.subgroup_id, racer.partition_id) != old_place:
            reconcile_partition_lists(racer.event_id)
    return result


class EditFailure(RacerFailure):
    """A refused edit, with what PHP wrote before it refused."""

    def __init__(self, result: EditResult, code: Failure, description: str):
        super().__init__(code, description)
        self.result = result


def _move_round_1_entry(racer: Racer, old_group_id: int) -> None:
    """PHP moves the racer's entry in the old group's round 1 to the new
    group's round 1. With no round 1 in the new group PHP leaves the entry
    pointing at no round; here it goes."""
    old = RosterEntry.objects.filter(
        racer=racer, round__group_id=old_group_id, round__number=1
    )
    new_round = Round.objects.filter(group_id=racer.group_id, number=1).first()
    if (
        new_round is None
        or RosterEntry.objects.filter(racer=racer, round=new_round).exists()
    ):
        old.delete()
    else:
        old.update(round=new_round)


def delete_racer(event, racer: Racer | None, posted_id) -> None:
    """`racer.delete`: refused for a racer with heats (`scheduled`) or in
    more than one round (`rosters`). The history gets `racer.*delete` with
    the posted id, as PHP writes it whether or not the delete happened."""
    try:
        with transaction.atomic():
            lock_race_state(event.id)
            if racer is not None:
                if HeatLane.objects.filter(racer=racer).exists():
                    raise RacerFailure(
                        Failure.SCHEDULED, "Racer already appears in a race schedule."
                    )
                if RosterEntry.objects.filter(racer=racer).count() > 1:
                    raise RacerFailure(
                        Failure.ROSTERS,
                        "Racer already appears in more than one racing round.",
                    )
                RosterEntry.objects.filter(racer=racer).delete()
                racer.delete()
                reconcile_partition_lists(event.id)
    finally:
        # After the transaction, so a refusal's rollback keeps the entry.
        _record(event.id, {"action": "racer.*delete", "racerid": posted_id})


# ----- Check-in ---------------------------------------------------------------


def _results_in_racers_rounds(racer: Racer) -> bool:
    rounds = HeatLane.objects.filter(racer=racer).values("round_id")
    return HeatLane.objects.filter(round_id__in=rounds).filter(HAS_RESULT).exists()


@dataclass(frozen=True)
class PassDetails:
    group_name: str
    first_name: str
    last_name: str


def pass_racer(racer: Racer | None, passed: bool) -> PassDetails | None:
    """`racer.pass`: set the racer's inspection status and check-in time.

    Refused with `notunique` when the barcode or id named no racer, and with
    `alreadyscheduled` when the racer has a result. Returns the details the
    photo stand announces, for a pass.
    """
    if racer is None:
        raise RacerFailure(Failure.NOT_UNIQUE, "Need to identify racer uniquely.")
    with transaction.atomic():
        lock_race_state(racer.event_id)
        if HeatLane.objects.filter(racer=racer).filter(HAS_RESULT).exists():
            raise RacerFailure(
                Failure.ALREADY_SCHEDULED,
                "Schedule data already exists for this racer",
            )
        Racer.objects.filter(pk=racer.pk).update(
            passed_inspection=passed, checked_in_at=timezone.now()
        )
    if not passed:
        return None
    racer = Racer.objects.select_related("group").get(pk=racer.pk)
    return PassDetails(racer.group.name, racer.first_name, racer.last_name)


# ----- Bulk changes -------------------------------------------------------------


class What(StrEnum):
    CHECKIN = "checkin"
    NUMBER = "number"
    ELIGIBILITY = "eligibility"


@dataclass(frozen=True)
class Bulk:
    who: str
    what: str
    value: str = "1"
    start: str = ""
    auto: bool = False
    may_revert: bool = False


def _bulk_racers(event, who: str):
    racers = Racer.objects.filter(event=event)
    if who == "all":
        return racers.order_by("pk")
    field_name = {"d": "partition_id", "c": "group_id", "r": "subgroup_id"}.get(who[:1])
    if field_name is None:
        raise RacerFailure(Failure.WHO, f"Unrecognized 'who' parameter: {who}")
    key = who[1:]
    if not re.fullmatch(r"[0-9]+", key):
        return racers.none()
    return racers.filter(**{field_name: int(key)}).order_by("pk")


def apply_bulk(event, bulk: Bulk) -> None:
    """`racer.bulk`: check in, renumber, or change the award eligibility of
    every racer, or those of one partition (`d<id>`), group (`c<id>`) or
    subgroup (`r<id>`), in id order.

    A check-in stops at the first racer whose rounds have a result
    (`alreadyscheduled`), and an un-check-in without `REVERT_CHECK_IN` stops
    with `notauthorized` (PHP writes XML into the JSON there, C21); the
    racers before it keep the change, as in PHP. Renumbering by `start`
    counts up from it; by `auto` each partition counts up from its lower
    bound. The `renumber` flag is ignored, as in PHP.
    """
    if bulk.what not in set(What):
        raise RacerFailure(Failure.WHAT, f"Unrecognized 'what' parameter: {bulk.what}")
    stopped = None
    with transaction.atomic():
        lock_race_state(event.id)
        racers = list(_bulk_racers(event, bulk.who))
        if bulk.what == What.NUMBER:
            _renumber(event, racers, bulk)
        elif bulk.what == What.CHECKIN:
            stopped = _check_in_all(racers, bulk)
        else:
            exclude = 1 - php_intval(bulk.value) != 0
            Racer.objects.filter(pk__in=[r.pk for r in racers]).update(exclude=exclude)
    if stopped is not None:
        raise stopped


def _check_in_all(racers: Iterable[Racer], bulk: Bulk) -> RacerFailure | None:
    """Pass or un-pass each racer until one is refused. Returns the refusal,
    so the racers before it keep their change."""
    passed = php_truthy(bulk.value)
    for racer in racers:
        if _results_in_racers_rounds(racer):
            return RacerFailure(
                Failure.ALREADY_SCHEDULED, "Schedule data already exists for this racer"
            )
        if not passed and not bulk.may_revert:
            return RacerFailure(
                Failure.NOT_AUTHORIZED, "Not authorized -- please see race coordinator."
            )
        Racer.objects.filter(pk=racer.pk).update(passed_inspection=passed)
    return None


def _renumber(event, racers: Iterable[Racer], bulk: Bulk) -> None:
    if bulk.auto:
        numbers = car_numbers.base_car_number_by_partition(event.id)
    else:
        numbers = php_intval(bulk.start)
    for racer in racers:
        if isinstance(numbers, int):
            number, numbers = numbers, numbers + 1
        else:
            number = numbers.get(racer.partition_id, 0)
            numbers[racer.partition_id] = number + 1
        Racer.objects.filter(pk=racer.pk).update(car_number=number)


# ----- Fake racers --------------------------------------------------------------


def fake_partition_name(n: int) -> str:
    return {
        1: "Fake Lions",
        2: "Fake Tigers",
        3: "Fake Wolves",
        4: "Fake Bears",
        5: "Fake Webelos",
    }.get(n, f"Fake Group {n}")


def make_fake_racers(
    event, n_groups: int, per_group: int, check_in: bool, rng=random
) -> list[Racer]:
    """`racer.fake`: `n_groups` partitions of about `per_group` racers each
    (two either way), with names from a list and a random racer and car
    picture, copied into the photo folders as `fake_<racerid>.svg`. With
    `check_in`, every racer of the event passes. Sets `fake-racers`."""
    folders = RaceSettings.objects.get(event=event)
    racer_pictures = sorted((FAKE_IMAGES / "racers").iterdir())
    car_pictures = sorted((FAKE_IMAGES / "cars").iterdir())
    made = []
    with transaction.atomic():
        lock_race_state(event.id)
        names = list(FAKE_NAMES)
        for g in range(1, n_groups + 1):
            size = per_group + rng.randint(-2, 2)
            for r in range(size):
                first, last = names.pop(rng.randrange(len(names)))
                if not names:
                    names = list(FAKE_NAMES)
                racer = insert_new_racer(
                    event,
                    first_name=first,
                    last_name=last,
                    car_number=(g - 1) * 100 + r + 1,
                    partition_name=fake_partition_name(g),
                ).racer
                name = f"fake_{racer.pk}.svg"
                for folder, pictures in (
                    (folders.photo_directory, racer_pictures),
                    (folders.car_photo_directory, car_pictures),
                ):
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(rng.choice(pictures), Path(folder) / name)
                Racer.objects.filter(pk=racer.pk).update(headshot=name, car_photo=name)
                made.append(racer)
        if check_in:
            Racer.objects.filter(event=event).update(passed_inspection=True)
        RaceState.objects.filter(event=event).update(fake_racers=True)
    return made


# ----- Reading ------------------------------------------------------------------

HEADSHOTS = "head"
CAR_PHOTOS = "car"
RENDER_LISTVIEW: Final = "200x200"
RENDER_CHECKIN: Final = "200x200"
NAMED_RENDERS = frozenset({"original", "cropped", "work", "200x200", "400x400"})
_SIZED_RENDER = re.compile(r"[0-9]+(\.[0-9]+)?x[0-9]+(\.[0-9]+)?|[0-9]+(\.[0-9]+)?h")


def render_label(posted: str | None) -> str:
    """A `render` parameter PHP's `lookup()` accepts, else the list view's.
    PHP fails with a fatal error on any other label."""
    if isinstance(posted, str) and (
        posted in NAMED_RENDERS or _SIZED_RENDER.fullmatch(posted)
    ):
        return posted
    return RENDER_LISTVIEW


def _urlencode(text: str) -> str:
    """PHP's `urlencode()`, which also escapes `~`."""
    return quote_plus(text, safe="").replace("~", "%7E")


def photo_url(repository: str, folder: str, path: str, render: str) -> str:
    """`PhotoRender::render_url()`: the `photo.php` URL of a stored photo, or
    "" for none. The last part, a cache breaker, is the file's modification
    time, or 0 when the file is missing."""
    if not path:
        return ""
    basename = path.rsplit("/", 1)[-1]
    try:
        breaker = int((Path(folder) / path).stat().st_mtime)
    except OSError:
        breaker = 0
    return f"photo.php/{repository}/file/{render}/{_urlencode(basename)}/{breaker}"


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _nocase(text: str) -> str:
    """SQLite's `COLLATE NOCASE`, which folds ASCII letters only."""
    return text.translate(_ASCII_LOWER)


class Order(StrEnum):
    NAME = "name"
    CLASS = "class"
    RANK = "rank"
    CAR = "car"
    CHECKIN = "checkin"


def _sort_key(order: str):
    def name(r):
        return (_nocase(r.last_name), _nocase(r.first_name))

    match order:
        case Order.CAR:
            return lambda r: (r.car_number, *name(r), r.pk)
        case Order.CLASS:
            return lambda r: (r.group.sort_order, *name(r), r.pk)
        case Order.RANK:
            return lambda r: (r.group.sort_order, r.subgroup.sort_order, *name(r), r.pk)
        case Order.CHECKIN:
            return lambda r: (-_epoch_ms(r.checked_in_at), r.pk)
    return lambda r: (*name(r), r.pk)


def list_racers(event, order: str = "", racer_id=None, render: str | None = None):
    """`query=racer.list`: every racer of the event, or one, as rows.

    Sorted as PHP's SQLite sorts names, `COLLATE NOCASE`: ASCII letters
    ignore case, everything else by code point, so the same order comes back
    on Postgres. Rows that tie keep id order.
    """
    settings = RaceSettings.objects.get(event=event)
    label = render_label(render)
    racers = Racer.objects.filter(event=event).select_related(
        "group", "subgroup", "partition"
    )
    if racer_id is not None:
        racers = racers.filter(pk=racer_id)
    rows = sorted(racers, key=_sort_key(order or Order.NAME))
    return [
        {
            "racerid": r.pk,
            "firstname": r.first_name,
            "lastname": r.last_name,
            "carnumber": r.car_number,
            "carname": r.car_name,
            "partitionid": r.partition_id,
            "partition": r.partition.name,
            "note_from": r.note,
            "headshot": photo_url(
                HEADSHOTS, settings.photo_directory, r.headshot, label
            ),
            "car_photo": photo_url(
                CAR_PHOTOS, settings.car_photo_directory, r.car_photo, label
            ),
            "classid": r.group_id,
            "class": r.group.name,
            "rankid": r.subgroup_id,
            "rank": r.subgroup.name,
            "passed": r.passed_inspection,
            "seq": _epoch_ms(r.checked_in_at),
        }
        for r in rows
    ]


def _rank_seq(event, subgroup_id) -> int | None:
    """The subgroup's place, from 1, among every subgroup by group and
    subgroup order, as `classes_and_ranks()` numbers them."""
    subgroups = sorted(
        Subgroup.objects.filter(event=event).select_related("group"),
        key=lambda s: (
            s.group.sort_order,
            s.sort_order,
            _nocase(s.group.name),
            _nocase(s.name),
        ),
    )
    return next((n for n, s in enumerate(subgroups, 1) if s.pk == subgroup_id), None)


def checkin_row(racer: Racer) -> dict:
    """`json_table_row_for_id()`: the check-in table's row for one racer.

    PHP passes `use_groups()` where the row number goes, so `rowno` is
    whether the event has more than one group."""
    racer = Racer.objects.select_related("group", "subgroup", "partition").get(
        pk=racer.pk
    )
    event = racer.event
    settings = RaceSettings.objects.get(event=event)
    xbs = Award.objects.filter(
        event=event, name=settings.xbs_award, racer=racer
    ).exists()
    return {
        "racerid": racer.pk,
        "rowno": Group.objects.filter(event=event).count() > 1,
        "denscheduled": HeatLane.objects.filter(
            event=event, round__group_id=racer.group_id
        ).exists(),
        "exclude": racer.exclude,
        "partitionid": racer.partition_id,
        "partition": racer.partition.name,
        "partition_sortorder": racer.partition.sort_order,
        "note": racer.note,
        "classid": racer.group_id,
        "class": racer.group.name,
        "rankid": racer.subgroup_id,
        "rank": racer.subgroup.name,
        "rankseq": _rank_seq(event, racer.subgroup_id),
        "carnumber": racer.car_number,
        "headshot": photo_url(
            HEADSHOTS, settings.photo_directory, racer.headshot, RENDER_CHECKIN
        )
        or "img/photo-headshot.png",
        "carphoto": photo_url(
            CAR_PHOTOS, settings.car_photo_directory, racer.car_photo, RENDER_CHECKIN
        )
        or "img/photo-car.png",
        "firstname": racer.first_name,
        "lastname": racer.last_name,
        "carname": racer.car_name,
        "scheduled": HeatLane.objects.filter(racer=racer).exists(),
        "passed": racer.passed_inspection,
        "xbs": int(xbs),
    }
