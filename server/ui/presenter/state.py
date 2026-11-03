"""What the awards presentation console shows (#45): the award list with
its filter choices (`awards.presentation`), the award being presented as
`query=award.current` gives it, and whether any kiosk shows award
presentations. Reads only.

The 1 s poll reads `RaceState` alone, one indexed read, and answers 204
while the key and the reveal flag hold; only a change reads the award.
"""

import time
from dataclasses import dataclass

from django.urls import get_script_prefix

from awards import listing
from core.models import RaceState
from ui.checkin.state import car

POLL_SECONDS = 1


@dataclass(frozen=True)
class Current:
    key: str
    reveal: bool
    signature: str
    # `query=award.current`'s fields, or None when it answers no award.
    shown: dict | None

    @property
    def car(self) -> str:
        return car(self.shown["carnumber"]) if self.shown else ""

    @property
    def photo(self) -> str:
        if not self.shown:
            return ""
        found = self.shown.get("carphoto") or self.shown.get("headphoto") or ""
        return get_script_prefix() + found if found else ""


def signature(event) -> tuple[str, bool, str]:
    key, reveal = RaceState.objects.filter(event=event).values_list(
        "current_award_key", "reveal_current_award"
    )[0]
    return key, reveal, f"{reveal:d}{key}"


def current(event) -> Current:
    key, reveal, sig = signature(event)
    try:
        shown = listing.current(event, time.monotonic()) if key else None
    except listing.UnrecognizedKey:
        shown = None
    return Current(key, reveal, sig, shown)
