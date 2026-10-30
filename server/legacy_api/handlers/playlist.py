"""The playlist endpoints (#34): `playlist.add`, `playlist.edit`,
`playlist.delete`, `playlist.order` and `playlist.start`. Thin adapters over
`racing.services.playlist`.

`playlist.start` builds rounds through the playlist service, which never
puts a late racer into a chart (D32): only Add to schedule does. A final it
builds holds its qualifiers and the racers `racing.services.finals` allowed.
"""

from accounts.permissions import Perm
from kiosks.models import Scene
from legacy_api.registry import LegacyContext, action
from racing.engine.phpcompat import php_intval, php_truthy
from racing.models import PlaylistEntry
from racing.services import playlist
from racing.services.rounds import playlist_finalists
from roster.models import Group


def _text(ctx: LegacyContext, name: str) -> str | None:
    value = ctx.args.get(name)
    return value if isinstance(value, str) else None


def _fields(ctx: LegacyContext) -> playlist.StepFields:
    """A missing field reads as PHP's null does: no cut, no runs, no scene.
    A scene of another event, or -1, is no scene."""
    return playlist.StepFields(
        bucket_limit=max(php_intval(_text(ctx, "top") or ""), 0),
        bucketed=php_truthy(_text(ctx, "bucketed") or ""),
        n_times_per_lane=max(php_intval(_text(ctx, "n_times_per_lane") or ""), 0),
        scene_at_finish=ctx.resolve(Scene, ctx.args.get("sceneid_at_finish")),
        continue_racing=php_truthy(_text(ctx, "continue_racing") or ""),
    )


@action("playlist.add", permission=Perm.SET_UP)
def playlist_add(ctx: LegacyContext) -> None:
    """`classid` and `round` name the step's round; `top` and `bucketed` its
    cut, `n_times_per_lane` its runs, and `sceneid_at_finish` or
    `continue_racing` what follows it. A class of another event fails with
    no_classid; PHP stores the id as sent."""
    group = ctx.resolve(Group, ctx.args.get("classid"))
    round_text = _text(ctx, "round")
    if group is None:
        ctx.failure("no_classid", "No classid")
    if round_text is None:
        ctx.failure("no_round", "No round")
    if group is None or round_text is None:
        return
    step = playlist.add_step(ctx.event, group, php_intval(round_text), _fields(ctx))
    ctx.put("queue-entry", playlist.queue_entry(step))
    ctx.success()


@action("playlist.edit", permission=Perm.SET_UP)
def playlist_edit(ctx: LegacyContext) -> None:
    playlist.edit_step(
        ctx.resolve(PlaylistEntry, ctx.args.get("queueid")), _fields(ctx)
    )
    ctx.success()


@action("playlist.delete", permission=Perm.SET_UP)
def playlist_delete(ctx: LegacyContext) -> None:
    playlist.delete_step(ctx.resolve(PlaylistEntry, ctx.args.get("queueid")))
    ctx.success()


@action("playlist.order", permission=Perm.SET_UP)
def playlist_order(ctx: LegacyContext) -> None:
    """`queueid_<n>=<queueid>` puts the step at position n."""
    seqs = {}
    for key, value in ctx.args.items():
        seq = key.removeprefix("queueid_")
        if seq != key and seq.isdigit():
            step = ctx.resolve(PlaylistEntry, value)
            if step is not None:
                seqs[step] = int(seq)
    playlist.order_steps(ctx.event, seqs)
    ctx.success()


@action(
    "playlist.start",
    permission=Perm.CONTROL_RACE,
    divergences=("S18: needs CONTROL_RACE; PHP lets anyone start racing.",),
)
def playlist_start(ctx: LegacyContext) -> None:
    """Activate the first step whose round is not raced and turn racing on."""
    playlist.start_playlist(ctx.event, playlist_finalists())
    ctx.success()
