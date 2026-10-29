"""Starting an event and browsing the data folder from the setup page (#36).

`setup.nodata` runs with no event open. A server with no event lets anyone
set up (the first run); after that it needs `SET_UP`, and not when
`DERBYNET_LOCK_SETTINGS` is set. PHP lists and stats any path on the server
for anyone (S25). Here `SET_UP` is required and paths stay under the data
folder.
"""

from django.conf import settings

from accounts.permissions import Perm, log_in
from core import setup_details, setup_event
from legacy_api.registry import LegacyContext, action, query

S25 = (
    "S25: needs SET_UP and stays under the data folder; PHP serves any path to anyone."
)


def _text(ctx: LegacyContext, key: str) -> str | None:
    value = ctx.args.get(key)
    return value if isinstance(value, str) else None


@action("setup.nodata", permission=Perm.SET_UP, session_write=True)
def setup_nodata(ctx: LegacyContext) -> None:
    if settings.LOCK_SETTINGS:
        ctx.failure("locked", "Settings are locked and cannot be changed.")
        return
    new, old = _text(ctx, "ez-new"), _text(ctx, "ez-old")
    try:
        if new:
            event = setup_event.start_event(new)
        elif old:
            event = setup_event.open_existing(old)
        else:
            raise setup_event.SetupFailure(
                "connection-string",
                "The database comes from the server's settings, not from here.",
            )
    except setup_event.SetupFailure as failure:
        ctx.failure(failure.code, failure.description)
        return
    ctx.put("executed", [])
    ctx.success()
    ctx.put("details", setup_details.build(event))
    # Setup rights were granted for want of an event. Now log in, without a
    # password, as the role the setup page names.
    role = setup_event.post_setup_role()
    if role is not None:
        log_in(ctx.session, role)


@query("file.directory.nodata", permission=Perm.SET_UP, divergences=(S25,))
def file_directory(ctx: LegacyContext) -> None:
    path = _text(ctx, "path")
    if path is None:
        ctx.failure("nopath", "No PATH query argument provided")
        return
    listed = setup_event.listing(path)
    failures = listed.pop("failures")
    for key, value in listed.items():
        ctx.put(key, value)
    if failures:
        ctx.failure(*failures[0])


@query("file.stat", permission=Perm.SET_UP, divergences=(S25,))
def file_stat(ctx: LegacyContext) -> None:
    path = _text(ctx, "path")
    if path is None:
        ctx.failure("nopath", "No PATH query argument provided")
        return
    found = setup_event.stat(path)
    if found is None:
        ctx.failure("outside", f"{path} is outside the data folder")
    else:
        ctx.put("stat", found)


@action("file.directory.new.nodata", permission=Perm.SET_UP, divergences=(S25,))
def file_directory_new(ctx: LegacyContext) -> None:
    directory = _text(ctx, "directory") or ""
    if setup_event.make_folder(directory):
        ctx.put("path", directory)
        ctx.success()
    else:
        ctx.failure("mkdir", f"Unable to create directory {directory}")
