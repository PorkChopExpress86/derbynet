"""Online balloting (#54): the ballot behind `vote.php` and the ballot
endpoints, ported from `website/inc/voterid.inc`,
`website/ajax/query.ballot.get.inc`, `website/ajax/action.vote.cast.inc`,
`website/ajax/query.ballot.results.inc` and
`website/ajax/action.ballot.open.inc`.

There is one ballot, PHP's `BALLOTID` 1 (`voterid.inc:6`): every award with
a `BallotAward` row is on it, `depth` votes deep. A voter is a `Voter` row
named by its identifier. PHP names a voter by its PHP session id
(`query.ballot.get.inc:17`), or by any `ballot_code` the caller sends
(S15). Here the caller never names the voter: the legacy handler keeps a
random identifier in the session, so a login's `cycle_key` keeps the same
voter.

`cast()` adds the checks PHP leaves out (S14): a ballot password that was
never given refuses the vote, and so does a racer who has not passed check-in,
is not eligible for any award, cannot win the award (`Eligibility`, D33,
D35), or is named twice.
"""

import json
from collections import Counter
from dataclasses import dataclass

from django.db import transaction

from awards.eligibility import Eligibility
from awards.models import Award, BallotAward, Vote, Voter
from core import race_settings
from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from racing.engine.phpcompat import php_numeric
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


PASSWORD = BallotFailure("password", "Wrong password for ballot")
CLOSED_NOW = BallotFailure("closed", "Voting is not open at this time")
NO_AWARD = BallotFailure("no-award", "No such award")
TOO_MANY = BallotFailure("too-many", "Too many choices offered")
INELIGIBLE = BallotFailure("ineligible", "A choice is not eligible for this award")
BAD_STATE = BallotFailure("invalid-state", "Balloting is either open or closed")


def is_open(event) -> bool:
    return RaceState.objects.filter(event=event, balloting_open=True).exists()


def password(event) -> str:
    return RaceSettings.objects.get(event=event).ballot_password


def password_matches(event, given: str) -> bool:
    """PHP's `$pwd != read_raceinfo('ballot_password', '')` turned around
    (`query.ballot.get.inc:12`): two numeric strings compare as numbers."""
    stored = password(event)
    given_number, stored_number = php_numeric(given), php_numeric(stored)
    if given_number is not None and stored_number is not None:
        return given_number == stored_number
    return given == stored


def needs_password(event) -> bool:
    return password(event) != ""


def set_open(event, state) -> None:
    """`ballot.open`. PHP stores any text as the state
    (`action.ballot.open.inc:8`); only `open` and `closed` are kept here."""
    if state not in STATES:
        raise BAD_STATE
    race_settings.write(event, "balloting", state)


def set_password(event, text: str) -> None:
    race_settings.write(event, "ballot_password", text)


def voter(event, identifier: str) -> Voter:
    """`retrieve_voterid()` (`voterid.inc:14` to `:29`): the voter's row,
    added on its first visit, while balloting is closed too, as PHP adds
    it."""
    found = Voter.objects.filter(event=event, identifier=identifier).first()
    if found is None:
        found = Voter.objects.create(event=event, identifier=identifier)
    return found


@dataclass(frozen=True)
class BallotEntry:
    max_votes: int
    votes: list[int]


def ballot(event, voter_row: Voter) -> dict[int, BallotEntry]:
    """`query.ballot.get.inc:25` to `:42`: each award on the ballot with its
    depth and this voter's choices, or nothing while balloting is closed."""
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
        # (`query.ballot.get.inc:39`); deleting an award's ballot row
        # deletes its votes (`voterid.inc:84`), so none is left.
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


def cast(event, voter_identifier: str, award_id: int | None, posted_votes, *, allowed):
    """`vote.cast`: the voter's choices for one award replace any before.
    `award_id` is the posted id resolved to the event's award, or None.

    `allowed` says the voter gave the ballot password in this session, or
    that none is set. Refusals come in PHP's order (`action.vote.cast.inc`):
    closed, no-award, too-many, and then this port's password (S14) and
    ineligible (S14) checks. Returns `voterid awardid racerid` for each vote,
    PHP's `votes` reply."""
    with transaction.atomic():
        lock_race_state(event.id)
        if not allowed and needs_password(event):
            raise PASSWORD
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
        # so only an empty list passes (`action.vote.cast.inc:33` to `:38`).
        if racer_ids is not None and len(racer_ids) > (depth or 0):
            raise TOO_MANY
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
        voter_row = voter(event, voter_identifier)
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
    """`vote_tabulations()` (`voterid.inc:34` to `:62`): each award with a
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
