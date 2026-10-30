"""Kiosks and scenes, ported from `website/inc/kiosks.inc` and
`website/inc/scenes.inc`.

Each function returns PHP's JSON shapes as plain data, and the legacy
handlers encode them. A kiosk is found by its address within the event.
"""

from datetime import datetime, timedelta

from django.db import models, transaction

from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from kiosks.madlib import madlib
from kiosks.models import Kiosk, Scene, SceneKiosk
from racing.models import PlaylistEntry

IDENTIFY = "kiosks/identify.kiosk"
NO_PARAMS = "{}"

# The pages `all_kiosk_pages()` lists, sorted as PHP's `sort()` sorts them.
# Django shows only the built-in pages (ADR-10), so no `local/kiosks` page.
BUILT_IN_PAGES = (
    "award-presentations",
    "derbynet",
    "flag",
    "identify",
    "now-racing",
    "now-racing-columnar",
    "now-racing-columnar-reversed",
    "ondeck",
    "please-check-in",
    "qrcode",
    "results-by-racer",
    "scale-MPH",
    "slideshow",
    "standings",
    "welcome",
)

LISTED_FOR = timedelta(seconds=60)
"""`all_kiosks()` lists a kiosk heard from in the last 60 s."""
DROPPED_AFTER = timedelta(seconds=3600)
"""`drop_old_kiosks()` deletes a kiosk silent for an hour."""


def apply_scene(state: RaceState, scene_id: int) -> None:
    """`apply_scene()` in `website/inc/scenes.inc`: make the scene current
    and give each kiosk the page the scene sets for its name. A kiosk name
    the scene leaves blank keeps its page. The caller saves `state`."""
    state.current_scene_id = scene_id
    for row in SceneKiosk.objects.filter(scene_id=scene_id).exclude(page=""):
        Kiosk.objects.filter(event_id=state.event_id, name=row.kiosk_name).update(
            page=row.page, params=row.params
        )


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def kiosk_setting(event, address: str, now: datetime) -> dict:
    """`kiosk_page()`: the page a kiosk shows. An unknown address is added
    with the identify page; each poll records the contact and consumes a
    pending reload. A kiosk with no page shows the identify page."""
    with transaction.atomic():
        kiosk, created = Kiosk.objects.select_for_update().get_or_create(
            event=event, address=address, defaults={"page": IDENTIFY}
        )
        reload = kiosk.reload_requested
        Kiosk.objects.filter(pk=kiosk.pk).update(
            last_contact=now, reload_requested=False
        )
    if created or not kiosk.page:
        return {"page": IDENTIFY, "parameters": NO_PARAMS, "reload": False}
    return {
        "page": kiosk.page,
        "name": kiosk.name,
        "reload": reload,
        "parameters": kiosk.params or NO_PARAMS,
    }


def _sort_key(kiosk: Kiosk):
    """PHP orders by `CASE name WHEN 'Main' THEN '' ELSE name END, address,
    last_contact`, where an unnamed kiosk's name is NULL and sorts first.
    Sorted here, in byte order, so Postgres collation plays no part."""
    rank = 0 if not kiosk.name else 1 if kiosk.name == "Main" else 2
    return (rank, kiosk.name, kiosk.address, kiosk.last_contact)


def all_kiosks(event, now: datetime) -> list[dict]:
    """`all_kiosks()`, which first runs `drop_old_kiosks()`. Each kiosk
    carries its own parameters, where PHP's are always null (C20)."""
    with transaction.atomic():
        Kiosk.objects.filter(event=event, last_contact__lt=now - DROPPED_AFTER).delete()
        listed = list(
            Kiosk.objects.filter(event=event, last_contact__gt=now - LISTED_FOR)
        )
    return [
        {
            "name": kiosk.name,
            "address": kiosk.address,
            "madlib": madlib(kiosk.address),
            "last_contact": _epoch(kiosk.last_contact),
            "page": kiosk.page,
            "reload": kiosk.reload_requested,
            "age": _epoch(now) - _epoch(kiosk.last_contact),
            "parameters": kiosk.params or NO_PARAMS,
        }
        for kiosk in sorted(listed, key=_sort_key)
    ]


def kiosk_pages() -> list[dict]:
    return [{"brief": page, "full": f"kiosks/{page}.kiosk"} for page in BUILT_IN_PAGES]


def dashboard(event, now: datetime) -> dict:
    """The reply of `poll.kiosk.all`, which `kiosk.assign` and `scene.apply`
    append too. `current-scene` is the scene applied last, where PHP reads
    a key nothing writes and always shows `""` (C19)."""
    scene = (
        RaceState.objects.filter(event=event)
        .values_list("current_scene_id", flat=True)
        .get()
    )
    return {
        "current-scene": "" if scene is None else scene,
        "kiosks": all_kiosks(event, now),
        "kiosk-pages": kiosk_pages(),
    }


def assign(
    event,
    address: str,
    *,
    page: str | None = None,
    params: str | None = None,
    name: str | None = None,
    every_page: str | None = None,
) -> None:
    """`kiosk.assign`. A page or parameters set by hand mean no scene is
    current. A new name takes the current scene's page for that name. A
    kiosk with that address that does not exist is left alone, as PHP's
    UPDATE does."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        kiosks = Kiosk.objects.filter(event=event, address=address)
        if page is not None or params is not None:
            if page is None:
                page = kiosks.values_list("page", flat=True).first() or ""
            kiosks.update(page=page, params=params or "")
            state.current_scene = None
            state.save(update_fields=["current_scene"])
        if name is not None:
            kiosks.update(name=name)
            row = SceneKiosk.objects.filter(
                scene_id=state.current_scene_id, kiosk_name=name
            ).first()
            if row is not None and row.page:
                kiosks.update(page=row.page, params=row.params)
        if every_page is not None:
            page, _, params = every_page.partition("#")
            Kiosk.objects.filter(event=event).update(page=page, params=params)


def request_reload(event, address: str) -> None:
    """`mark_kiosk_for_reload()`: the kiosk reloads at its next poll."""
    Kiosk.objects.filter(event=event, address=address).update(reload_requested=True)


# ----- scenes ------------------------------------------------------------------


class DuplicateSceneName(Exception):
    pass


def _main_first(name: str) -> str:
    """PHP's `CASE kiosk_name WHEN 'Main' THEN '' ELSE kiosk_name END`."""
    return "" if name == "Main" else name


def _scene_kiosk(row: SceneKiosk) -> dict:
    return {
        "kiosk_name": row.kiosk_name,
        "page": row.page,
        "parameters": row.params or NO_PARAMS,
    }


def all_scenes(event) -> list[dict]:
    """`all_scenes()`: every scene, those with no kiosk too, its kiosks with
    Main first. Sorted here, so Postgres collation plays no part."""
    rows = list(SceneKiosk.objects.filter(event=event))
    out = []
    for scene in Scene.objects.filter(event=event).order_by("sort_order", "pk"):
        kiosks = sorted(
            (r for r in rows if r.scene_id == scene.pk),
            key=lambda r: _main_first(r.kiosk_name),
        )
        out.append(
            {
                "sceneid": scene.pk,
                "name": scene.name,
                "kiosks": [_scene_kiosk(r) for r in kiosks],
            }
        )
    return out


def scene_list(event) -> list[dict]:
    """`query=scene.list`: only scenes with a kiosk, as its INNER JOIN, and
    their kiosks in plain name order."""
    out = []
    for scene in all_scenes(event):
        kiosks = sorted(scene["kiosks"], key=lambda k: k["kiosk_name"])
        if kiosks:
            out.append({"name": scene["name"], "kiosks": kiosks})
    return out


def add_scene(event, name: str) -> Scene:
    """`scene.add`: a new scene after the others. Raises
    `DuplicateSceneName` when the event has one by that name."""
    with transaction.atomic():
        lock_race_state(event.id)
        scenes = Scene.objects.filter(event=event)
        if scenes.filter(name=name).exists():
            raise DuplicateSceneName(name)
        last = scenes.aggregate(last=models.Max("sort_order"))["last"] or 0
        return Scene.objects.create(event=event, name=name, sort_order=last + 1)


def delete_scene(event, scene: Scene | None) -> None:
    """`scene.delete`, its kiosk pages with it. A playlist step, the racing
    scene and the race state that name it name no scene afterwards."""
    if scene is None:
        return
    with transaction.atomic():
        state = lock_race_state(event.id)
        PlaylistEntry.objects.filter(event=event, scene_at_finish=scene).update(
            scene_at_finish=None
        )
        RaceSettings.objects.filter(event=event, racing_scene=scene).update(
            racing_scene=None
        )
        for field in ("current_scene", "playlist_scene"):
            if getattr(state, f"{field}_id") == scene.pk:
                setattr(state, field, None)
                state.save(update_fields=[field])
        scene.delete()


def set_scene_kiosk(
    scene: Scene | None, kiosk_name: str, page: str, params: str | None
) -> None:
    """`scene.setkiosk`: the page kiosks named `kiosk_name` show in the scene.
    No page takes the name out of the scene."""
    if scene is None:
        return
    rows = SceneKiosk.objects.filter(scene=scene, kiosk_name=kiosk_name)
    if not page:
        rows.delete()
        return
    SceneKiosk.objects.update_or_create(
        event_id=scene.event_id,
        scene=scene,
        kiosk_name=kiosk_name,
        defaults={"page": page, "params": params or ""},
    )


def scene_named(event, name: str | None) -> Scene | None:
    return Scene.objects.filter(event=event, name=name).first()


def choose_scene(event, scene: Scene | None) -> None:
    """`scene.apply`. None, for a negative or unknown id, means no scene is
    current, and no kiosk changes."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        if scene is None:
            state.current_scene = None
        else:
            apply_scene(state, scene.pk)
        state.save(update_fields=["current_scene"])
