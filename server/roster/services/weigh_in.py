"""Car weigh-in at check-in (#70, owner decisions D8 and D54).

PHP has no weigh-in: `RegistrationInfo` has no weight column
(`website/sql/sqlite/schema.inc:126-141`) and check-in sets only the pass
and its time (`website/ajax/action.racer.pass.inc:35-40`). No legacy
endpoint reads or writes these fields
(`ui/tests/test_checkin.py::test_no_legacy_endpoint_reads_or_writes_the_weigh_in`).

Each partition sets its weigh-in (D54, refining D8's "per class"):

- `none`: check-in shows no weigh-in;
- `pass_fail`: the volunteer taps Pass or Fail;
- `recorded`: the volunteer types the weight in the partition's unit.

These are D8's defaults, which the owner may correct: every weight is
stored in grams to two places, entered and shown in the partition's unit
(grams, ounces or pounds), and a partition may set a maximum. A recorded
weight over the maximum saves and comes back with a warning; it never
blocks check-in (D8). A recorded car passes its weigh-in when it is at or
under the maximum, or when there is none.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from core.services import lock_race_state
from roster.models import Partition, Racer

Mode = Partition.WeighInMode
Unit = Partition.WeightUnit

GRAMS_PER = {
    Unit.GRAMS: Decimal(1),
    Unit.OUNCES: Decimal("28.349523125"),
    Unit.POUNDS: Decimal("453.59237"),
}
CENT = Decimal("0.01")
# The places a weight shows in its unit: enough that a weight stored to
# 0.01 g reads back as it was typed, and never fewer than one.
SHOWN_PLACES = {Unit.GRAMS: 2, Unit.OUNCES: 2, Unit.POUNDS: 4}
# The largest weight the columns hold (`max_digits=7, decimal_places=2`).
MAX_GRAMS = Decimal("99999.99")


class Code(StrEnum):
    NO_WEIGH_IN = "no-weigh-in"
    WEIGHT_NOT_ASKED = "weight-not-asked"
    WEIGHT_NEEDED = "weight-needed"
    PASS_NOT_ASKED = "pass-not-asked"
    PASS_NEEDED = "pass-needed"
    BAD_WEIGHT = "bad-weight"
    BAD_UNIT = "bad-unit"
    BAD_MODE = "bad-mode"
    UNIT_NEEDED = "unit-needed"


class WeighInFailure(Exception):
    def __init__(self, code: Code, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


@dataclass(frozen=True)
class Entry:
    """What the volunteer entered: Pass or Fail, or a weight as typed in
    `unit` (the partition's unit when None)."""

    passed: bool | None = None
    weight: object = None
    unit: str | None = None


@dataclass(frozen=True)
class Recorded:
    racer_id: int
    passed: bool
    weight_grams: Decimal | None
    weighed_at: object
    # The weight in the partition's unit ("5.0 oz"), or "" for pass or fail.
    weight: str = ""
    # The over-the-maximum warning, or "".
    warning: str = ""


@dataclass(frozen=True)
class Setting:
    """A partition's weigh-in, as the add sheet asks for it."""

    mode: str
    unit: str
    # The maximum in the unit, with the unit, or "".
    maximum: str


def setting(partition: Partition | None) -> Setting | None:
    """The partition's weigh-in, or None when it has none."""
    if partition is None or partition.weigh_in_mode == Mode.NONE:
        return None
    return Setting(partition.weigh_in_mode, partition.weight_unit, _maximum(partition))


@dataclass(frozen=True)
class Status:
    """A racer's weigh-in as check-in shows it, read against the partition's
    setting now, so a changed unit or maximum shows at once."""

    mode: str
    unit: str
    weighed: bool
    passed: bool | None
    failed: bool
    over: bool
    # The weight in the unit, with the unit ("5.0 oz"), or "".
    weight: str
    # The weight in the unit as a bare number, for the input field.
    value: str
    # The maximum in the unit, with the unit, or "".
    maximum: str


def _unit(unit: str) -> Unit:
    try:
        return Unit(unit)
    except ValueError:
        raise WeighInFailure(Code.BAD_UNIT, "Pick grams, ounces or pounds.") from None


def to_grams(weight, unit: str) -> Decimal:
    """`weight` (a number, or text as typed) in `unit`, as grams to two
    places. Refuses anything but a positive weight the columns hold."""
    if isinstance(weight, float):
        weight = repr(weight)
    try:
        value = Decimal(str(weight).strip())
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite() or value <= 0:
        raise WeighInFailure(Code.BAD_WEIGHT, "Enter the weight as a number above 0.")
    grams = (value * GRAMS_PER[_unit(unit)]).quantize(CENT, rounding=ROUND_HALF_UP)
    if grams <= 0 or grams > MAX_GRAMS:
        raise WeighInFailure(Code.BAD_WEIGHT, "That weight is out of range.")
    return grams


def in_unit(grams: Decimal, unit: str) -> str:
    """Stored grams as a bare number in `unit`, at `SHOWN_PLACES` with
    trailing zeros dropped but one place kept: 141.75 g is "5.0" oz."""
    unit = _unit(unit)
    value = (Decimal(grams) / GRAMS_PER[unit]).quantize(
        Decimal(1).scaleb(-SHOWN_PLACES[unit]), rounding=ROUND_HALF_UP
    )
    text = f"{value:f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def shown(grams: Decimal, unit: str) -> str:
    return f"{in_unit(grams, unit)} {_unit(unit).value}"


def _maximum(partition: Partition) -> str:
    """The partition's maximum in its unit, with the unit, or "". A maximum
    always has a unit (`partition_weight_unit_when_needed`)."""
    maximum, unit = partition.max_weight_grams, partition.weight_unit
    return shown(maximum, unit) if maximum is not None and unit else ""


def status(racer: Racer) -> Status | None:
    """The racer's weigh-in under its partition's setting, or None when the
    partition has no weigh-in. Reads `racer.partition`. The stored pass is
    the one source: `set_partition_weigh_in` keeps it in step with the
    maximum and clears it on a new mode."""
    partition = racer.partition
    mode = partition.weigh_in_mode
    if mode == Mode.NONE:
        return None
    unit = partition.weight_unit
    grams = racer.weight_grams if mode == Mode.RECORDED else None
    if mode == Mode.RECORDED:
        weighed = grams is not None
    else:
        weighed = racer.weigh_in_passed is not None
    passed = racer.weigh_in_passed if weighed else None
    failed = passed is False
    return Status(
        mode=mode,
        unit=unit,
        weighed=weighed,
        passed=passed,
        failed=failed,
        over=failed and mode == Mode.RECORDED,
        weight=shown(grams, unit) if grams is not None else "",
        value=in_unit(grams, unit) if grams is not None else "",
        maximum=_maximum(partition),
    )


def record_weigh_in(
    racer: Racer, passed: bool | None = None, weight=None, unit: str | None = None
) -> Recorded:
    """Record the racer's weigh-in under its partition's setting: Pass or
    Fail in a `pass_fail` partition, a weight in a `recorded` one. A weight
    in `unit` (default the partition's) is stored in grams. A weight over
    the partition's maximum saves, with a warning (D8)."""
    with transaction.atomic():
        lock_race_state(racer.event_id)
        return record_locked(racer.pk, Entry(passed, weight, unit))


def record_locked(racer_id: int, entry: Entry) -> Recorded:
    """`record_weigh_in` inside a caller's transaction that holds the
    race-state lock, as `insert_new_racer` does for the add sheet."""
    racer = Racer.objects.select_related("partition").get(pk=racer_id)
    partition = racer.partition
    mode = partition.weigh_in_mode
    weight = None if entry.weight in (None, "") else entry.weight
    warning = ""
    if mode == Mode.NONE:
        raise WeighInFailure(
            Code.NO_WEIGH_IN, f"{partition.name} has no weigh-in at check-in."
        )
    if mode == Mode.PASS_FAIL:
        if weight is not None:
            raise WeighInFailure(
                Code.WEIGHT_NOT_ASKED,
                f"{partition.name} weighs cars pass or fail, with no weight.",
            )
        if entry.passed is None:
            raise WeighInFailure(Code.PASS_NEEDED, "Tap Pass or Fail.")
        passed, grams = bool(entry.passed), None
    else:
        if entry.passed is not None:
            raise WeighInFailure(
                Code.PASS_NOT_ASKED,
                f"{partition.name} records each car's weight. Enter the weight.",
            )
        if weight is None:
            raise WeighInFailure(Code.WEIGHT_NEEDED, "Enter the car's weight.")
        unit = partition.weight_unit if entry.unit is None else entry.unit
        grams = to_grams(weight, unit)
        maximum = partition.max_weight_grams
        passed = maximum is None or grams <= maximum
        if not passed:
            warning = (
                f"{shown(grams, partition.weight_unit)} is over the"
                f" {shown(maximum, partition.weight_unit)} maximum for"
                f" {partition.name}."
            )
    now = timezone.now()
    Racer.objects.filter(pk=racer.pk).update(
        weigh_in_passed=passed, weight_grams=grams, weighed_at=now
    )
    as_shown = shown(grams, partition.weight_unit) if grams is not None else ""
    return Recorded(racer.pk, passed, grams, now, as_shown, warning)


def set_partition_weigh_in(
    partition: Partition, mode: str, unit: str = "", max_weight=None
) -> Partition:
    """Set how check-in weighs the partition's cars, for the settings page
    (#48). `max_weight` is in `unit`; None or "" clears it. A recorded
    weight, or a pass-or-fail maximum, needs a unit. Stored weights stay in
    grams, so a new unit changes only how they show.

    In the same transaction, a new maximum passes or fails each recorded car
    again, and a new mode clears every weigh-in of the partition, since a
    pass or fail is no weight and a weight is no pass or fail. Clearing is a
    D8 default the owner may correct (review of #70)."""
    try:
        mode = Mode(mode)
    except ValueError:
        raise WeighInFailure(
            Code.BAD_MODE, "Pick no weigh-in, pass or fail, or recorded weight."
        ) from None
    unit = _unit(unit).value if unit else ""
    maximum = None
    if max_weight not in (None, ""):
        if not unit:
            raise WeighInFailure(Code.UNIT_NEEDED, "Pick the maximum's unit.")
        maximum = to_grams(max_weight, unit)
    if mode == Mode.RECORDED and not unit:
        raise WeighInFailure(Code.UNIT_NEEDED, "Pick the unit weights are entered in.")
    with transaction.atomic():
        lock_race_state(partition.event_id)
        old_mode = (
            Partition.objects.filter(pk=partition.pk)
            .values_list("weigh_in_mode", flat=True)
            .get()
        )
        Partition.objects.filter(pk=partition.pk).update(
            weigh_in_mode=mode, weight_unit=unit, max_weight_grams=maximum
        )
        racers = Racer.objects.filter(partition_id=partition.pk)
        if mode != old_mode:
            racers.update(weigh_in_passed=None, weight_grams=None, weighed_at=None)
        elif mode == Mode.RECORDED:
            weighed = racers.filter(weight_grams__isnull=False)
            if maximum is None:
                weighed.update(weigh_in_passed=True)
            else:
                weighed.filter(weight_grams__lte=maximum).update(weigh_in_passed=True)
                weighed.filter(weight_grams__gt=maximum).update(weigh_in_passed=False)
    partition.refresh_from_db()
    return partition
