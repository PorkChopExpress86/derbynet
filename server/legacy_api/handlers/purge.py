"""`database.purge`: clear results, schedules, racers or awards (#36)."""

from accounts.permissions import Perm
from core import setup_details
from legacy_api.registry import LegacyContext, action
from racing.services import purge


@action("database.purge", permission=Perm.SET_UP)
def database_purge(ctx: LegacyContext) -> None:
    what = ctx.args.get("purge")
    what = what if isinstance(what, str) else ""
    round_id = ctx.args.get("roundid")
    if purge.purge(ctx.event, what, round_id):
        ctx.success()
        ctx.put("details", setup_details.build(ctx.event))
    else:
        ctx.failure("not-recognized", f"Unrecognized purge operation: {what}")
