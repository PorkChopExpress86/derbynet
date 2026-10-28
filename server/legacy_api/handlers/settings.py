"""`settings.list` and `settings.write`: the settings under their RaceInfo names (#63).

PHP answers it anonymously and lists every row, `ballot_password` and
`token-salt` included (S2). Only a caller with `SET_UP` sees any key.
"""

from accounts.permissions import Access, Perm, granted_for
from core import race_settings, settings_form
from legacy_api.registry import LegacyContext, action, query


@query("settings.list", permission=Access.ANONYMOUS)
def settings_list(ctx: LegacyContext) -> None:
    # PHP reports the database it connected to, which clients ignore.
    found: dict = {}
    if granted_for(ctx.session) & Perm.SET_UP:
        key = ctx.args.get("key")
        if isinstance(key, str):
            found = race_settings.read_many(ctx.event, key.split(","))
        else:
            found = race_settings.all_keys(ctx.event)
    ctx.put("settings", {"db-connection": "", **found})


@action("settings.write", permission=Perm.SET_UP)
def settings_write(ctx: LegacyContext) -> None:
    result = settings_form.apply_form(ctx.event, ctx.args)
    ctx.put("written", result.written)
    if result.failure:
        ctx.failure(*result.failure)
    else:
        ctx.success()
