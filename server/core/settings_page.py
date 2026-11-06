"""The settings page's fields and saving one of them (#48).

`FIELDS` is the field table of `settings.php`, each with the typed control
the new page draws, its help line, and its bounds. A field saves through
`save_field()`, one call per change, which posts what `website/js/settings.js`
posts for it to `settings_form.apply_form`, so a value reads back the same
`RaceInfo` text as PHP stores. PHP's page takes any number; the bounds
here are the `min` and `max` of its inputs (`settings.php`), and the
replay fields the choices of `coordinator.php`'s replay settings dialog
(D80).

Three things differ from `settings.php`, each for #48's scope:

- a folder must be under the data folder (S25), where PHP takes any path;
- the replay post-roll and the longest clip are Django-only settings (D18),
  written to `RaceSettings` directly, since no legacy key holds them;
- the car numbering's two controls each save on their own, where PHP's
  page posts both as one `car-numbering` value.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

from django.conf import settings as django_settings
from django.db import transaction

from core import race_settings
from core.models import Event, RaceSettings
from core.services import lock_race_state
from core.settings_form import FORM_SETTINGS, SETTINGS_WRITE, apply_form
from core.setup_event import inside
from media.slides import DEFAULT_IMAGE_SET, IMAGE_SETS


class Kind(Enum):
    SWITCH = "switch"
    NUMBER = "number"
    # Shown and typed in seconds, stored in milliseconds.
    SECONDS = "seconds"
    SEGMENTED = "segmented"
    SELECT = "select"
    TEXT = "text"
    DIRECTORY = "directory"


class Section(Enum):
    TRACK = "track"
    RACERS = "racers"
    AWARDS = "awards"
    PHOTOS = "photos"
    RULES = "rules"
    REPLAY = "replay"


SECTION_TITLES = {
    Section.TRACK: "Track",
    Section.RACERS: "Racers",
    Section.AWARDS: "Awards",
    Section.PHOTOS: "Photos and video",
    Section.RULES: "Rules",
    Section.REPLAY: "Replay",
}


@dataclass(frozen=True)
class Field:
    # The `settings.write` form key, or a Django-only name.
    key: str
    section: Section
    kind: Kind
    label: str
    help: str
    unit: str = ""
    options: Sequence[tuple[str, str]] = ()
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    step: Decimal = Decimal(1)
    # Refused while unraced heats exist (`already-scheduled`).
    lane_lock: bool = False
    # A `RaceSettings` field written directly: a Django-only setting.
    model_field: str = ""
    extra: dict = field(default_factory=dict)


def _n(text: str) -> Decimal:
    return Decimal(text)


# `settings.php`'s precision radio (`finish-formatting`).
FINISH_FORMATS = (("%5.3f", "0.001 s"), ("%6.4f", "0.0001 s"))
NAME_STYLES = (("0", "First and last name"), ("1", "First name, last initial"))
NOW_RACING_PHOTOS = (("0", "None"), ("head", "Racer"), ("car", "Car"))
SCORING = (("0", "Average"), ("1", "Drop slowest"), ("2", "Fastest heat"))
# `coordinator.php`'s replay-rate choices, as a percentage.
REPLAY_RATES = (
    ("10", "0.1×"),
    ("25", "0.25×"),
    ("50", "0.5×"),
    ("75", "0.75×"),
    ("100", "1×"),
)
CAR_NUMBERS_FROM = (("101", "101"), ("1", "1"))
LANE_COLORS = ("none", "blue", "green", "orange", "pink", "purple", "red", "yellow")
# `settings.php`'s n-lanes input (`max="20"`).
MAX_LANE_COUNT = 20
MAX_TROPHIES = 20

T, R, A, P, U, Y = (
    Section.TRACK,
    Section.RACERS,
    Section.AWARDS,
    Section.PHOTOS,
    Section.RULES,
    Section.REPLAY,
)

FIELDS: tuple[Field, ...] = (
    # Track
    Field(
        "warn-no-timer",
        T,
        Kind.SWITCH,
        "Warn when no timer is connected",
        "Turn off if you enter times by hand; the race dashboard then stops"
        " warning that no timer is connected.",
    ),
    Field(
        "n-lanes",
        T,
        Kind.NUMBER,
        "Lanes on the track",
        "How many lanes the track has, used lanes or not.",
        unit="lanes",
        minimum=_n("0"),
        maximum=_n(str(MAX_LANE_COUNT)),
        lane_lock=True,
    ),
    Field(
        "reverse-lanes",
        T,
        Kind.SWITCH,
        "Number lanes in reverse",
        "Turn on when the timer counts lanes from the other side of the track.",
    ),
    Field(
        "track-length",
        T,
        Kind.NUMBER,
        "Track length",
        "Used to show speeds in scale miles per hour.",
        unit="ft",
        minimum=_n("0"),
        maximum=_n("999"),
    ),
    Field(
        "finish-formatting",
        T,
        Kind.SEGMENTED,
        "Time precision",
        "How many digits finish times show with.",
        options=FINISH_FORMATS,
    ),
    Field(
        "now-racing-linger-ms",
        T,
        Kind.SECONDS,
        "Previous heat stays on Now Racing for",
        "How long the last heat's results stay up before the next heat shows.",
        unit="s",
        minimum=_n("0"),
        maximum=_n("600"),
        step=_n("0.1"),
    ),
    # Racers
    Field(
        "supergroup-label",
        R,
        Kind.TEXT,
        "The whole roster is a",
        'What the full roster is called, such as "Pack".',
    ),
    Field(
        "partition-label",
        R,
        Kind.TEXT,
        "A sub-division is a",
        'What a sub-division of the roster is called, such as "Den".',
    ),
    Field(
        "name-style",
        R,
        Kind.SEGMENTED,
        "Show racer names as",
        "How racer names show on the kiosks and results.",
        options=NAME_STYLES,
    ),
    Field(
        "car-numbering-from",
        R,
        Kind.SEGMENTED,
        "Assigned car numbers start at",
        "The first car number given to a new racer with none.",
        options=CAR_NUMBERS_FROM,
    ),
    Field(
        "car-numbering-by-partition",
        R,
        Kind.SWITCH,
        "Hundreds place counts up for each sub-division",
        "With this on, the second sub-division's cars start at 201 (or 101).",
    ),
    # Awards
    Field(
        "n-pack-trophies",
        A,
        Kind.NUMBER,
        "Speed trophies for the whole roster",
        "How many top finishers of the whole roster get a speed trophy.",
        minimum=_n("0"),
        maximum=_n(str(MAX_TROPHIES)),
    ),
    Field(
        "n-den-trophies",
        A,
        Kind.NUMBER,
        "Speed trophies per group",
        "How many top finishers of each racing group get a speed trophy.",
        minimum=_n("0"),
        maximum=_n(str(MAX_TROPHIES)),
    ),
    Field(
        "n-rank-trophies",
        A,
        Kind.NUMBER,
        "Speed trophies per subgroup",
        "How many top finishers of each subgroup get a speed trophy.",
        minimum=_n("0"),
        maximum=_n(str(MAX_TROPHIES)),
    ),
    Field(
        "one-trophy-per",
        A,
        Kind.SWITCH,
        "At most one trophy per racer",
        "A racer who wins at a higher level is left out of the lower levels.",
    ),
    Field(
        "use-xbs",
        A,
        Kind.SWITCH,
        'Offer an "Exclusively By Scout" award',
        "Adds the award for cars the racer built on their own.",
    ),
    Field(
        "xbs-award",
        A,
        Kind.TEXT,
        '"Exclusively By Scout" award name',
        "The award's name, when it is offered.",
    ),
    # Photos
    Field(
        "images-dir",
        P,
        Kind.SELECT,
        "Image set",
        "The artwork the kiosks show.",
    ),
    Field(
        "photos-on-now-racing",
        P,
        Kind.SEGMENTED,
        "Photos on Now Racing",
        "Which photo the Now Racing kiosk shows for each car.",
        options=NOW_RACING_PHOTOS,
    ),
    Field(
        "show-car-photos-on-deck",
        P,
        Kind.SWITCH,
        "Car photos on the On Deck kiosk",
        "Shows each car's photo beside the racer on deck.",
    ),
    Field(
        "show-racer-photos-rr",
        P,
        Kind.SWITCH,
        "Racer photos on Results By Racer",
        "Shows each racer's photo in the results by racer.",
    ),
    Field(
        "show-car-photos-rr",
        P,
        Kind.SWITCH,
        "Car photos on Results By Racer",
        "Shows each car's photo in the results by racer.",
    ),
    Field(
        "photo-dir",
        P,
        Kind.DIRECTORY,
        "Racer photos folder",
        "Where racer photos are kept, under the data folder.",
    ),
    Field(
        "car-photo-dir",
        P,
        Kind.DIRECTORY,
        "Car photos folder",
        "Where car photos are kept, under the data folder.",
    ),
    Field(
        "upload-videos",
        P,
        Kind.SWITCH,
        "Upload replay videos",
        "The replay station saves each heat's clip to the videos folder.",
    ),
    Field(
        "video-dir",
        P,
        Kind.DIRECTORY,
        "Videos folder",
        "Where replay clips are kept, under the data folder.",
    ),
    # Rules
    Field(
        "use-master-sched",
        U,
        Kind.SWITCH,
        "Interleave heats from different groups",
        "Runs the heats of rounds at the same level in one shared order.",
    ),
    Field(
        "use-points",
        U,
        Kind.SWITCH,
        "Race by points",
        "Ranks racers by their places in each heat instead of by their times.",
    ),
    Field(
        "scoring",
        U,
        Kind.SEGMENTED,
        "Scoring",
        "Average every heat time, drop each car's slowest, or take its fastest.",
        options=SCORING,
    ),
    # Replay (D9, D18). #53 reads all five.
    Field(
        "replay-rate",
        Y,
        Kind.SEGMENTED,
        "Playback speed",
        "How fast each replay plays, slower than real time.",
        options=REPLAY_RATES,
    ),
    Field(
        "replay-num-showings",
        Y,
        Kind.NUMBER,
        "Showings",
        "How many times each replay plays.",
        unit="times",
        minimum=_n("1"),
        maximum=_n("10"),
    ),
    Field(
        "replay-skipback",
        Y,
        Kind.SECONDS,
        "Recorded before the last car finishes",
        "How much of the race before the finish each clip shows.",
        unit="s",
        minimum=_n("0.5"),
        maximum=_n("10"),
        step=_n("0.5"),
    ),
    Field(
        "replay-post-roll",
        Y,
        Kind.SECONDS,
        "Recorded after the last car finishes",
        "How long each clip runs past the finish line (D18).",
        unit="s",
        minimum=_n("0"),
        maximum=_n("10"),
        step=_n("0.5"),
        model_field="replay_post_roll_ms",
    ),
    Field(
        "replay-max-clip",
        Y,
        Kind.SECONDS,
        "Longest clip",
        "A clip never runs longer than this (D18).",
        unit="s",
        minimum=_n("1"),
        maximum=_n("30"),
        step=_n("0.5"),
        model_field="replay_max_clip_ms",
    ),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}
CAR_NUMBERING_KEYS = ("car-numbering-from", "car-numbering-by-partition")


class SaveRefused(Exception):
    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


def image_sets() -> list[tuple[str, str]]:
    """The image sets, `Generic` first, as `emit_images_dir_select()`
    orders them (`website/inc/pick_image_set.inc`)."""
    names = sorted(p.name for p in IMAGE_SETS.iterdir() if p.is_dir())
    names.sort(key=lambda n: n != DEFAULT_IMAGE_SET)
    return [(n, n) for n in names if not n.startswith(".")]


def options(f: Field) -> Sequence[tuple[str, str]]:
    return image_sets() if f.kind is Kind.SELECT else f.options


def _decimal(raw: str, f: Field) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite():
        raise SaveRefused("not-a-number", f"{f.label}: type a number.")
    if (f.minimum is not None and value < f.minimum) or (
        f.maximum is not None and value > f.maximum
    ):
        raise SaveRefused(
            "out-of-range", f"{f.label}: pick {f.minimum} to {f.maximum}."
        )
    if f.kind is Kind.NUMBER and value != value.to_integral_value():
        raise SaveRefused("not-a-number", f"{f.label}: type a whole number.")
    return value


def _milliseconds(value: Decimal) -> str:
    return str(int((value * 1000).to_integral_value()))


def _post_for(f: Field, raw: str) -> dict[str, str]:
    """What `settings.js` posts for the field."""
    match f.kind:
        case Kind.SWITCH:
            on = {f"{f.key}": "1"} if raw == "1" else {}
            return {f"{f.key}-checkbox": "yes", **on}
        case Kind.NUMBER:
            return {f.key: str(int(_decimal(raw, f)))}
        case Kind.SECONDS:
            return {f.key: _milliseconds(_decimal(raw, f))}
        case Kind.SEGMENTED | Kind.SELECT:
            if raw not in {value for value, _ in options(f)}:
                raise SaveRefused("bad-choice", f"{f.label}: pick one of the choices.")
            return {f.key: raw}
        case Kind.DIRECTORY:
            path = raw.strip()
            if path and inside(path) is None:
                raise SaveRefused(
                    "outside",
                    f"{f.label}: pick a folder under {django_settings.DATA_DIR}.",
                )
            return {f.key: path}
        case _:
            return {f.key: raw}


def _apply(event: Event, post: dict) -> None:
    result = apply_form(event, {**SETTINGS_WRITE, **post})
    if result.failure:
        raise SaveRefused(*result.failure)


def car_numbering(text: str) -> tuple[bool, str]:
    """`read_car_numbering_values()`: (hundreds per sub-division, first
    number) of `car-numbering`, `100+101` by default."""
    mult, _, smallest = (text or "100+101").partition("+")
    return mult.strip() not in ("0", ""), smallest.strip() or "101"


def _save_car_numbering(event: Event, f: Field, raw: str) -> None:
    """`on_car_numbering_change()` writes `<0 or 100>+<101 or 1>`."""
    by_partition, smallest = car_numbering(race_settings.read(event, "car-numbering"))
    if f.key == "car-numbering-from":
        if raw not in {v for v, _ in CAR_NUMBERS_FROM}:
            raise SaveRefused("bad-choice", f"{f.label}: pick 101 or 1.")
        smallest = raw
    else:
        by_partition = raw == "1"
    _apply(event, {"car-numbering": f"{'100' if by_partition else '0'}+{smallest}"})


def save_field(event: Event, key: str, raw: str) -> None:
    """Save one field of the settings page. Raises `SaveRefused` with
    `settings.write`'s code (`locked`, `already-scheduled`, `not-written`)
    or the page's own (`out-of-range`, `outside`, ...)."""
    f = BY_KEY.get(key)
    if f is None or not isinstance(raw, str):
        raise SaveRefused("unrecognized", "That is not a setting.")
    with transaction.atomic():
        lock_race_state(event.pk)
        if key in CAR_NUMBERING_KEYS:
            _save_car_numbering(event, f, raw)
        elif f.model_field:
            ms = int(_milliseconds(_decimal(raw, f)))
            RaceSettings.objects.filter(event=event).update(**{f.model_field: ms})
        else:
            _apply(event, _post_for(f, raw))


def save_lane_colors(event: Event, colors: Sequence[str]) -> None:
    """The lane colors dialog, one color per lane: `on_lane_color_change()`
    writes them joined by commas, or "" when every lane is `none`."""
    if any(c not in LANE_COLORS for c in colors):
        raise SaveRefused("bad-choice", "Pick a color for each lane.")
    text = ",".join(colors) if any(c != "none" for c in colors) else ""
    _apply(event, {"lane-colors": text})


def lane_colors(text: str, lane_count: int) -> list[str]:
    """Each lane's color from `lane-colors`, `none` for a lane it leaves out."""
    given = (text or "").split(",") if text else []
    return [
        given[i] if i < len(given) and given[i] in LANE_COLORS else "none"
        for i in range(lane_count)
    ]


def value_of(event_settings: RaceSettings, values: dict[str, str | None], f: Field):
    """The field's value as the page shows it."""
    if f.model_field:
        return _seconds(getattr(event_settings, f.model_field))
    if f.key in CAR_NUMBERING_KEYS:
        by_partition, smallest = car_numbering(values["car-numbering"] or "")
        return smallest if f.key == "car-numbering-from" else by_partition
    text = values[FORM_SETTINGS[f.key].setting] or ""
    match f.kind:
        case Kind.SWITCH:
            return text not in ("", "0")
        case Kind.SECONDS:
            return _seconds(int(text or 0))
    return text


def _seconds(ms: int) -> str:
    return f"{Decimal(ms) / 1000:f}".rstrip("0").rstrip(".") or "0"


def read_values(event: Event) -> dict[str, str | None]:
    """The legacy text of every setting the page shows."""
    keys = {FORM_SETTINGS[f.key].setting for f in FIELDS if f.key in FORM_SETTINGS}
    return race_settings.read_many(event, sorted(keys | {"car-numbering"}))
