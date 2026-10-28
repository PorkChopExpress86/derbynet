"""The `PWD` barcode grammar of `website/inc/barcode.inc` (#29).

DerbyNet no longer prints barcodes (D13), but the Raspberry Pi photo stand
still scans them, so `racer.pass`, `racer.list` and `photo.upload` (#35)
accept the `barcode` parameter. `parse()` reads the text only; `resolve()`
looks the racer up and creates a walk-in racer for an unknown `PWDwi` car.

    PWDid<racerid>    the racer with that id
    PWDwi<carno>      the racer with that car number, made if there is none
    PWDuploadtest     the photo stand's upload test, no racer
    PWDuploadonly     a photo with no check-in, no racer
    PWD<carno>        the one racer with that car number
"""

import re
from dataclasses import dataclass

from racing.engine.phpcompat import php_intval
from roster.models import Racer


@dataclass(frozen=True)
class RacerId:
    racer_id: int


@dataclass(frozen=True)
class WalkIn:
    car_number: int


@dataclass(frozen=True)
class CarNumber:
    """The text after `PWD`, as posted. Only an integer names a car."""

    text: str


@dataclass(frozen=True)
class UploadTest:
    pass


@dataclass(frozen=True)
class UploadOnly:
    pass


@dataclass(frozen=True)
class NotABarcode:
    pass


Barcode = RacerId | WalkIn | CarNumber | UploadTest | UploadOnly | NotABarcode

# SQLite compares an INTEGER column with a text parameter by converting the
# text when it is an integer literal, so `PWD0101` finds car 101.
_INTEGER = re.compile(r"[+-]?[0-9]+")


def parse(barcode: str) -> Barcode:
    """The barcode's meaning, after trimming it as PHP's `trim()` does."""
    barcode = barcode.strip(" \t\n\r\0\x0b")
    if barcode.startswith("PWDid"):
        return RacerId(php_intval(barcode.removeprefix("PWDid")))
    if barcode.startswith("PWDwi"):
        return WalkIn(php_intval(barcode.removeprefix("PWDwi")))
    if barcode == "PWDuploadtest":
        return UploadTest()
    if barcode == "PWDuploadonly":
        return UploadOnly()
    if barcode.startswith("PWD"):
        return CarNumber(barcode.removeprefix("PWD"))
    return NotABarcode()


@dataclass(frozen=True)
class Resolved:
    """`barcode_to_racerid()`: the racer, or None when the barcode names no
    racer uniquely, and whether a walk-in racer was made for it."""

    racer: Racer | None
    created: bool = False


def resolve(event, barcode: str) -> Resolved:
    """Find the event's racer a barcode names. An unknown `PWDwi` car number
    becomes a walk-in racer (`racers.find_or_create_walk_in`)."""
    from roster.services import racers

    racers_of_event = Racer.objects.filter(event=event)
    match parse(barcode):
        case RacerId(racer_id):
            return Resolved(racers_of_event.filter(pk=racer_id).first())
        case WalkIn(car_number):
            racer, created = racers.find_or_create_walk_in(event, car_number)
            return Resolved(racer, created)
        case CarNumber(text) if _INTEGER.fullmatch(text):
            found = list(racers_of_event.filter(car_number=int(text))[:2])
            return Resolved(found[0] if len(found) == 1 else None)
    return Resolved(None)
