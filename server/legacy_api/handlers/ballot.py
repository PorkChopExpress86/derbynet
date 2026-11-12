"""The ballot endpoints (#54): `ballot.open`, `query=ballot.get`,
`vote.cast` and `query=ballot.results`, over `awards.balloting`.

`ballot.get` and `vote.cast` need no login, as PHP's do (neither
`query.ballot.get.inc` nor `action.vote.cast.inc` checks a permission): a
voter's credential is a racer's voting token (#206, D86). The vote link
(`ui.vote.views.token_link`) puts the token in the session, and so does
`ballot.get` given `voting_token=<GUID>`; `vote.cast` and later
`ballot.get` calls in the same session use it. A call whose session holds no
valid token of the active event is refused with `no-token`, and a refused
token leaves the session. `ballot.get` adds the voter's row on a GET
(`voterid.inc:18` to `:23`).
"""

from accounts.permissions import Access, Perm
from awards import balloting
from awards.balloting import BallotFailure
from awards.models import Award
from legacy_api.registry import LegacyContext, action, query

# The session key that holds the voting token. PHP's flag is
# `$_SESSION['ok-to-vote']`, set by the ballot password; its voter is the PHP
# session id.
TOKEN = balloting.SESSION_TOKEN
# The `ballot.get` parameter that names a token.
TOKEN_PARAM = "voting_token"

S15 = (
    "S15: the voter is the racer whose voting token the session holds (D86),"
    " and a ballot_code or voter id the caller sends is ignored; PHP names"
    " the voter by the session id or by any ballot_code."
)
D86 = (
    "D86: refused with no-token while the session holds no voting token of"
    " a racer of the active event (voting_token on ballot.get puts one"
    " there); the ballot password gates nothing. PHP lets in whoever gives"
    " the ballot password."
)


def _fail(ctx: LegacyContext, failure: BallotFailure) -> None:
    ctx.failure(failure.code, failure.description)


def _refuse(ctx: LegacyContext, failure: BallotFailure) -> None:
    """A refusal; a refused token leaves the session, so the next call
    does not try it again."""
    if failure is balloting.NO_TOKEN:
        ctx.session.pop(TOKEN, None)
    _fail(ctx, failure)


@action(
    "ballot.open",
    permission=Perm.JUDGING,
    divergences=(
        "C55: a state other than open or closed answers invalid-state and"
        " changes nothing; PHP stores the text as given.",
    ),
)
def ballot_open(ctx: LegacyContext) -> None:
    try:
        balloting.set_open(ctx.event, ctx.args.get("state"))
    except BallotFailure as failure:
        _fail(ctx, failure)
        return
    ctx.success()


@query(
    "ballot.get",
    permission=Access.ANONYMOUS,
    session_write=True,
    get_writes=True,
    divergences=(S15, D86),
)
def ballot_get(ctx: LegacyContext) -> None:
    if TOKEN_PARAM in ctx.args:
        given = ctx.args.get(TOKEN_PARAM)
        ctx.session[TOKEN] = given if isinstance(given, str) else ""
    try:
        found = balloting.ballot(ctx.event, ctx.session.get(TOKEN), ctx.remote_addr)
    except BallotFailure as failure:
        _refuse(ctx, failure)
        return
    ctx.put(
        "ballot",
        {
            str(award_id): {"max_votes": entry.max_votes, "votes": entry.votes}
            for award_id, entry in found.items()
        },
    )


@action(
    "vote.cast",
    permission=Access.ANONYMOUS,
    session_write=True,
    divergences=(
        S15,
        D86 + " The check comes after PHP's closed, no-award and too-many checks.",
        "S14: refused with ineligible for a racer who has not passed"
        " check-in, is not eligible for any award, cannot win the award"
        " (D33, D35), or is named twice, and for votes that are not a JSON"
        " list of racer ids; PHP stores any racer id it is sent.",
    ),
)
def vote_cast(ctx: LegacyContext) -> None:
    award = ctx.resolve(Award, ctx.args.get("awardid"))
    try:
        summary = balloting.cast(
            ctx.event,
            ctx.session.get(TOKEN),
            award.pk if award is not None else None,
            ctx.args.get("votes"),
            ip_address=ctx.remote_addr,
        )
    except BallotFailure as failure:
        _refuse(ctx, failure)
        return
    ctx.put("votes", summary)
    ctx.success()


@query(
    "ballot.results",
    permission=Perm.JUDGING,
    divergences=(
        "S16: needs JUDGING; PHP answers anyone with every tally and the"
        " racers' names.",
    ),
)
def ballot_results(ctx: LegacyContext) -> None:
    ctx.put(
        "awards",
        [
            {
                "awardid": tally.award_id,
                "awardname": tally.award_name,
                "recipient": tally.recipient,
                "candidates": [
                    {
                        "score": c.score,
                        "carnumber": c.car_number,
                        "firstname": c.first_name,
                        "lastname": c.last_name,
                    }
                    for c in tally.candidates
                ],
            }
            for tally in balloting.tallies(ctx.event)
        ],
    )
