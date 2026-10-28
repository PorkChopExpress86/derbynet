"""`database.execute`: the named scripts that reset or upgrade the database (#63).

The shell suites call it before each phase. It never runs caller-supplied SQL.
"""

from accounts.permissions import Perm
from core import database_scripts, setup_details
from legacy_api.registry import LegacyContext, action

SEPARATORS = ("/", "\\")


@action("database.execute", permission=Perm.SET_UP)
def database_execute(ctx: LegacyContext) -> None:
    script = ctx.args.get("script", "")
    script = script if isinstance(script, str) else ""
    if any(separator in script for separator in SEPARATORS):
        ctx.failure("insecure", "Only local SQL script execution is allowed.")
    else:
        result = database_scripts.run(ctx.event, script)
        ctx.put("executed", result.executed)
        if result.failure:
            ctx.failure(*result.failure)
        else:
            ctx.success()
    ctx.put("details", setup_details.build(ctx.event))
