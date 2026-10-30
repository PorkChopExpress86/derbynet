"""The kiosk endpoints (#34): `query=poll.kiosk`, `query=poll.kiosk.all`,
`kiosk.assign` and `kiosk.reload`. Thin adapters over `kiosks.services`
and `kiosks.polls`.
"""

from django.utils import timezone

from accounts.permissions import Access, Perm
from kiosks import polls, services
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
