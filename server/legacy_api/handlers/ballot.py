"""The ballot endpoints (#54): `ballot.open`, `query=ballot.get`,
`vote.cast` and `query=ballot.results`, over `awards.balloting`.

`ballot.get` and `vote.cast` answer anyone, as PHP's do (neither
`query.ballot.get.inc` nor `action.vote.cast.inc` checks a permission): a
voter's login is the ballot password (ADR-07, D5). `ballot.get` writes the
session, as PHP's does (`query.ballot.get.inc:16`), and adds the voter's row
on a GET (`voterid.inc:18` to `:23`).
"""

import secrets

from accounts.permissions import Access, Perm
from awards import balloting
from awards.balloting import BallotFailure
from awards.models import Award
from legacy_api.registry import LegacyContext, action, query

# The session keys. PHP's flag is `$_SESSION['ok-to-vote']`; its voter is the
# PHP session id.
OK_TO_VOTE = "ok-to-vote"
VOTER = "ballot-voter"

S15 = (
    "S15: the voter is a random identifier kept in the session, and a"
    " ballot_code the caller sends is ignored; PHP names the voter by the"
    " session id or by any ballot_code."
)


def _fail(ctx: LegacyContext, failure: BallotFailure) -> None:
    ctx.failure(failure.code, failure.description)


def _voter_identifier(ctx: LegacyContext) -> str:
    """The session's voter, made on its first ballot request."""
    found = ctx.session.get(VOTER)
    if not isinstance(found, str) or not found:
        found = secrets.token_hex(16)
        ctx.session[VOTER] = found
    return found


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
    divergences=(S15,),
)
def ballot_get(ctx: LegacyContext) -> None:
    given = ctx.args.get("password", "")
    if not isinstance(given, str):
        given = ""
    if not ctx.session.get(OK_TO_VOTE) and not balloting.password_matches(
        ctx.event, given
    ):
        _fail(ctx, balloting.PASSWORD)
        return
    ctx.session[OK_TO_VOTE] = 1
    voter_row = balloting.voter(ctx.event, _voter_identifier(ctx), ctx.remote_addr)
    ctx.put(
        "ballot",
        {
            str(award_id): {"max_votes": entry.max_votes, "votes": entry.votes}
            for award_id, entry in balloting.ballot(ctx.event, voter_row).items()
        },
    )


@action(
    "vote.cast",
    permission=Access.ANONYMOUS,
    session_write=True,
    divergences=(
        S15,
        "S14: refused with password, after PHP's closed, no-award and"
        " too-many checks, while a ballot password is set and this session"
        " has not given it; PHP never checks.",
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
            _voter_identifier(ctx),
            award.pk if award is not None else None,
            ctx.args.get("votes"),
            allowed=bool(ctx.session.get(OK_TO_VOTE)),
            ip_address=ctx.remote_addr,
        )
    except BallotFailure as failure:
        _fail(ctx, failure)
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
