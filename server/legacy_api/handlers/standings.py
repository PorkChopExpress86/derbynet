"""`query=standings` (#37): every ladder of the standings, over
`racing.services.standings`.

It answers `{standings: {<stkey>: [[racerid, ...], ...]}}`, an empty object
when nothing has raced. A ladder's rung i holds the racers in place i + 1; a
tie shares one rung and leaves empty rungs after it. The keys are PHP's:
`st-r<roundid>`, `st-rr<roundid>-<rankid>`, `st-q<classid>` and `st-ff`.
It is public, like the standings page (D24).
"""

from accounts.permissions import Access
from legacy_api.registry import LegacyContext, query
from racing.services.standings import all_standings

DIVERGENCES = (
    "Q23: a racer with no score under time scoring takes no place; PHP throws"
    " a TypeError or files the racer under rung -1.",
    "D37: a closed lane's results are dropped, and points count a round's"
    " open lanes; PHP keeps them and counts lane-count.",
    "D33, D35: a racer not eligible for a ladder's speed awards takes no place"
    " in it; PHP leaves out only a racer excluded from every award.",
    "Q13: reading the standings writes nothing; PHP writes scoring=1 when the"
    " legacy drop-slowest key is set and scoring is not.",
)


@query("standings", permission=Access.ANONYMOUS, divergences=DIVERGENCES)
def standings(ctx: LegacyContext) -> None:
    ctx.put("standings", all_standings(ctx.event))
