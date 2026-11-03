"""The settings form: `settings.write`'s table of form keys, and applying a post (#63).

The settings page and the preferences parser (#36) share `FORM_SETTINGS`, so a
key is added here once. PHP's rules are kept: a boolean is written only when
its `<key>-checkbox` field is also posted, a field PHP does not know makes the
whole post `unrecognized` after the known ones are written, and the lane
settings are refused while unraced heats exist.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from core import race_settings
from core.models import Event, RaceSettings
from core.services import lock_race_state
from racing.engine.phpcompat import php_intval
from racing.models import HeatLane
from racing.services.lanes import mask_of
from roster.models import Group, GroupConstituent, GroupConstituentSubgroup


class Kind(Enum):
    TEXT = "text"
    # PHP's `strval(intval($v))`: text that is not a number is zero.
    INT = "int"
    # A number as typed, or 0.0 for text that is not one.
    NUMBER = "number"
    CHECKBOX = "checkbox"


@dataclass(frozen=True)
class FormSetting:
    setting: str
    kind: Kind = Kind.TEXT


FORM_SETTINGS: dict[str, FormSetting] = {
    "ballot_password": FormSetting("ballot_password"),
    "car-numbering": FormSetting("car-numbering"),
    "car-photo-dir": FormSetting("car-photo-directory"),
    "do-use-subgroups": FormSetting("use-subgroups", Kind.CHECKBOX),
    "finish-formatting": FormSetting("finish-formatting"),
    "images-dir": FormSetting("images-dir"),
    "lane-colors": FormSetting("lane-colors"),
    "log-dir": FormSetting("logs-directory"),
    "max-runs-per-car": FormSetting("max-runs-per-car", Kind.INT),
    "n-den-trophies": FormSetting("n-den-trophies", Kind.INT),
    "n-lanes": FormSetting("lane_count", Kind.INT),
    "n-pack-trophies": FormSetting("n-pack-trophies", Kind.INT),
    "n-rank-trophies": FormSetting("n-rank-trophies", Kind.INT),
    "name-style": FormSetting("name-style"),
    "now-racing-linger-ms": FormSetting("now-racing-linger-ms"),
    "one-trophy-per": FormSetting("one-trophy-per", Kind.CHECKBOX),
    "partition-label": FormSetting("partition-label"),
    "photo-dir": FormSetting("photo-directory"),
    "photos-on-now-racing": FormSetting("photos-on-now-racing"),
    "racing_scene": FormSetting("racing_scene"),
    "replay-num-showings": FormSetting("replay-num-showings", Kind.INT),
    "replay-rate": FormSetting("replay-rate", Kind.NUMBER),
    "replay-skipback": FormSetting("replay-skipback", Kind.NUMBER),
    "reverse-lanes": FormSetting("reverse-lanes", Kind.CHECKBOX),
    "rotation-schedule": FormSetting("rotation-schedule"),
    "scoring": FormSetting("scoring"),
    "show-car-photos-on-deck": FormSetting("show-cars-on-deck", Kind.CHECKBOX),
    "show-car-photos-rr": FormSetting("show-car-photos-rr", Kind.CHECKBOX),
    "show-racer-photos-rr": FormSetting("show-racer-photos-rr", Kind.CHECKBOX),
    "supergroup-label": FormSetting("supergroup-label"),
    "timer-send-logs": FormSetting("timer-send-logs"),
    "track-length": FormSetting("track-length", Kind.NUMBER),
    "unused-lane-mask": FormSetting("unused-lane-mask"),
    "update-period": FormSetting("update-period"),
    "upload-videos": FormSetting("upload-videos", Kind.CHECKBOX),
    "use-master-sched": FormSetting("use-master-sched", Kind.CHECKBOX),
    "use-points": FormSetting("use-points", Kind.CHECKBOX),
    "use-xbs": FormSetting("use-xbs", Kind.CHECKBOX),
    "video-dir": FormSetting("video-directory"),
    "warn-no-timer": FormSetting("warn-no-timer", Kind.CHECKBOX),
    "xbs-award": FormSetting("xbs-award"),
}

# Refused while `LOCK_SETTINGS` is on. PHP leaves `log-dir` out of its list.
LOCKED_FORM_KEYS = ("photo-dir", "car-photo-dir", "video-dir")
LANE_FORM_KEYS = {"n-lanes": "lane_count", "unused-lane-mask": "unused-lane-mask"}

_NUMERIC = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*")


def _number(text: str) -> str:
    """A number stays as typed. Text that is not one is zero, which PHP writes
    as `0.0`; a whole number is written without the point, as its field holds."""
    if not _NUMERIC.fullmatch(text):
        return "0"
    value = float(text)
    return str(int(value)) if value.is_integer() else text.strip()


def _differs(posted: str, current: str) -> bool:
    """PHP's `!=` on two strings, which compares two numbers as numbers."""
    if _NUMERIC.fullmatch(posted) and _NUMERIC.fullmatch(current):
        return float(posted) != float(current)
    return posted != current


@dataclass
class FormResult:
    written: list[str]
    # (code, description) of the last failure, or None for success.
    failure: tuple[str, str] | None


def lanes_locked(event: Event) -> bool:
    """Unraced heats exist, so a change of the lane count or the unused
    lanes is refused (`already-scheduled`)."""
    return HeatLane.objects.filter(event=event, completed__isnull=True).exists()


def _lane_change_refused(event: Event, post: Mapping) -> bool:
    for form_key, setting in LANE_FORM_KEYS.items():
        posted = post.get(form_key)
        if posted is None:
            continue
        if not isinstance(posted, str) or _differs(
            posted, race_settings.read(event, setting) or ""
        ):
            return lanes_locked(event)
    return False


def _value(setting: FormSetting, form_key: str, post: Mapping) -> str:
    posted = post[f"{form_key}-checkbox" if setting.kind is Kind.CHECKBOX else form_key]
    if setting.kind is Kind.CHECKBOX:
        return "1" if form_key in post else "0"
    if setting.kind is Kind.INT:
        return str(php_intval(posted))
    if setting.kind is Kind.NUMBER:
        return _number(posted)
    return posted


@transaction.atomic
def apply_form(event: Event, post: Mapping) -> FormResult:
    """Write every known field of a `settings.write` post.

    PHP stops at the lock and lane refusals before it writes anything, and
    carries on past a value it cannot store. Where two failures happen the
    last one is the reply.
    """
    lock_race_state(event.pk)
    failure = None
    if settings.LOCK_SETTINGS and any(key in post for key in LOCKED_FORM_KEYS):
        failure = ("locked", "Directory settings are locked and cannot be changed.")
    if _lane_change_refused(event, post):
        failure = (
            "already-scheduled",
            "Remove unraced schedules before changing lane counts.",
        )
    written: list[str] = []
    if failure:
        return FormResult(written, failure)

    picked = 1  # The `action` field.
    for form_key, setting in FORM_SETTINGS.items():
        post_key = f"{form_key}-checkbox" if setting.kind is Kind.CHECKBOX else form_key
        if post_key not in post:
            continue
        picked += 1
        written.append(post_key)
        if setting.kind is Kind.CHECKBOX and form_key in post:
            picked += 1
        posted = post[post_key]
        if not isinstance(posted, str):
            failure = _not_written(setting.setting)
            continue
        try:
            race_settings.write(event, setting.setting, _value(setting, form_key, post))
        except race_settings.NotWritten:
            failure = _not_written(setting.setting)

    if failure is None and picked != len(post):
        failure = ("unrecognized", f"{len(post) - picked} unrecognized setting(s)")
    return FormResult(written, failure)


def _not_written(setting: str) -> tuple[str, str]:
    return ("not-written", f"Value for {setting} could not be written.")


class PackStandingsRefused(Exception):
    """The pack standings choice the group editor cannot store (#43)."""


def _pack_trophies(event: Event) -> bool:
    return (RaceSettings.objects.get(event=event).n_pack_trophies or 0) > 0


@transaction.atomic
def apply_group_form(event: Event, post: Mapping) -> FormResult:
    """A field of the racing groups editor (#43): `apply_form`, and a pack
    trophy count above zero turns "Don't calculate" pack standings back to
    "Calculate normally", as `racing-groups.js` does, since pack trophies
    need the pack standings. `set_pack_standings` holds the other half of
    that rule."""
    result = apply_form(event, post)
    if (
        result.failure is None
        and "n-pack-trophies" in post
        and _pack_trophies(event)
        and race_settings.read(event, "full-field-calc") == "-1"
    ):
        race_settings.write(event, "full-field-calc", "0")
    return result


@transaction.atomic
def set_pack_standings(event: Event, value: str) -> None:
    """The group editor's pack standings choice (`award.calc-rule`): `0`
    calculate normally, `-1` don't calculate, or an aggregate's id to use
    its standings. "Don't calculate" is refused while pack trophies exist,
    which need the pack standings (`racing-groups.js`), and anything but an
    aggregate of the event is refused."""
    lock_race_state(event.pk)
    if value == "-1" and _pack_trophies(event):
        raise PackStandingsRefused("Pack trophies need the pack standings.")
    if value not in ("0", "-1"):
        aggregates = Group.objects.filter(event=event).filter(
            Q(pk__in=GroupConstituent.objects.values("aggregate_id"))
            | Q(pk__in=GroupConstituentSubgroup.objects.values("aggregate_id"))
        )
        if not value.isdigit() or not aggregates.filter(pk=int(value)).exists():
            raise PackStandingsRefused("Pick how to calculate the standings.")
    try:
        race_settings.write(event, "full-field-calc", value)
    except race_settings.NotWritten:
        raise PackStandingsRefused("Pick how to calculate the standings.") from None


# ----- the scheduling method and the lanes in use (#44) -----------------------------

SETTINGS_WRITE = {"action": "settings.write"}

# The scheduling method radio of `settings.php` to the two settings it
# writes, as `on_schedule_method_change()` in `website/js/settings.js`.
SCHEDULING_METHODS: dict[str, dict[str, str]] = {
    "normal": {"max-runs-per-car": "0", "rotation-schedule": "0"},
    "abbreviated": {"max-runs-per-car": "1", "rotation-schedule": "0"},
    "rotation": {"max-runs-per-car": "0", "rotation-schedule": "1"},
}


def scheduling_method(settings: RaceSettings) -> str:
    """`settings.php`: abbreviated when max-runs-per-car is set, else
    rotation when rotation-schedule is on, else normal."""
    if settings.max_runs_per_car:
        return "abbreviated"
    if settings.rotation_schedule:
        return "rotation"
    return "normal"


def set_scheduling_method(event: Event, method: str) -> FormResult:
    """Raises KeyError for a method that is not one of `SCHEDULING_METHODS`."""
    return apply_form(event, {**SETTINGS_WRITE, **SCHEDULING_METHODS[method]})


def set_lanes_in_use(event: Event, lane_count: int, in_use) -> FormResult:
    """The unused-lane mask: lanes 1 to `lane_count`, the count the page
    showed, that are not in `in_use`. While unraced heats exist the change
    is refused (`already-scheduled`)."""
    unused = [n for n in range(1, lane_count + 1) if n not in set(in_use)]
    return apply_form(
        event, {**SETTINGS_WRITE, "unused-lane-mask": str(mask_of(unused))}
    )
