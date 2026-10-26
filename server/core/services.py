from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from core.models import Event, RaceSettings, RaceState, SiteState
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


@transaction.atomic
def new_event(name):
    """Create an event with its per-event rows and the six legacy scenes.

    The first event becomes the active one.
    """
    event = Event.objects.create(name=name, slug=_unique_slug(name))
    root = event_dir(event.slug)
    RaceSettings.objects.create(
        event=event,
        photo_directory=str(root / "racers"),
        car_photo_directory=str(root / "cars"),
        video_directory=str(root / "videos"),
        logs_directory=str(root / "logs"),
    )
    RaceState.objects.create(event=event)
    TimerStatus.objects.create(event=event)
    ReplayStatus.objects.create(event=event)

    for sort_order, (scene_name, *pages) in enumerate(LEGACY_SCENES, start=1):
        scene = Scene.objects.create(
            event=event, name=scene_name, sort_order=sort_order
        )
        SceneKiosk.objects.bulk_create(
            SceneKiosk(event=event, scene=scene, kiosk_name=kiosk_name, page=page)
            for kiosk_name, page in zip(KIOSK_NAMES, pages, strict=True)
            if page
        )

    SiteState.objects.get_or_create(pk=1, defaults={"active_event": event})
    return event


def lock_race_state(event_id):
    """Lock the event's `RaceState` row and return it.

    Call it first inside `transaction.atomic()`. Every writer of the roster,
    inspection status, lane settings, or results takes this lock, because
    `generate_schedule` rechecks those inputs under it (#20, "Transactions
    and locks").
    """
    return RaceState.objects.select_for_update().get(event_id=event_id)
