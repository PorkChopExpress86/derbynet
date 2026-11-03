"""What the awards presentation console shows (#45): the award list with
its filter choices (`awards.presentation`), the award being presented as
`query=award.current` gives it, and whether any kiosk shows award
presentations. Reads only.

The 1 s poll reads the award as `query=award.current` gives it and answers
204 while the key, the reveal flag and those fields hold, so a new winner, a
renamed racer or a new photo shows (#45 retro: a signature of the key, flag
and recipient missed the names).
"""

import hashlib
import json
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


def current(event) -> Current:
    key, reveal = RaceState.objects.filter(event=event).values_list(
        "current_award_key", "reveal_current_award"
    )[0]
    try:
        shown = listing.current(event, time.monotonic()) if key else None
    except listing.UnrecognizedKey:
        shown = None
    if shown:
        shown.pop("duration", None)
    material = json.dumps([key, reveal, shown], sort_keys=True, default=str)
    sig = hashlib.sha256(material.encode()).hexdigest()[:20]
    return Current(key, reveal, sig, shown)
