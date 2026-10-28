from graphlib import TopologicalSorter

from django.conf import settings
from django.db import models, transaction
from django.utils.text import slugify

from core.fake_images import remove_fake_images
from core.models import (
    Event,
    PerEvent,
    RaceSettings,
    RaceState,
    SiteState,
    event_scoped_models,
)
from kiosks.models import ReplayStatus, Scene, SceneKiosk
from racing.models import TimerStatus

# website/sql/sqlite/scene-tables.inc: (name, Main page, Aux1 page, Aux2 page).
# The scene's sort order is its position, starting at 1.
LEGACY_SCENES = [
    ("Welcome", "kiosks/welcome.kiosk", "", ""),
    ("Opening", "kiosks/flag.kiosk", "kiosks/flag.kiosk", "kiosks/flag.kiosk"),
    (
        "Check-In",
        "kiosks/slideshow.kiosk",
        "kiosks/please-check-in.kiosk",
        "kiosks/welcome.kiosk",
    ),
    (
        "Racing",
        "kiosks/now-racing.kiosk",
        "kiosks/ondeck.kiosk",
        "kiosks/results-by-racer.kiosk",
    ),
    ("Awards", "kiosks/award-presentations.kiosk", "", ""),
    (
        "Wrap-Up",
        "kiosks/standings.kiosk",
        "kiosks/derbynet.kiosk",
        "local/kiosks/feedback.kiosk",
    ),
]
KIOSK_NAMES = ("Main", "Aux1", "Aux2")


def _unique_slug(name):
    base = slugify(name) or "event"
    slug, n = base, 1
    while Event.objects.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


def event_dir(slug):
    return settings.DATA_DIR / "events" / slug


def _create_event_rows(event, folders):
    """The per-event rows with their defaults, and the six legacy scenes."""
    RaceSettings.objects.create(event=event, **folders)
    RaceState.objects.create(event=event)
    TimerStatus.objects.create(event=event)
    ReplayStatus.objects.create(event=event)
    _create_legacy_scenes(event)


def _create_legacy_scenes(event):
    for sort_order, (scene_name, *pages) in enumerate(LEGACY_SCENES, start=1):
        scene = Scene.objects.create(
            event=event, name=scene_name, sort_order=sort_order
        )
        SceneKiosk.objects.bulk_create(
            SceneKiosk(event=event, scene=scene, kiosk_name=kiosk_name, page=page)
            for kiosk_name, page in zip(KIOSK_NAMES, pages, strict=True)
            if page
        )


@transaction.atomic
def new_event(name):
    """Create an event with its per-event rows and the six legacy scenes.

    The first event becomes the active one.
    """
    event = Event.objects.create(name=name, slug=_unique_slug(name))
    root = event_dir(event.slug)
    _create_event_rows(
        event,
        {
            "photo_directory": str(root / "racers"),
            "car_photo_directory": str(root / "cars"),
            "video_directory": str(root / "videos"),
            "logs_directory": str(root / "logs"),
        },
    )
    SiteState.objects.get_or_create(pk=1, defaults={"active_event": event})
    return event


# The settings the `schema` script of `database.execute` keeps
# (`RACE_INFO_KEYS_FOR_SCHEMA_SCRIPT`): the four folders, the lane count, and
# the websocket relay.
SCHEMA_KEPT_SETTINGS = (
    "photo_directory",
    "car_photo_directory",
    "video_directory",
    "logs_directory",
    "lane_count",
    "websocket_url",
    "ws_trigger_port",
)


def _deletion_order():
    """The event-scoped models to empty, each before the models it points at.

    A foreign key is `PROTECT` in nearly every case, so a row cannot go before
    the rows that refer to it. The per-event rows are not emptied: they are put
    back to their defaults first, which clears their references.
    """
    emptied = [
        model for model in event_scoped_models() if not issubclass(model, PerEvent)
    ]
    graph = {model: set() for model in emptied}
    for model in emptied:
        for field in model._meta.concrete_fields:
            target = field.related_model
            if (
                field.is_relation
                and target in graph
                and target is not model
                and field.remote_field.on_delete is models.PROTECT
            ):
                # `model` must go before `target`.
                graph[target].add(model)
    return list(TopologicalSorter(graph).static_order())


def _clear_self_references(model, event):
    for field in model._meta.concrete_fields:
        if field.is_relation and field.related_model is model and field.null:
            model.objects.filter(event=event).update(**{field.name: None})


def _reset_to_defaults(event, keep=()):
    """Put the event's per-event rows back to their field defaults, in place,
    so the `RaceState` row that `lock_race_state` holds stays the same row."""
    for model in event_scoped_models():
        if not issubclass(model, PerEvent):
            continue
        row = model.objects.get(event=event)
        for field in model._meta.concrete_fields:
            if field.primary_key or field.name == "event" or field.name in keep:
                continue
            setattr(row, field.attname, field.get_default())
        row.save()


def reset_event(event):
    """Empty the event's tables and put its per-event rows back to their
    defaults, as the `schema` script of `database.execute` does (#63).

    The folder, lane count, and websocket settings stay. The action log goes,
    as PHP's `schema` script drops its `ActionHistory` table. Other events are
    untouched. The fake racers' images go
    from both photo folders, which PHP means to do and skips (C28). Returns the
    number of rows deleted.
    """
    settings_row = RaceSettings.objects.get(event=event)
    folders = (settings_row.photo_directory, settings_row.car_photo_directory)
    deleted = _reset_rows(event)
    # After the commit, so a rolled-back reset leaves the files.
    transaction.on_commit(lambda: [remove_fake_images(f) for f in folders])
    return deleted


@transaction.atomic
def _reset_rows(event):
    lock_race_state(event.pk)
    # The per-event rows refer to other rows, so they are cleared first.
    _reset_to_defaults(event, keep=SCHEMA_KEPT_SETTINGS)
    deleted = 0
    for model in _deletion_order():
        _clear_self_references(model, event)
        deleted += model.objects.filter(event=event).delete()[0]
    _create_legacy_scenes(event)
    return deleted


def lock_race_state(event_id):
    """Lock the event's `RaceState` row and return it.

    Call it first inside `transaction.atomic()`. Every writer of the roster,
    inspection status, lane settings, or results takes this lock, because
    `generate_schedule` rechecks those inputs under it (#20, "Transactions
    and locks").
    """
    return RaceState.objects.select_for_update().get(event_id=event_id)
