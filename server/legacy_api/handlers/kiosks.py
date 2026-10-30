"""The kiosk endpoints (#34): `query=poll.kiosk`, `query=poll.kiosk.all`,
`kiosk.assign` and `kiosk.reload`. Thin adapters over `kiosks.services`
and `kiosks.polls`.
"""

from django.utils import timezone

from accounts.permissions import Access, Perm
from kiosks import polls, services
from kiosks.models import Scene
from legacy_api.registry import LegacyContext, action, query

DASHBOARD_DIVERGENCES = (
    "C19: current-scene is the scene applied last; PHP reads a key nothing"
    ' writes and always shows "".',
    "C20: each kiosk's parameters are its own; PHP's are always null.",
)


def _text(ctx: LegacyContext, name: str) -> str | None:
    value = ctx.args.get(name)
    return value if isinstance(value, str) else None


def _put_all(ctx: LegacyContext, keys: dict) -> None:
    for key, value in keys.items():
        ctx.put(key, value)


@query("poll.kiosk", permission=Access.ANONYMOUS, get_writes=True)
def poll_kiosk(ctx: LegacyContext) -> None:
    """Every kiosk polls every 5 s, anonymously (D5). The poll adds an
    unknown address, records the contact, consumes a pending reload, and
    shows a playlist scene that has come due."""
    ctx.put("kiosk-setting", polls.poll_kiosk(ctx.event, _text(ctx, "address")))


@query(
    "poll.kiosk.all",
    permission=Perm.PRESENT_AWARDS,
    get_writes=True,
    divergences=(
        "S27: needs PRESENT_AWARDS, as kiosk-dashboard.php does; PHP lists"
        " every kiosk address to anyone.",
        *DASHBOARD_DIVERGENCES,
    ),
)
def poll_kiosk_all(ctx: LegacyContext) -> None:
    """The kiosk dashboard's poll. It drops kiosks silent for an hour and
    shows a playlist scene that has come due."""
    _put_all(ctx, polls.poll_dashboard(ctx.event))


@action("kiosk.assign", permission=Perm.CONTROL_RACE, divergences=DASHBOARD_DIVERGENCES)
def kiosk_assign(ctx: LegacyContext) -> None:
    """`address` names the kiosk; `page`, `params` and `name` change it, and
    `all` gives every kiosk one page."""
    services.assign(
        ctx.event,
        _text(ctx, "address") or "",
        page=_text(ctx, "page"),
        params=_text(ctx, "params"),
        name=_text(ctx, "name"),
        every_page=_text(ctx, "all"),
    )
    ctx.success()
    _put_all(ctx, services.dashboard(ctx.event, timezone.now()))


@action(
    "kiosk.reload",
    permission=Perm.CONTROL_RACE,
    divergences=("C18: answers success; PHP never sets an outcome (in-progress).",),
)
def kiosk_reload(ctx: LegacyContext) -> None:
    services.request_reload(ctx.event, _text(ctx, "address") or "")
    ctx.success()


# ----- scenes --------------------------------------------------------------------


@query(
    "scene.list",
    permission=Access.LOGGED_IN,
    divergences=("D5: needs a login; PHP lists the scenes to anyone.",),
)
def scene_list(ctx: LegacyContext) -> None:
    ctx.put("scenes", services.scene_list(ctx.event))
    ctx.success()


@action("scene.add", permission=Perm.SET_UP)
def scene_add(ctx: LegacyContext) -> None:
    name = _text(ctx, "name") or ""
    try:
        scene = services.add_scene(ctx.event, name)
    except services.DuplicateSceneName:
        ctx.failure("duplicate-name", f"Scene {name} already exists.")
        return
    ctx.put("scene-id", scene.pk)
    ctx.success()


@action("scene.delete", permission=Perm.SET_UP)
def scene_delete(ctx: LegacyContext) -> None:
    """A playlist step, the racing scene or the current scene that named the
    scene name none afterwards; PHP leaves them naming a deleted scene."""
    services.delete_scene(ctx.event, ctx.resolve(Scene, ctx.args.get("sceneid")))
    ctx.success()


@action("scene.setkiosk", permission=Perm.SET_UP)
def scene_setkiosk(ctx: LegacyContext) -> None:
    """`page` with optional `params` sets the page kiosks named `kiosk_name`
    show in the scene; no page takes the name out. A scene of another event,
    or none, changes nothing."""
    services.set_scene_kiosk(
        ctx.resolve(Scene, ctx.args.get("sceneid")),
        _text(ctx, "kiosk_name") or "",
        _text(ctx, "page") or "",
        _text(ctx, "params"),
    )
    ctx.put("all-scenes", services.all_scenes(ctx.event))
    ctx.success()


@action("scene.apply", permission=Perm.CONTROL_RACE, divergences=DASHBOARD_DIVERGENCES)
def scene_apply(ctx: LegacyContext) -> None:
    """`sceneid`, or `name`. A negative id, or an id or name that names no
    scene of the event, leaves no scene current."""
    if "sceneid" in ctx.args:
        scene = ctx.resolve(Scene, ctx.args["sceneid"])
    elif "name" in ctx.args:
        scene = services.scene_named(ctx.event, _text(ctx, "name"))
    else:
        ctx.failure("no-scene", "No scene specified")
        return
    services.choose_scene(ctx.event, scene)
    ctx.success()
    _put_all(ctx, services.dashboard(ctx.event, timezone.now()))
