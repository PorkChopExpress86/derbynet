"""The ballot endpoints (#54): `ballot.open`, `query=ballot.get`,
`vote.cast` and `query=ballot.results`, with the fixes of S14, S15 and S16.

`testing/test-balloting.sh` runs the PHP flow end to end against a live
server; these tests hold the rules it does not reach."""

import json
from urllib.parse import urlencode

import pytest
from django.test import Client

from accounts.permissions import Perm
from awards.models import BallotAward, Vote, Voter
from awards.tests.factories import AwardFactory, BallotAwardFactory
from core.models import RaceSettings, RaceState
from core.tests.factories import EventFactory
from legacy_api.tests.conftest import log_in, post
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db


def act(client, action_name, **fields):
    return json.loads(
        post(client, urlencode({"action": action_name, **fields})).content
    )


def ask(client, query_name, **fields):
    return json.loads(
        client.get("/action.php", {"query": query_name, **fields}).content
    )


def code(reply):
    return reply["outcome"]["code"]


def open_ballot(event, is_open=True):
    RaceState.objects.filter(event=event).update(balloting_open=is_open)


def set_password(event, text):
    RaceSettings.objects.filter(event=event).update(ballot_password=text)


@pytest.fixture
def wolves(event):
    return GroupFactory(event=event, name="Wolves", sort_order=1)


@pytest.fixture
def bears(event):
    return GroupFactory(event=event, name="Bears", sort_order=2)


def racer(group, **fields):
    fields.setdefault("passed_inspection", True)
    return RacerFactory(group=group, **fields)


@pytest.fixture
def pack_award(event):
    return BallotAwardFactory(award__event=event, award__name="Best", depth=2).award


@pytest.fixture
def wolf_award(event, wolves):
    return BallotAwardFactory(
        award__event=event, award__name="Best Wolf", award__group=wolves, depth=1
    ).award


def vote(client, award, *racers, raw=None):
    votes = raw if raw is not None else json.dumps([r.pk for r in racers])
    return act(client, "vote.cast", awardid=award.pk, votes=votes)


# ----- ballot.open -----


def test_ballot_open_needs_judging(client, event):
    assert code(act(client, "ballot.open", state="open")) == "notauthorized"
    log_in(client, Perm.JUDGING)

    assert code(act(client, "ballot.open", state="open")) == "success"
    assert RaceState.objects.get(event=event).balloting_open
    assert code(act(client, "ballot.open", state="closed")) == "success"
    assert not RaceState.objects.get(event=event).balloting_open


@pytest.mark.parametrize("state", ["maybe", "", "OPEN"])
def test_ballot_open_takes_only_open_or_closed(client, event, state):
    """B1: PHP stores any text."""
    open_ballot(event)
    log_in(client, Perm.JUDGING)

    assert code(act(client, "ballot.open", state=state)) == "invalid-state"
    assert RaceState.objects.get(event=event).balloting_open


def test_ballot_open_with_no_state_changes_nothing(client, event):
    log_in(client, Perm.JUDGING)
    assert code(act(client, "ballot.open")) == "invalid-state"


# ----- ballot.get -----


def test_ballot_get_lists_each_ballot_award_with_its_depth(
    client, event, pack_award, wolf_award
):
    AwardFactory(event=event, name="Not on the ballot")
    open_ballot(event)

    reply = ask(client, "ballot.get", password="")

    assert reply["ballot"] == {
        str(pack_award.pk): {"max_votes": 2, "votes": []},
        str(wolf_award.pk): {"max_votes": 1, "votes": []},
    }


def test_ballot_get_while_closed_is_empty_and_still_adds_the_voter(
    client, event, pack_award
):
    """PHP adds the Voters row on the first ballot.get, closed or open
    (`voterid.inc:17` to `:22`); the port keeps it."""
    reply = ask(client, "ballot.get", password="")

    assert reply == {"ballot": {}}
    assert Voter.objects.filter(event=event).count() == 1


def test_a_ballot_password_is_asked_for_once_per_session(client, event, pack_award):
    set_password(event, "acorn")
    open_ballot(event)

    assert code(ask(client, "ballot.get")) == "password"
    assert code(ask(client, "ballot.get", password="oak")) == "password"
    assert "ballot" in ask(client, "ballot.get", password="acorn")
    # PHP's ok-to-vote flag: the session needs no password again.
    assert "ballot" in ask(client, "ballot.get", password="")
    assert code(ask(Client(), "ballot.get", password="")) == "password"


def test_two_numeric_passwords_compare_as_numbers(client, event):
    """PHP's `!=` (`query.ballot.get.inc:12`)."""
    set_password(event, "10")
    assert "ballot" in ask(client, "ballot.get", password="1e1")


def test_ballot_get_shows_this_voters_choices_only(client, event, wolves, pack_award):
    a, b = racer(wolves), racer(wolves)
    open_ballot(event)
    ask(client, "ballot.get", password="")
    other = Client()
    ask(other, "ballot.get", password="")

    vote(client, pack_award, a, b)
    vote(other, pack_award, b)

    assert ask(client, "ballot.get")["ballot"][str(pack_award.pk)]["votes"] == [
        a.pk,
        b.pk,
    ]
    assert ask(other, "ballot.get")["ballot"][str(pack_award.pk)]["votes"] == [b.pk]


def test_a_ballot_code_names_no_other_voter(client, event, wolves, pack_award):
    """S15: PHP lets a caller read any voter's ballot by its session id."""
    a = racer(wolves)
    open_ballot(event)
    ask(client, "ballot.get", password="")
    vote(client, pack_award, a)
    [theirs] = Voter.objects.filter(event=event)

    other = Client()
    reply = ask(other, "ballot.get", password="", ballot_code=theirs.identifier)

    assert reply["ballot"][str(pack_award.pk)]["votes"] == []
    assert Voter.objects.filter(event=event).count() == 2


def test_a_login_keeps_the_voter(client, event, wolves, pack_award):
    """A login cycles the session key and keeps its data, so the voter
    stays one voter."""
    a = racer(wolves)
    open_ballot(event)
    ask(client, "ballot.get", password="")
    vote(client, pack_award, a)

    act(client, "role.login", name="RaceCoordinator", password="doyourbest")

    assert ask(client, "ballot.get")["ballot"][str(pack_award.pk)]["votes"] == [a.pk]
    assert Voter.objects.filter(event=event).count() == 1


# ----- vote.cast -----


def test_a_vote_replaces_the_voters_choices_for_the_award(
    client, event, wolves, pack_award
):
    a, b, c = racer(wolves), racer(wolves), racer(wolves)
    open_ballot(event)

    first = vote(client, pack_award, a, b)
    assert code(first) == "success"
    [voter] = Voter.objects.filter(event=event)
    assert first["votes"] == [
        f"{voter.pk} {pack_award.pk} {a.pk}",
        f"{voter.pk} {pack_award.pk} {b.pk}",
    ]
    assert code(vote(client, pack_award, c)) == "success"

    assert list(Vote.objects.values_list("racer_id", flat=True)) == [c.pk]


def test_a_vote_while_closed_is_refused(client, event, wolves, pack_award):
    assert code(vote(client, pack_award, racer(wolves))) == "closed"
    assert not Vote.objects.exists()


def test_a_vote_for_no_award_is_refused(client, event, wolves):
    open_ballot(event)
    reply = act(client, "vote.cast", awardid="999", votes="[]")
    assert code(reply) == "no-award"


def test_a_vote_for_another_events_award_is_refused(client, event, wolves):
    open_ballot(event)
    theirs = BallotAwardFactory(award__event=EventFactory()).award
    assert code(vote(client, theirs)) == "no-award"


def test_more_choices_than_the_depth_are_refused(client, event, wolves, pack_award):
    open_ballot(event)
    reply = vote(client, pack_award, racer(wolves), racer(wolves), racer(wolves))
    assert code(reply) == "too-many"


def test_an_award_off_the_ballot_takes_only_an_empty_list(client, event, wolves):
    """PHP compares the count with false (`action.vote.cast.inc:33`)."""
    off = AwardFactory(event=event)
    open_ballot(event)

    assert code(vote(client, off, racer(wolves))) == "too-many"
    assert code(vote(client, off)) == "success"


def test_a_vote_needs_the_password_when_one_is_set(client, event, wolves, pack_award):
    """S14: PHP's vote.cast never checks ok-to-vote."""
    a = racer(wolves)
    set_password(event, "acorn")
    open_ballot(event)

    assert code(vote(client, pack_award, a)) == "password"
    assert not Vote.objects.exists()
    ask(client, "ballot.get", password="acorn")
    assert code(vote(client, pack_award, a)) == "success"


@pytest.mark.parametrize(
    "case",
    ["not-passed", "excluded", "other-group", "twice", "other-event", "unknown"],
)
def test_a_racer_who_cannot_win_the_award_is_refused(
    client, event, wolves, bears, wolf_award, case
):
    """S14: PHP stores any racer id."""
    open_ballot(event)
    choices = {
        "not-passed": lambda: [racer(wolves, passed_inspection=False).pk],
        "excluded": lambda: [racer(wolves, exclude=True).pk],
        "other-group": lambda: [racer(bears).pk],
        "twice": lambda: [(r := racer(wolves)).pk, r.pk],
        "other-event": lambda: [racer(GroupFactory(event=EventFactory())).pk],
        "unknown": lambda: [999999],
    }[case]()
    # Room for "twice".
    BallotAward.objects.filter(award=wolf_award).update(depth=2)

    reply = vote(client, wolf_award, raw=json.dumps(choices))

    assert code(reply) == "ineligible"
    assert not Vote.objects.exists()


@pytest.mark.parametrize("raw", ["", "not json", "5", '{"a": 1}', "[true]", '["x"]'])
def test_votes_that_are_not_a_list_of_racer_ids_are_refused(
    client, event, pack_award, raw
):
    open_ballot(event)
    assert code(vote(client, pack_award, raw=raw)) == "ineligible"


def test_a_racer_in_the_awards_group_is_taken(client, event, wolves, wolf_award):
    open_ballot(event)
    assert code(vote(client, wolf_award, racer(wolves))) == "success"


# ----- ballot.results -----


def test_ballot_results_needs_judging(client, event):
    """S16: PHP answers anyone."""
    assert code(ask(client, "ballot.results")) == "notauthorized"


def test_ballot_results_tally_each_award_most_votes_first(
    client, event, wolves, pack_award, wolf_award
):
    pack_award.sort, wolf_award.sort = 2, 1
    pack_award.save()
    wolf_award.save()
    a, b = racer(wolves, car_number=101), racer(wolves, car_number=102)
    open_ballot(event)
    voters = [Client() for _ in range(3)]
    for voter in voters:
        vote(voter, wolf_award, a)
    vote(voters[0], pack_award, b)
    vote(voters[1], pack_award, a, b)
    log_in(client, Perm.JUDGING)

    reply = ask(client, "ballot.results")

    assert reply["awards"] == [
        {
            "awardid": wolf_award.pk,
            "awardname": "Best Wolf",
            "recipient": 0,
            "candidates": [
                {
                    "score": 3,
                    "carnumber": 101,
                    "firstname": a.first_name,
                    "lastname": a.last_name,
                }
            ],
        },
        {
            "awardid": pack_award.pk,
            "awardname": "Best",
            "recipient": 0,
            "candidates": [
                {
                    "score": 2,
                    "carnumber": 102,
                    "firstname": b.first_name,
                    "lastname": b.last_name,
                },
                {
                    "score": 1,
                    "carnumber": 101,
                    "firstname": a.first_name,
                    "lastname": a.last_name,
                },
            ],
        },
    ]
