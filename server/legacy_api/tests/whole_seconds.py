"""Near misses for a rule PHP counts in whole seconds (#356).

PHP stores a moment as `time()` and compares whole seconds, so each such
rule's answer turns at the first moment of a second, whatever fraction past
its own second the stored moment came. `near_misses` reads a rule either
side of that edge, for a moment at a whole second and at fractions past it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

SECOND = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
"""A whole second: each stored moment is at it or a fraction past it."""
MICROSECOND = timedelta(microseconds=1)
FRACTIONS = {
    "0": timedelta(0),
    "0.6": timedelta(microseconds=600_000),
    "0.999999": timedelta(microseconds=999_999),
}
BEFORE = {"less-0.5s": -timedelta(milliseconds=500), "less-1us": -MICROSECOND}
FROM = {
    "at": timedelta(0),
    "plus-1us": MICROSECOND,
    "plus-0.5s": timedelta(milliseconds=500),
}


def near_misses(edge: int, before, after) -> list:
    """`(stored, read, expected)` params: a moment stored at SECOND plus each
    of FRACTIONS, read `edge` whole seconds past SECOND, shifted by each
    offset. Short of the edge the rule answers `before`, and from the edge
    on `after`."""
    at = SECOND + timedelta(seconds=edge)
    return [
        pytest.param(SECOND + fraction, at + offset, expected, id=f"{f}-{edge}-{o}")
        for f, fraction in FRACTIONS.items()
        for offsets, expected in ((BEFORE, before), (FROM, after))
        for o, offset in offsets.items()
    ]


class Clock:
    """`timezone.now()` for the server, which the test sets before each
    request."""

    def __init__(self, monkeypatch):
        self.now = SECOND
        monkeypatch.setattr(timezone, "now", lambda: self.now)
