"""What the awards presentation console shows (#45): the award list with
its filter choices (`awards.presentation`), the award being presented as
`query=award.current` gives it, and whether any kiosk shows award
presentations. Reads only.

The 1 s poll reads `RaceState` and the award's recipient, and answers 204
while the key, the reveal flag and the recipient hold: a judged award is
one more indexed read, a speed award the standings. Only a change renders
the award.
"""

import time
from dataclasses import dataclass

from django.urls import get_script_prefix

from awards import listing
from core.models import RaceState
from ui.responses import car

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
    recipient = listing.current_recipient(event, key) if key else None
    return key, reveal, f"{reveal:d}-{recipient or 0}-{key}"


def current(event) -> Current:
    key, reveal, sig = signature(event)
    try:
        shown = listing.current(event, time.monotonic()) if key else None
    except listing.UnrecognizedKey:
        shown = None
    return Current(key, reveal, sig, shown)
