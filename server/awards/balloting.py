"""Online balloting (#54): the ballot behind `vote.php` and the ballot
endpoints, ported from `website/inc/voterid.inc`,
`website/ajax/query.ballot.get.inc`, `website/ajax/action.vote.cast.inc`,
`website/ajax/query.ballot.results.inc` and
`website/ajax/action.ballot.open.inc`.

There is one ballot, PHP's `BALLOTID` 1 (`voterid.inc:6`): every award with
a `BallotAward` row is on it, `depth` votes deep. A voter is a `Voter` row
named by its identifier. PHP names a voter by its PHP session id
(`query.ballot.get.inc:17`), or by any `ballot_code` the caller sends
(S15), and lets in whoever gives the ballot password
(`query.ballot.get.inc:13`).

Here the voter is a racer's voting token (#206, D86): whoever holds the
token votes, and every phone that holds it edits the same one ballot. The
`Voter` row is named by a digest of the token (`voter_identifier()`), so a
snapshot, which exports `Voters.identifier`, carries no token that votes.
A token is good while its racer is in the event; one that is malformed,
unknown, from another event or of a deleted racer is refused alike. The
ballot password gates nothing.

`cast()` adds the checks PHP leaves out (S14): a racer who has not passed
check-in, is not eligible for any award, cannot win the award
(`Eligibility`, D33, D35), or is named twice is refused.
"""

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from awards.eligibility import Eligibility
from awards.models import Award, BallotAward, Vote, Voter
from core import race_settings
from core.models import RaceState
from core.services import lock_race_state
from roster.models import Racer

OPEN = "open"
CLOSED = "closed"
STATES = (OPEN, CLOSED)
# The votes per ballot the judging page offers, 0 for "Not on ballot", as
# `update_ballot_awards()` does (`website/js/judging.js:271` to `:291`).
# `award.edit` takes any depth.
DEPTH_CHOICES = tuple(range(7))


class BallotFailure(Exception):
    """A refusal with PHP's code where PHP has one."""

    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


# The session key that holds the voting token, written by the vote link
# (`ui.vote.views.token_link`) and by `ballot.get`'s `voting_token`.
SESSION_TOKEN = "voting-token"

NO_TOKEN = BallotFailure(
    "no-token", "This phone has no valid voting code. Scan a racer's voting code."
)
CLOSED_NOW = BallotFailure("closed", "Voting is not open at this time")
NO_AWARD = BallotFailure("no-award", "No such award")
TOO_MANY = BallotFailure("too-many", "Too many choices offered")
INELIGIBLE = BallotFailure("ineligible", "A choice is not eligible for this award")
BAD_STATE = BallotFailure("invalid-state", "Balloting is either open or closed")


def is_open(event) -> bool:
    return RaceState.objects.filter(event=event, balloting_open=True).exists()


def set_open(event, state) -> None:
    """`ballot.open`. PHP stores any text as the state
    (`action.ballot.open.inc:8`); only `open` and `closed` are kept here."""
    if state not in STATES:
        raise BAD_STATE
    race_settings.write(event, "balloting", state)


def parse_token(text) -> uuid.UUID | None:
    """A voting token as the link or `voting_token` gives it: a GUID in any
    of the forms `uuid.UUID` reads, or None for anything else."""
    if not isinstance(text, str) or len(text) > 64:
        return None
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def token_racer(event, text) -> Racer | None:
    """The event's racer whose voting token `text` is, or None. A deleted
    racer's token and another event's are as unknown as a made-up one."""
    token = parse_token(text)
    if token is None:
        return None
    return Racer.objects.filter(event=event, voting_token=token).first()


def is_valid_token(event, text) -> bool:
    return token_racer(event, text) is not None


def voter_identifier(racer: Racer) -> str:
    """The `Voter.identifier` of a racer's token: a SHA-256 digest, so the
    voter is the token (D86) and an exported snapshot holds no token."""
    return "token:" + hashlib.sha256(racer.voting_token.bytes).hexdigest()


def voter(event, identifier: str, ip_address: str = "") -> Voter:
    """`retrieve_voterid()` (`voterid.inc:14` to `:28`): the voter's row,
    added on its first visit, while balloting is closed too, as PHP adds
    it, with the time and the client's address (`voterid.inc:18` to `:23`,
    `dbnow_expr()` and `REMOTE_ADDR`)."""
    found = Voter.objects.filter(event=event, identifier=identifier).first()
    if found is None:
        found = Voter.objects.create(
            event=event,
            identifier=identifier,
            cast_at=timezone.now(),
            ip_address=ip_address[: Voter._meta.get_field("ip_address").max_length],
        )
    return found


@dataclass(frozen=True)
class BallotEntry:
    max_votes: int
    votes: list[int]


def ballot(event, token, ip_address: str = "") -> dict[int, BallotEntry]:
    """`query.ballot.get.inc`: the token's voter row, added on its first
    visit (`voterid.inc:14` to `:28`), and each award on the ballot with its
    depth and this voter's choices, or nothing while balloting is closed
    (`query.ballot.get.inc:27` to `:45`). A token that is not a racer's of
    this event is refused with NO_TOKEN, before anything is written, where
    PHP asks for the ballot password (D86).

    The voter row is added under the race-state lock, so two phones that
    open the same token at once still make one voter."""
    with transaction.atomic():
        lock_race_state(event.id)
        racer = token_racer(event, token)
        if racer is None:
            raise NO_TOKEN
        voter_row = voter(event, voter_identifier(racer), ip_address)
    if not is_open(event):
        return {}
    found = {
        award_id: BallotEntry(depth, [])
        for award_id, depth in BallotAward.objects.filter(event=event)
        .order_by("award_id")
        .values_list("award_id", "depth")
    }
    for award_id, racer_id in (
        Vote.objects.filter(event=event, voter=voter_row)
        .order_by("award_id", "pk")
        .values_list("award_id", "racer_id")
    ):
        # PHP adds an entry for a vote on an award off the ballot
        # (`query.ballot.get.inc:40` to `:44`); deleting an award's ballot row
        # deletes its votes (`voterid.inc:85`), so none is left.
        if award_id in found:
            found[award_id].votes.append(racer_id)
    return found


def _racer_ids(posted) -> list[int] | None:
    """The posted `votes`, a JSON list of racer ids, or None for anything
    else. PHP counts and inserts whatever `json_decode` gives."""
    try:
        decoded = json.loads(posted) if isinstance(posted, str) else None
    except ValueError:
        return None
    if not isinstance(decoded, list):
        return None
    ids = []
    for item in decoded:
        if isinstance(item, bool):
            return None
        if isinstance(item, int):
            ids.append(item)
        elif isinstance(item, str) and item.isdigit():
            ids.append(int(item))
        else:
            return None
    return ids


def cast(
    event,
    token,
    award_id: int | None,
    posted_votes,
    *,
    ip_address: str = "",
):
    """`vote.cast`: the token's choices for one award replace any before.
    `award_id` is the posted id resolved to the event's award, or None.

    Refusals come in PHP's order (`action.vote.cast.inc`): closed,
    no-award, too-many, and then this port's no-token (D86) and ineligible
    (S14) checks. The token is read again here, under the lock, so a racer
    deleted since the ballot opened votes no more. Returns
    `voterid awardid racerid` for each vote, PHP's `votes` reply."""
    with transaction.atomic():
        lock_race_state(event.id)
        if not is_open(event):
            raise CLOSED_NOW
        award = (
            Award.objects.filter(event=event, pk=award_id).first()
            if award_id is not None
            else None
        )
        if award is None:
            raise NO_AWARD
        racer_ids = _racer_ids(posted_votes)
        depth = (
            BallotAward.objects.filter(event=event, award=award)
            .values_list("depth", flat=True)
            .first()
        )
        # PHP compares the count with `false` for an award off the ballot,
        # so only an empty list passes (`action.vote.cast.inc:35` to `:40`).
        if racer_ids is not None and len(racer_ids) > (depth or 0):
            raise TOO_MANY
        token_holder = token_racer(event, token)
        if token_holder is None:
            raise NO_TOKEN
        if racer_ids is None or len(set(racer_ids)) != len(racer_ids):
            raise INELIGIBLE
        racers = Racer.objects.filter(
            event=event, pk__in=racer_ids, passed_inspection=True, exclude=False
        ).select_related("group", "subgroup")
        eligibility = Eligibility(event.id)
        if len(racers) != len(racer_ids) or any(
            eligibility.refusal(award, racer) for racer in racers
        ):
            raise INELIGIBLE
        voter_row = voter(event, voter_identifier(token_holder), ip_address)
        Vote.objects.filter(event=event, voter=voter_row, award=award).delete()
        Vote.objects.bulk_create(
            Vote(event=event, voter=voter_row, award=award, racer_id=racer_id)
            for racer_id in racer_ids
        )
    return [f"{voter_row.pk} {award.pk} {racer_id}" for racer_id in racer_ids]


@dataclass(frozen=True)
class Candidate:
    racer_id: int
    score: int
    car_number: int
    first_name: str
    last_name: str


@dataclass(frozen=True)
class Tally:
    award_id: int
    award_name: str
    recipient: int
    candidates: list[Candidate]


def tallies(event) -> list[Tally]:
    """`vote_tabulations()` (`voterid.inc:32` to `:61`): each award with a
    vote, by its sort then id, its candidates most votes first. PHP leaves
    a tie in the database's order; here the lower racer id comes first."""
    counts = Counter(
        Vote.objects.filter(event=event).values_list("award_id", "racer_id")
    )
    awards = Award.objects.in_bulk({award_id for award_id, _ in counts})
    racers = Racer.objects.in_bulk({racer_id for _, racer_id in counts})
    found: dict[int, Tally] = {}
    for award_id, racer_id in sorted(
        counts,
        key=lambda key: (
            awards[key[0]].sort,
            key[0],
            -counts[key],
            key[1],
        ),
    ):
        award = awards[award_id]
        racer = racers[racer_id]
        tally = found.setdefault(
            award_id, Tally(award_id, award.name, award.racer_id or 0, [])
        )
        tally.candidates.append(
            Candidate(
                racer_id,
                counts[(award_id, racer_id)],
                racer.car_number,
                racer.first_name,
                racer.last_name,
            )
        )
    return list(found.values())
