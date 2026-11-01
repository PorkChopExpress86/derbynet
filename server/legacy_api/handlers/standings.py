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

# Each divergence is reproduced through `/action.php` in
# `legacy_api/tests/test_standings.py`: `test_q23_...`, `test_d33_...`,
# `test_d35_...`, `test_d37_...`, and for Q13 `test_a_standings_read_writes_
# nothing` and `test_q13_reading_the_standings_writes_no_scoring`.
DIVERGENCES = (
    "Q23: a racer with no score under time scoring takes no place; PHP throws"
    " a TypeError or files the racer under rung -1. deviations.json records the"
    " suite-points-racing exchanges, which racing/engine/tests/"
    "test_standings_parity.py checks.",
    "D37: a closed lane's results are dropped; PHP has no lane closures and"
    " keeps them.",
    "D33, D35: a racer not eligible for a ladder's speed awards takes no place"
    " in it; PHP leaves out only a racer excluded from every award.",
    "Q13: reading the standings writes nothing; PHP writes scoring=1 when the"
    " legacy drop-slowest key is set and scoring is not.",
)


@query("standings", permission=Access.ANONYMOUS, divergences=DIVERGENCES)
def standings(ctx: LegacyContext) -> None:
    ctx.put("standings", all_standings(ctx.event))
