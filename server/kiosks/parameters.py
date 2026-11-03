"""Kiosk page parameters (#46): the text after `#` that a kiosk page reads,
from the Configure dialogs of `website/inc/kiosk-parameters.inc` and
`website/js/kiosk-parameters.js`.

Five pages take parameters, each its own JSON object, written as
`JSON.stringify()` writes it, keys in the same order:

- please-check-in: `{"classids":[...]}`
- slideshow: `{"title":...,"subdir":...,"classids":[...]}`
- standings: `{"key":...}`, a key of the standings catalog
- award-presentations: `{"confetti":true|false}`
- qrcode: `{"title":...,"content":...}`

`encode()` checks each value against the event, so a forged class id, slide
folder or standings key is refused, and `stored()` reads what is stored,
which a legacy client may have written loosely (`{confetti:false}` in
`testing/test-scenes.sh`).
"""

import json
import re
from dataclasses import dataclass, field

from roster.models import Group

PLEASE_CHECK_IN = "kiosks/please-check-in.kiosk"
SLIDESHOW = "kiosks/slideshow.kiosk"
STANDINGS = "kiosks/standings.kiosk"
AWARDS = "kiosks/award-presentations.kiosk"
QRCODE = "kiosks/qrcode.kiosk"
CONFIGURABLE = (PLEASE_CHECK_IN, SLIDESHOW, STANDINGS, AWARDS, QRCODE)
TAKES_CLASSES = (PLEASE_CHECK_IN, SLIDESHOW)
TEXT_LIMIT = 500

# A key with no quotes, as a JavaScript object literal writes it.
_BARE_KEY = re.compile(r"([{,]\s*)([A-Za-z_][\w-]*)\s*:")


class ParameterRefused(Exception):
    def __init__(self, description: str):
        super().__init__(description)
        self.description = description


@dataclass(frozen=True)
class Posted:
    """A Configure form's fields as posted, parsed by nothing yet."""

    title: str = ""
    subdir: str = ""
    classids: tuple[str, ...] = ()
    key: str = ""
    confetti: bool = True
    content: str = ""


@dataclass(frozen=True)
class Choices:
    """What a page's Configure form offers: the event's classes, its slide
    folders, and its standings, `(key, name)` in catalog order. Only the
    page's own lists are read."""

    groups: tuple[tuple[int, str], ...] = ()
    subdirs: tuple[str, ...] = ()
    standings: tuple[tuple[str, str], ...] = field(default=())


def stored(text: str) -> dict:
    """The parameters stored as `text`: a JSON object, or a JavaScript object
    literal with bare keys. Anything else is none."""
    for candidate in (text, _BARE_KEY.sub(r'\1"\2":', text or "")):
        try:
            value = json.loads(candidate)
        except TypeError, ValueError:
            continue
        return value if isinstance(value, dict) else {}
    return {}


def choices(event, page: str) -> Choices:
    groups, subdirs, standings = (), (), ()
    if page in TAKES_CLASSES:
        groups = tuple(
            Group.objects.filter(event=event)
            .order_by("sort_order", "name", "pk")
            .values_list("pk", "name")
        )
    if page == SLIDESHOW:
        from media.slides import slide_subdirs

        subdirs = tuple(slide_subdirs(event))
    if page == STANDINGS:
        from racing.services.standings import standings_snapshot

        standings = tuple(
            (entry.key, entry.name)
            for entry in standings_snapshot(event).standings.catalog()
        )
    return Choices(groups=groups, subdirs=subdirs, standings=standings)


def _text(value: str, what: str) -> str:
    if len(value) > TEXT_LIMIT:
        raise ParameterRefused(f"The {what} is too long.")
    return value


def _classids(posted: Posted, offered: Choices) -> list[int]:
    known = {pk for pk, _ in offered.groups}
    picked = set()
    for raw in posted.classids:
        try:
            picked.add(int(raw))
        except ValueError:
            raise ParameterRefused("Pick classes from the list.") from None
    if not picked <= known:
        raise ParameterRefused("A class picked is gone. Reload the page.")
    return [pk for pk, _ in offered.groups if pk in picked]


def encode(event, page: str, posted: Posted) -> str:
    """The parameters text for `page` from its Configure form. Raises
    `ParameterRefused` for a page with no parameters or a value the event
    does not offer."""
    if page not in CONFIGURABLE:
        raise ParameterRefused("This page has nothing to configure.")
    offered = choices(event, page)
    if page == PLEASE_CHECK_IN:
        value = {"classids": _classids(posted, offered)}
    elif page == SLIDESHOW:
        if posted.subdir and posted.subdir not in offered.subdirs:
            raise ParameterRefused("That slide folder is gone. Reload the page.")
        value = {
            "title": _text(posted.title, "title"),
            "subdir": posted.subdir,
            "classids": _classids(posted, offered),
        }
    elif page == STANDINGS:
        if posted.key not in {key for key, _ in offered.standings}:
            raise ParameterRefused("Pick standings from the list.")
        value = {"key": posted.key}
    elif page == AWARDS:
        value = {"confetti": posted.confetti}
    else:
        value = {
            "title": _text(posted.title, "title"),
            "content": _text(posted.content, "QR code text"),
        }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
