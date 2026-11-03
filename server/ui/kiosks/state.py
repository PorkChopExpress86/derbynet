"""What the kiosk dashboard and the scene editor show (#46). Reads only.

The board lists the kiosks heard from in the last 60 s, as
`poll.kiosk.all` does, without its drop of kiosks silent for an hour: a GET
here writes nothing. The active scene is `RaceState.current_scene`, which
`kiosks.services.apply_scene()` writes, so the switcher shows the scene
applied last (C19). Each kiosk's parameters are its own (C20).
"""

from dataclasses import dataclass

from core.models import RaceState
from kiosks import parameters
from kiosks import services as kiosks
from kiosks.madlib import madlib
from kiosks.models import Scene, SceneKiosk
from roster.models import Group

POLL_SECONDS = 2
# PHP shows "Last contact" once a kiosk has been silent this long.
QUIET_SECONDS = 5

# The page picker's names for the built-in pages (`kiosks.services`).
PAGE_LABELS = {
    "award-presentations": "Award presentations",
    "derbynet": "DerbyNet logo",
    "flag": "Flag",
    "identify": "Identify",
    "now-racing": "Now racing",
    "now-racing-columnar": "Now racing, in columns",
    "now-racing-columnar-reversed": "Now racing, in columns, reversed",
    "ondeck": "On deck",
    "please-check-in": "Please check in",
    "qrcode": "QR code",
    "results-by-racer": "Results by racer",
    "scale-MPH": "Scale MPH",
    "slideshow": "Slideshow",
    "standings": "Standings",
    "welcome": "Welcome",
}


@dataclass(frozen=True)
class Page:
    full: str
    brief: str
    label: str


def pages() -> list[Page]:
    return [
        Page(row["full"], row["brief"], PAGE_LABELS[row["brief"]])
        for row in kiosks.kiosk_pages()
    ]


@dataclass(frozen=True)
class Shown:
    """A page as a card shows it. A page that is not built in, such as a
    `local/kiosks` page from an imported scene, shows as Identify, the page a
    kiosk shows for it (D10)."""

    full: str
    brief: str
    label: str
    # The page's own name when it is not built in.
    custom: str
    configurable: bool

    @classmethod
    def of(cls, full: str) -> Shown:
        for page in pages():
            if page.full == full:
                return cls(
                    full,
                    page.brief,
                    page.label,
                    "",
                    full in parameters.CONFIGURABLE,
                )
        custom = full.rsplit("/", 1)[-1].removesuffix(".kiosk") if full else ""
        return cls(full, "identify", PAGE_LABELS["identify"], custom, False)


def summary(page: str, values: dict, groups: dict[int, str]) -> str:
    """One line for the card: the classes, title or switch a page's
    parameters set, as `add_classids_description()` shows the classes."""
    parts = []
    if values.get("title") and isinstance(values["title"], str):
        parts.append(values["title"])
    if page in parameters.TAKES_CLASSES and isinstance(values.get("classids"), list):
        names = [groups[i] for i in values["classids"] if i in groups]
        parts.append(", ".join(names) if names else "No classes")
    if page == parameters.AWARDS:
        parts.append("Confetti" if values.get("confetti", True) else "No confetti")
    return " · ".join(parts)


@dataclass(frozen=True)
class Card:
    address: str
    name: str
    madlib: str
    last_contact: int
    page: Shown
    summary: str
    reload: bool


@dataclass(frozen=True)
class Board:
    kiosks: list[Card]
    scenes: list[tuple[int, str]]
    current_scene_id: int | None
    named: int

    @property
    def unnamed(self) -> bool:
        return self.named < len(self.kiosks)


def group_names(event) -> dict[int, str]:
    return dict(Group.objects.filter(event=event).values_list("pk", "name"))


def board(event, now) -> Board:
    listed = kiosks.listed_kiosks(event, now)
    groups = group_names(event) if listed else {}
    cards = [
        Card(
            address=k.address,
            name=k.name,
            madlib=madlib(k.address),
            last_contact=int(k.last_contact.timestamp()),
            page=Shown.of(k.page),
            summary=summary(k.page, parameters.stored(k.params), groups),
            reload=k.reload_requested,
        )
        for k in listed
    ]
    current = (
        RaceState.objects.filter(event=event)
        .values_list("current_scene_id", flat=True)
        .get()
    )
    return Board(
        kiosks=cards,
        scenes=scene_choices(event),
        current_scene_id=current,
        named=sum(1 for c in cards if c.name),
    )


def scene_choices(event) -> list[tuple[int, str]]:
    return list(
        Scene.objects.filter(event=event)
        .order_by("sort_order", "pk")
        .values_list("pk", "name")
    )


def kiosk(event, address: str, now):
    """The listed kiosk at `address`, or None."""
    return next(
        (k for k in kiosks.listed_kiosks(event, now) if k.address == address), None
    )


# ----- Configure sheets ----------------------------------------------------------


@dataclass(frozen=True)
class Configure:
    """A Configure sheet's fields with the stored values, and what it offers."""

    page: Shown
    choices: parameters.Choices
    title: str
    subdir: str
    # None: every class, as `populate_classids()` checks them all.
    classids: frozenset[int] | None
    key: str
    confetti: bool
    content: str

    @property
    def classes(self) -> list[tuple[int, str, bool]]:
        """Each class offered, and whether its box is checked."""
        return [
            (pk, name, self.classids is None or pk in self.classids)
            for pk, name in self.choices.groups
        ]


def configure(event, page: str, text: str, default_content: str) -> Configure:
    values = parameters.stored(text)

    def string(name, default=""):
        found = values.get(name, default)
        return found if isinstance(found, str) else default

    ids = values.get("classids")
    classids = (
        frozenset(i for i in ids if isinstance(i, int))
        if isinstance(ids, list)
        else None
    )
    return Configure(
        page=Shown.of(page),
        choices=parameters.choices(event, page),
        title=string("title"),
        subdir=string("subdir"),
        classids=classids,
        key=string("key"),
        confetti=values.get("confetti", True) is not False,
        content=string("content") or default_content,
    )


# ----- the scene editor ----------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One kiosk name in the scene shown: its page there, or none."""

    kiosk_name: str
    page: Shown | None
    summary: str


@dataclass(frozen=True)
class Editor:
    scenes: list[tuple[int, str]]
    scene_id: int | None
    scene_name: str
    entries: list[Entry]
    current_scene_id: int | None


def editor(event, scene_id: int | None) -> Editor:
    """Every kiosk name any scene uses, as `scenes.php` lists them, with the
    page the chosen scene gives each. No scene chosen, or one that is gone,
    shows the first scene."""
    scenes = scene_choices(event)
    ids = [pk for pk, _ in scenes]
    if scene_id not in ids:
        scene_id = ids[0] if ids else None
    rows = {
        r.kiosk_name: r
        for r in SceneKiosk.objects.filter(event=event, scene_id=scene_id)
    }
    groups = group_names(event)
    entries = []
    for name in kiosks.scene_kiosk_names(event):
        row = rows.get(name)
        entries.append(
            Entry(
                kiosk_name=name,
                page=Shown.of(row.page) if row else None,
                summary=summary(row.page, parameters.stored(row.params), groups)
                if row
                else "",
            )
        )
    current = (
        RaceState.objects.filter(event=event)
        .values_list("current_scene_id", flat=True)
        .get()
    )
    return Editor(
        scenes=scenes,
        scene_id=scene_id,
        scene_name=dict(scenes).get(scene_id, ""),
        entries=entries,
        current_scene_id=current,
    )
