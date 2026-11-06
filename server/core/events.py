"""The event lifecycle of #9: list, create with Copy from, switch, archive,
and Reopen for editing (#212).

One event is active at a time (D12). Field clients send no event id and
talk to the active event, so a switch is one update of `SiteState`, and
kiosks and the timer move on their next request (#9, Switch semantics).

Past events are read-only (#9, Past events): a switch makes the event it
replaces read-only, and archiving an event makes it read-only too. Every
write service refuses a read-only event in `lock_race_state`, and "Reopen
for editing" unlocks one. Each change to `Event.read_only` here takes that
event's `RaceState` lock, so a write service that holds the lock reads the
flag as it stays until its commit.

PHP has no copy, switch rule, archive or reopen: `setup.php` creates and
opens event folders only (`action.setup.nodata.inc`).
"""

from dataclasses import dataclass

from django.conf import settings as django_settings
from django.db import models, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from awards.listing import AD_HOC
from awards.models import Award, AwardPartition, BallotAward
from core.models import (
    Event,
    FullFieldRule,
    RaceSettings,
    SiteState,
    is_event_scoped,
)
from core.services import event_dir, lock_race_state, new_event
from kiosks.models import Kiosk, Scene, SceneKiosk
from roster.models import (
    CarRange,
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Racer,
    Subgroup,
)

SUBFOLDERS = ("racers", "cars", "videos", "logs", "imagery", "slides")
MAX_NAME_LENGTH = Event._meta.get_field("name").max_length


class Part(models.TextChoices):
    """What Copy from offers (D14). Classes and ranks bring their
    partitions, and award definitions leave winners and ad hoc awards
    behind (D81)."""

    SETTINGS = "settings", "Settings"
    GROUPS = "groups", "Classes and ranks"
    AWARDS = "awards", "Award definitions"
    KIOSKS = "kiosks", "Kiosk names"
    SCENES = "scenes", "Scenes"


ALL_PARTS = tuple(Part)

# Every event-scoped model, by label, and the part that copies it. A model
# no part copies says why. `core/tests/test_event_lifecycle.py` fails a new
# event-scoped model that is in neither.
COPIED = {
    "core.racesettings": Part.SETTINGS,
    "roster.group": Part.GROUPS,
    "roster.subgroup": Part.GROUPS,
    "roster.groupconstituent": Part.GROUPS,
    "roster.groupconstituentsubgroup": Part.GROUPS,
    # Partitions are the ranks a racer registers in under the by-partition
    # rule, and their car number ranges and weigh-in rules go with them.
    "roster.partition": Part.GROUPS,
    "roster.carrange": Part.GROUPS,
    "awards.award": Part.AWARDS,
    "awards.ballotaward": Part.AWARDS,
    "awards.awardpartition": Part.AWARDS,
    "kiosks.kiosk": Part.KIOSKS,
    "kiosks.scene": Part.SCENES,
    "kiosks.scenekiosk": Part.SCENES,
}
NOT_COPIED = {
    "roster.racer": "racers are never copied (#9, Copy from)",
    "awards.voter": "a ballot's voters are race-day results",
    "awards.vote": "votes are results",
    "racing.round": "rounds are the schedule, made from the new roster",
    "racing.rosterentry": "a round's racers",
    "racing.heatlane": "results are never copied (#9, Copy from)",
    "racing.emptiedheat": "the schedule",
    "racing.laneclosure": "the schedule",
    "racing.finalrequest": "finalists are results",
    "racing.playlistentry": "the playlist names rounds of the old schedule",
    "racing.auditevent": "the race-day audit",
    "racing.auditlaneresult": "the race-day audit",
    "racing.timersetting": "what the connected timer reported",
    "core.racestate": "live race state starts empty",
    "racing.timerstatus": "live timer state starts empty",
    "kiosks.replaystatus": "live replay state starts empty",
    "kiosks.replaycommand": "the replay queue",
    "kiosks.signalmessage": "the message queue",
    "core.actionlog": "the old event's history",
    "transfer.legacyid": "ids of the old event's import",
}
# Not copied from RaceSettings: the folders are the new event's own.
EVENT_FOLDERS = (
    "photo_directory",
    "car_photo_directory",
    "video_directory",
    "logs_directory",
)


# Not frozen: a context manager sets `__traceback__` on an exception that
# passes through it.
@dataclass(eq=False)
class EventRefused(Exception):
    code: str
    description: str


def refuse_if_locked() -> None:
    """`DERBYNET_LOCK_SETTINGS` hides event management (#9, Hosted lock):
    creating, switching, archiving and reopening an event refuse with
    `locked`, as `setup.nodata` does (`legacy_api/handlers/setup.py`);
    export stays available (D81)."""
    if django_settings.LOCK_SETTINGS:
        raise EventRefused("locked", "Settings are locked and cannot be changed.")


class State(models.TextChoices):
    ACTIVE = "active", "Active"
    READ_ONLY = "read-only", "Read-only"
    ARCHIVED = "archived", "Archived"
    EDITABLE = "editable", "Open for editing"

    @property
    def pill(self) -> str:
        """The status pill's colour on the events page."""
        return {"active": "ok", "read-only": "warn"}.get(self.value, "idle")


def state_of(event: Event, active_id: int | None) -> list[State]:
    """The event's states, as the events page shows them: active or not,
    and read-only, archived, or reopened for editing."""
    states = [State.ACTIVE] if event.pk == active_id else []
    if event.archived_at is not None:
        states.append(State.ARCHIVED)
    elif event.read_only:
        states.append(State.READ_ONLY)
    elif event.pk != active_id:
        states.append(State.EDITABLE)
    return states


@dataclass(frozen=True)
class Listed:
    event: Event
    racers: int
    states: list[State]

    @property
    def active(self) -> bool:
        return State.ACTIVE in self.states

    @property
    def archived(self) -> bool:
        return self.event.archived_at is not None


def listing() -> list[Listed]:
    """Every event, newest first, with its racer count and states."""
    racers = (
        Racer.objects.filter(event=OuterRef("pk"))
        .order_by()
        .values("event")
        .annotate(n=Count("pk"))
        .values("n")
    )
    events = (
        Event.objects.select_related("copied_from")
        .annotate(racer_count=Coalesce(Subquery(racers), 0))
        .order_by("-created_at", "-pk")
    )
    active_id = (
        SiteState.objects.filter(pk=1).values_list("active_event_id", flat=True).first()
    )
    return [Listed(e, e.racer_count, state_of(e, active_id)) for e in events]


def valid_name(name: str) -> bool:
    return (
        bool(name.strip())
        and len(name) <= MAX_NAME_LENGTH
        and not any(c in name for c in "/\\\0")
        and name not in (".", "..")
    )


def make_folders(event: Event) -> None:
    root = event_dir(event.slug)
    for sub in SUBFOLDERS:
        try:
            (root / sub).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise EventRefused(f"mkdir-{sub}", f"{root / sub}: {error}") from None


def create_event(name: str, *, copy_from: int | None = None, parts=ALL_PARTS) -> Event:
    """A new event with its folders, not made active (D81: switching is
    its own step). With `copy_from`, the `parts` of that event come across;
    racers, results and photos never do (#9, Copy from)."""
    refuse_if_locked()
    name = name.strip()
    parts = frozenset(Part(p) for p in parts)
    if not valid_name(name):
        raise EventRefused("name", "An event needs a name, without / or \\.")
    if Part.AWARDS in parts and Part.GROUPS not in parts:
        # An award definition names the class or rank it is for, and its
        # partition list names partitions. With no class copied, each such
        # award would become a pack award, or name another event's class
        # (D81).
        raise EventRefused(
            "copy-awards-needs-classes",
            "Award definitions name classes and ranks, so copy those too.",
        )
    with transaction.atomic():
        source = None
        if copy_from is not None:
            source = Event.objects.filter(pk=copy_from).first()
            if source is None:
                raise EventRefused("no-such-event", "That event no longer exists.")
            # The copy reads the source whole, so no write lands mid-copy.
            lock_race_state(source.pk, writable=False)
        if Event.objects.filter(name=name).exists():
            raise EventRefused("already_exists", f"An event called {name} exists.")
        event = new_event(name)
        make_folders(event)
        if source is not None:
            _copy(source, event, parts)
            event.copied_from = source
            event.save(update_fields=["copied_from"])
    return event


def _row_values(row, remap: dict, reset=()) -> dict:
    """`row`'s column values for a copy: each foreign key through `remap`,
    the `reset` fields at their defaults. A relation `remap` does not name
    would point into the source event, so it is refused."""
    values = {}
    for field in type(row)._meta.concrete_fields:
        if field.primary_key or field.name == "event":
            continue
        if field.name in reset:
            values[field.attname] = field.get_default()
            continue
        value = getattr(row, field.attname)
        if field.name in remap:
            value = None if value is None else remap[field.name].get(value)
        elif field.is_relation and is_event_scoped(field.related_model):
            raise AssertionError(
                f"{type(row).__name__}.{field.name} has no mapping for a copy"
            )
        values[field.attname] = value
    return values


def _clone(model, source, event, remap=None, reset=(), rows=None) -> dict[int, int]:
    """Copy `model`'s rows of `source` into `event`. Returns old pk to new."""
    remap = remap or {}
    rows = model.objects.filter(event=source) if rows is None else rows
    ids = {}
    for row in rows.order_by("pk"):
        ids[row.pk] = model.objects.create(
            event=event, **_row_values(row, remap, reset)
        ).pk
    return ids


def _copy(source: Event, event: Event, parts: frozenset) -> None:
    groups: dict[int, int] = {}
    subgroups: dict[int, int] = {}
    partitions: dict[int, int] = {}
    if Part.GROUPS in parts:
        groups = _clone(Group, source, event)
        subgroups = _clone(Subgroup, source, event, {"group": groups})
        _clone(
            GroupConstituent,
            source,
            event,
            {"aggregate": groups, "constituent": groups},
        )
        _clone(
            GroupConstituentSubgroup,
            source,
            event,
            {"aggregate": groups, "subgroup": subgroups},
        )
        partitions = _clone(Partition, source, event, {"subgroup": subgroups})
        _clone(CarRange, source, event, {"partition": partitions})
    if Part.AWARDS in parts:
        # A definition, not its winner. An ad hoc award exists only for the
        # racer it was given to, so it is a result.
        definitions = Award.objects.filter(event=source).exclude(award_type_id=AD_HOC)
        awards = _clone(
            Award,
            source,
            event,
            {"group": groups, "subgroup": subgroups},
            reset=("racer",),
            rows=definitions,
        )
        _clone(
            BallotAward,
            source,
            event,
            {"award": awards},
            rows=BallotAward.objects.filter(event=source, award_id__in=awards),
        )
        _clone(
            AwardPartition,
            source,
            event,
            {
                "group": groups,
                "subgroup": subgroups,
                "award": awards,
                "partition": partitions,
            },
            # A pack, group or subgroup unit, or a copied award's.
            rows=AwardPartition.objects.filter(event=source).filter(
                Q(award__isnull=True) | Q(award_id__in=list(awards))
            ),
        )
    if Part.KIOSKS in parts:
        _clone(
            Kiosk,
            source,
            event,
            reset=("page", "params", "reload_requested", "last_contact"),
        )
    if Part.SCENES in parts:
        # The six legacy scenes `new_event` made give way to the source's.
        Scene.objects.filter(event=event).delete()
        scenes = _clone(Scene, source, event)
        _clone(SceneKiosk, source, event, {"scene": scenes})
    if Part.SETTINGS in parts:
        _copy_settings(source, event, groups)


def _copy_settings(source: Event, event: Event, groups: dict[int, int]) -> None:
    old = RaceSettings.objects.get(event=source)
    new_scenes = dict(Scene.objects.filter(event=event).values_list("name", "pk"))
    values = _row_values(
        old,
        {
            "full_field_group": groups,
            # By name: the new event's scene of that name, copied or not.
            "racing_scene": {
                pk: new_scenes.get(name)
                for pk, name in Scene.objects.filter(event=source).values_list(
                    "pk", "name"
                )
            },
        },
        reset=EVENT_FOLDERS,
    )
    for folder in EVENT_FOLDERS:
        values.pop(folder)
    if values["full_field_group_id"] is None:
        values["full_field_rule"] = (
            FullFieldRule.STANDARD
            if old.full_field_rule == FullFieldRule.GROUP
            else old.full_field_rule
        )
    RaceSettings.objects.filter(event=event).update(**values)


# LOCK ORDER: a transaction that locks `SiteState` and an event's
# `RaceState` takes `SiteState` first (`lock_site_state`), then the
# `RaceState`. Two transactions that took them in opposite orders could
# deadlock on Postgres: a switch (`make_active`) against an import that
# replaces the active event (`transfer.importer`).


def lock_site_state() -> SiteState | None:
    """The site row, locked: the first lock of LOCK ORDER."""
    return SiteState.objects.select_for_update().filter(pk=1).first()


@dataclass(frozen=True)
class Switched:
    replaced: Event | None
    active: Event


def make_active(event: Event) -> Switched:
    """Make `event` the active one, and the event it replaces read-only
    (#9, Past events; D14). Refused with `racing` while the active event's
    racing flag is on, stricter than #9's "heat running" (D81). A switch
    never unlocks `event`: a past event stays read-only until Reopen for
    editing (D81). Call it inside `transaction.atomic()`."""
    refuse_if_locked()
    state = lock_site_state()
    if state is None:
        SiteState.objects.create(pk=1, active_event=event)
        return Switched(None, event)
    replaced = state.active_event
    if replaced.pk == event.pk:
        return Switched(None, event)
    race = lock_race_state(replaced.pk, writable=False)
    if race.racing:
        raise EventRefused(
            "racing",
            f"{replaced.name} is racing. Stop racing before switching events.",
        )
    Event.objects.filter(pk=replaced.pk).update(read_only=True)
    state.active_event = event
    state.save(update_fields=["active_event"])
    replaced.refresh_from_db()
    return Switched(replaced, event)


def switch_to(event_id: int) -> Switched:
    """The events page's Switch: make the event the active one."""
    with transaction.atomic():
        event = Event.objects.filter(pk=event_id).first()
        if event is None:
            raise EventRefused("no-such-event", "That event no longer exists.")
        return make_active(event)


def archive(event_id: int) -> Event:
    """Archive a past event: it becomes read-only and is marked archived.
    The active event is refused, since the field clients are using it; an
    archived event can still be made active, staying read-only (D81)."""
    refuse_if_locked()
    with transaction.atomic():
        # SiteState first, as make_active takes it (LOCK ORDER), so a switch
        # cannot make the event active between this check and the archive.
        state = lock_site_state()
        event = _locked(event_id)
        if state is not None and state.active_event_id == event.pk:
            raise EventRefused(
                "active", "Switch to another event before archiving this one."
            )
        if event.archived_at is None:
            event.archived_at = timezone.now()
        event.read_only = True
        event.save(update_fields=["archived_at", "read_only"])
    return event


def reopen(event_id: int) -> Event:
    """Reopen for editing: the event takes changes again, and is no longer
    archived. The active event stays as it is (D81)."""
    refuse_if_locked()
    with transaction.atomic():
        event = _locked(event_id)
        event.read_only = False
        event.archived_at = None
        event.save(update_fields=["archived_at", "read_only"])
    return event


def _locked(event_id: int) -> Event:
    """The event, read under its `RaceState` lock."""
    if not Event.objects.filter(pk=event_id).exists():
        raise EventRefused("no-such-event", "That event no longer exists.")
    lock_race_state(event_id, writable=False)
    return Event.objects.get(pk=event_id)
