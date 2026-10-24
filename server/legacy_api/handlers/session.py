"""`session.write`, cut down to the one key a page sets (#15, S1).

PHP copies every `session_<key>` field into the session, so a caller could post
`session_permissions=-1`. Only the check-in page's barcode mode is accepted.
"""

from accounts.permissions import Perm
from legacy_api.registry import LegacyContext, action

WRITABLE = {
    "barcode-action": frozenset({"locate", "checkin", "racer-photo", "car-photo"}),
}


@action("session.write", permission=Perm.CHECK_IN_RACERS, session_write=True)
def session_write(ctx: LegacyContext) -> None:
    for key, allowed in WRITABLE.items():
        value = ctx.args.get(f"session_{key}")
        if isinstance(value, str) and value in allowed:
            ctx.session[key] = value
    # PHP echoes the whole session, which would show the role and its bits.
    ctx.put(
        "session", {key: ctx.session[key] for key in WRITABLE if key in ctx.session}
    )
    ctx.success()
