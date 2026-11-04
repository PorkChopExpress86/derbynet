"""Kiosks and scenes, ported from `website/inc/kiosks.inc` and
`website/inc/scenes.inc`.

Each function returns PHP's JSON shapes as plain data, and the legacy
handlers encode them. A kiosk is found by its address within the event.
"""

from datetime import datetime, timedelta

from django.db import models, transaction

from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from kiosks import pages, parameters
from kiosks.madlib import madlib
from kiosks.models import Kiosk, Scene, SceneKiosk
from racing.models import PlaylistEntry

IDENTIFY = pages.IDENTIFY
NO_PARAMS = pages.NO_PARAMS

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
    return pages.setting(
        kiosk.page,
        kiosk.params,
        name=None if created else kiosk.name,
        reload=reload,
    )


def _sort_key(kiosk: Kiosk):
    """PHP orders by `CASE name WHEN 'Main' THEN '' ELSE name END, address,
    last_contact`, where an unnamed kiosk's name is NULL and sorts first.
    Sorted here, in byte order, so Postgres collation plays no part."""
    rank = 0 if not kiosk.name else 1 if kiosk.name == "Main" else 2
    return (rank, kiosk.name, kiosk.address, kiosk.last_contact)


def listed_kiosks(event, now: datetime) -> list[Kiosk]:
    """The kiosks heard from in the last 60 s, in `all_kiosks()` order. Reads
    only: the kiosk dashboard page polls it (#46)."""
    listed = Kiosk.objects.filter(event=event, last_contact__gt=now - LISTED_FOR)
    return sorted(listed, key=_sort_key)


def all_kiosks(event, now: datetime) -> list[dict]:
    """`all_kiosks()`, which first runs `drop_old_kiosks()`. Each kiosk
    carries its own parameters, where PHP's are always null (C20)."""
    with transaction.atomic():
        Kiosk.objects.filter(event=event, last_contact__lt=now - DROPPED_AFTER).delete()
        listed = listed_kiosks(event, now)
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
        for kiosk in listed
    ]


def kiosk_pages() -> list[dict]:
    return [{"brief": page.name, "full": page.key} for page in pages.PAGES.values()]


def is_built_in(page: str) -> bool:
    """A page a kiosk can be given from the pickers: a built-in page, never a
    `local/kiosks` one (ADR-10, D10)."""
    return pages.is_built_in(page)


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


# ----- the kiosk dashboard and scene editor pages (#46) ------------------------
#
# Each change the pages make is one call here, under the `RaceState` lock, and
# checks again what the page showed: the kiosk's row still exists, the scene
# still exists, the kiosk still shows the page its Configure sheet was for. A
# kiosk silent past the 60 s list window still takes a change, as PHP's
# `kiosk.assign` and `kiosk.reload` UPDATE its row by address; only one that
# is gone, dropped after an hour or never seen, is refused.

NAME_LIMIT = 200


class KioskFailure(Exception):
    def __init__(self, description: str):
        super().__init__(description)
        self.description = description


KIOSK_GONE = "That kiosk is gone. Reload the page."
SCENE_GONE = "That scene is gone. Reload the page."


def _known_kiosk(event, address: str) -> Kiosk:
    kiosk = Kiosk.objects.filter(event=event, address=address).first()
    if kiosk is None:
        raise KioskFailure(KIOSK_GONE)
    return kiosk


def _known_scene(event, scene_id: int) -> Scene:
    scene = Scene.objects.filter(event=event, pk=scene_id).first()
    if scene is None:
        raise KioskFailure(SCENE_GONE)
    return scene


def _name(name: str, what: str) -> str:
    name = name.strip()
    if len(name) > NAME_LIMIT:
        raise KioskFailure(f"That {what} is too long.")
    return name


def _built_in(page: str) -> str:
    if not is_built_in(page):
        raise KioskFailure("Pick a page from the list.")
    return page


def scene_kiosk_names(event) -> list[str]:
    """`all_scene_kiosk_names()`: every kiosk name any scene sets a page for,
    Main first. Sorted here, so Postgres collation plays no part."""
    names = set(
        SceneKiosk.objects.filter(event=event).values_list("kiosk_name", flat=True)
    )
    return sorted(names, key=_main_first)


def show_scene(event, scene_id: int) -> Scene:
    """The dashboard's scene switcher: `scene.apply` for a scene of the
    event. A scene deleted since the page loaded is refused, where
    `scene.apply` would leave no scene current."""
    with transaction.atomic():
        state = lock_race_state(event.id)
        scene = _known_scene(event, scene_id)
        apply_scene(state, scene.pk)
        state.save(update_fields=["current_scene"])
    return scene


def set_kiosk_page(event, address: str, page: str) -> None:
    """The page picker: `kiosk.assign` with `page`, which drops the kiosk's
    parameters and leaves no scene current."""
    with transaction.atomic():
        lock_race_state(event.id)
        _known_kiosk(event, address)
        assign(event, address, page=_built_in(page))


def identify_kiosk(event, address: str) -> None:
    """Identify: the kiosk shows its name and address in large type."""
    set_kiosk_page(event, address, IDENTIFY)


def name_kiosk(event, address: str, name: str) -> None:
    """`kiosk.assign` with `name`. A name the current scene sets a page for
    takes that page; an empty name leaves the kiosk unnamed."""
    with transaction.atomic():
        lock_race_state(event.id)
        _known_kiosk(event, address)
        assign(event, address, name=_name(name, "name"))


def configure_kiosk(event, address: str, page: str, posted: parameters.Posted):
    """A Configure sheet: `kiosk.assign` with `params`. `page` is the page the
    sheet was for; a kiosk given another page since is refused."""
    with transaction.atomic():
        lock_race_state(event.id)
        if _known_kiosk(event, address).page != page:
            raise KioskFailure(
                "That kiosk shows another page now. Open Configure again."
            )
        try:
            text = parameters.encode(event, page, posted)
        except parameters.ParameterRefused as refused:
            raise KioskFailure(refused.description) from None
        assign(event, address, params=text)


def reload_kiosk(event, address: str) -> None:
    """`kiosk.reload` for a listed kiosk."""
    with transaction.atomic():
        lock_race_state(event.id)
        _known_kiosk(event, address)
        request_reload(event, address)


def create_scene(event, name: str) -> Scene:
    """The scene editor's New scene: `scene.add`, refusing a blank name, and
    a name another scene has in any case, as `scenes.js` does."""
    name = _name(name, "scene name")
    if not name:
        raise KioskFailure("Name the scene.")
    with transaction.atomic():
        lock_race_state(event.id)
        if Scene.objects.filter(event=event, name__iexact=name).exists():
            raise KioskFailure(f"There is a scene called {name} already.")
        return add_scene(event, name)


def remove_scene(event, scene_id: int) -> bool:
    """`scene.delete`. False when the scene was gone already, so a second
    tap changes nothing."""
    with transaction.atomic():
        lock_race_state(event.id)
        scene = Scene.objects.filter(event=event, pk=scene_id).first()
        if scene is None:
            return False
        delete_scene(event, scene)
    return True


def set_scene_page(
    event, scene_id: int, kiosk_name: str, page: str, *, new_name: bool = False
) -> None:
    """`scene.setkiosk` from the scene editor: the page kiosks named
    `kiosk_name` show in the scene, or none, which takes the name out. The
    page starts with no parameters, the same page again too, as `scenes.js`
    `on_change_assigned_page` posts no `params`. With `new_name`, a name some
    scene already has, in any case, is refused, as "Add Kiosk Name" refuses
    it."""
    kiosk_name = _name(kiosk_name, "kiosk name")
    if not kiosk_name:
        raise KioskFailure("Name the kiosk.")
    with transaction.atomic():
        lock_race_state(event.id)
        scene = _known_scene(event, scene_id)
        if (
            new_name
            and SceneKiosk.objects.filter(
                event=event, kiosk_name__iexact=kiosk_name
            ).exists()
        ):
            raise KioskFailure(f"There is a kiosk called {kiosk_name} already.")
        if not page:
            set_scene_kiosk(scene, kiosk_name, "", None)
            return
        set_scene_kiosk(scene, kiosk_name, _built_in(page), None)


def configure_scene_kiosk(
    event, scene_id: int, kiosk_name: str, page: str, posted: parameters.Posted
) -> None:
    """A Configure sheet in the scene editor: `scene.setkiosk` with
    `params`. The scene must still give the name `page`."""
    with transaction.atomic():
        lock_race_state(event.id)
        scene = _known_scene(event, scene_id)
        row = SceneKiosk.objects.filter(scene=scene, kiosk_name=kiosk_name).first()
        if row is None or row.page != page:
            raise KioskFailure(
                "The scene shows another page there now. Open Configure again."
            )
        try:
            text = parameters.encode(event, page, posted)
        except parameters.ParameterRefused as refused:
            raise KioskFailure(refused.description) from None
        set_scene_kiosk(scene, kiosk_name, page, text)
