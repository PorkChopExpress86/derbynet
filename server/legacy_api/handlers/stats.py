"""`query=stats`: counts for the event, and its settings for `SET_UP` (S3).

PHP answers anonymously and returns every RaceInfo row, `ballot_password` and
`token-salt` included. Any login gets the counts (D16), and only a caller with
`SET_UP` gets `race_info`, through the settings listing that filters secrets.
"""

from django.utils import timezone

from accounts.permissions import Access, Perm, granted_for
from core import race_settings
from legacy_api.registry import LegacyContext, query
from racing.services import stats

DIVERGENCES = (
    "D16: a login is required, where PHP answers anyone (S3); race_info goes to SET_UP"
    " only, without the secrets (D16).",
)


@query("stats", permission=Access.LOGGED_IN, divergences=DIVERGENCES)
def stats_query(ctx: LegacyContext) -> None:
    now = timezone.localtime()
    ctx.put(
        "timestamp",
        {"ymd": now.strftime("%Y-%m-%d %H:%M:%S"), "time": int(now.timestamp())},
    )
    found = stats.counts(ctx.event)
    if granted_for(ctx.session) & Perm.SET_UP:
        ctx.put("race_info", race_settings.all_keys(ctx.event))
    for key, value in found.items():
        ctx.put(key, value)
    ctx.success()
