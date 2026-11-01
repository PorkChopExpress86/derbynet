"""Which legacy endpoints reply in XML (#17).

It imports nothing from Django, so tools that read transcripts outside the
server (`tools/transcript_diff.py`) use the same rule as the dispatcher.
"""

from enum import StrEnum


class Format(StrEnum):
    JSON = "json"
    XML = "xml"


def format_of(name: str) -> Format:
    """PHP's rule. `login` and `roles` are in XML for older derby-timer.jar."""
    if name in ("timer-message", "login", "roles") or "snapshot" in name:
        return Format.XML
    return Format.JSON
