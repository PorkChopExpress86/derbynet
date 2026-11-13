"""The ballot endpoints (#54): `ballot.open`, `query=ballot.get`,
`vote.cast` and `query=ballot.results`, with the fixes of S14, S15 and S16,
and the voting token of #206 (D86): a racer's token is the voter.

`testing/test-balloting.sh` runs the flow end to end against a live
server; these tests hold the rules it does not reach."""

import json
import uuid
from urllib.parse import urlencode

import pytest
from django.test import Client

from accounts.permissions import Perm
from awards import balloting
from awards.models import BallotAward, Vote, Voter
from awards.services import set_partition_list
from awards.tests.factories import AwardFactory, BallotAwardFactory
from core.models import Event, RaceSettings, RaceState
from core.tests.factories import EventFactory
from legacy_api.tests.conftest import log_in, post
from roster.models import Group, Racer
from roster.tests.factories import GroupFactory, RacerFactory, SubgroupFactory

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


def holder(event):
    """A racer of a group of its own, whose token votes; it has not passed
    check-in, as a token works either way (#206)."""
    families = Group.objects.filter(event=event, name="Families").first()
    return RacerFactory(group=families or GroupFactory(event=event, name="Families"))


def hold(client, token, **fields):
    """`ballot.get` naming `token`, which the session keeps."""
    return ask(client, "ballot.get", voting_token=str(token), **fields)


def voter_client(event, token=None):
    """A new phone that has opened a racer's vote link."""
    phone = Client()
    hold(phone, token if token is not None else holder(event).voting_token)
    return phone


@pytest.fixture
def client(event):
    """The test client holding a token of a racer of the event."""
    return voter_client(event)


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


def choices(client, award):
    return ask(client, "ballot.get")["ballot"][str(award.pk)]["votes"]


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
    """C55: PHP stores any text (`action.ballot.open.inc:8`)."""
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

    reply = ask(client, "ballot.get")

    assert reply["ballot"] == {
        str(pack_award.pk): {"max_votes": 2, "votes": []},
        str(wolf_award.pk): {"max_votes": 1, "votes": []},
    }


def test_ballot_get_while_closed_is_empty_and_still_adds_the_voter(event, pack_award):
    """PHP adds the Voters row on the first ballot.get, closed or open
    (`voterid.inc:18` to `:23`); the port keeps it."""
    reply = hold(Client(), holder(event).voting_token)

    assert reply == {"ballot": {}}
    assert Voter.objects.filter(event=event).count() == 1


def test_ballot_get_shows_this_voters_choices_only(client, event, wolves, pack_award):
    a, b = racer(wolves), racer(wolves)
    open_ballot(event)
    other = voter_client(event)

    vote(client, pack_award, a, b)
    vote(other, pack_award, b)

    assert ask(client, "ballot.get")["ballot"][str(pack_award.pk)]["votes"] == [
        a.pk,
        b.pk,
    ]
    assert ask(other, "ballot.get")["ballot"][str(pack_award.pk)]["votes"] == [b.pk]


def test_a_ballot_code_names_no_other_voter(client, event, wolves, pack_award):
    """S15: PHP lets a caller read any voter's ballot by its session id. Here
    a voter is named only by the token its session holds, so naming another
    voter, by its identifier, its row id or its racer, reads and changes
    nothing of that voter's."""
    a, b = racer(wolves), racer(wolves)
    open_ballot(event)
    vote(client, pack_award, a)
    [theirs] = Voter.objects.filter(event=event)
    named = {
        "ballot_code": theirs.identifier,
        "voterid": str(theirs.pk),
        "voter": theirs.identifier,
        "identifier": theirs.identifier,
        "racerid": str(Racer.objects.filter(group__name="Families").first().pk),
    }

    other = voter_client(event)
    reply = ask(other, "ballot.get", **named)
    cast = act(
        other, "vote.cast", awardid=pack_award.pk, votes=json.dumps([b.pk]), **named
    )

    assert reply["ballot"][str(pack_award.pk)]["votes"] == []
    assert code(cast) == "success"
    assert choices(client, pack_award) == [a.pk]
    assert choices(other, pack_award) == [b.pk]
    assert Voter.objects.filter(event=event).count() == 2


def test_a_caller_with_no_token_cannot_name_a_voter(client, event, wolves, pack_award):
    """S15 for the token path: a session with no token that names a voter
    is refused, and reads or writes nothing."""
    open_ballot(event)
    vote(client, pack_award, racer(wolves))
    [theirs] = Voter.objects.filter(event=event)
    stranger = Client()

    assert code(ask(stranger, "ballot.get", ballot_code=theirs.identifier)) == (
        "no-token"
    )
    reply = act(
        stranger,
        "vote.cast",
        awardid=pack_award.pk,
        votes="[]",
        ballot_code=theirs.identifier,
        voterid=str(theirs.pk),
    )

    assert code(reply) == "no-token"
    assert Vote.objects.filter(voter=theirs).count() == 1


def test_a_login_keeps_the_voter(client, event, wolves, pack_award):
    """A login cycles the session key and keeps its data, so the voter
    stays one voter."""
    a = racer(wolves)
    open_ballot(event)
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
    """PHP compares the count with false (`action.vote.cast.inc:35`)."""
    off = AwardFactory(event=event)
    open_ballot(event)

    assert code(vote(client, off, racer(wolves))) == "too-many"
    assert code(vote(client, off)) == "success"


def test_a_set_ballot_password_neither_blocks_a_token_nor_admits_a_caller(
    client, event, wolves, pack_award
):
    """D86: the ballot password gates nothing. A token holder who never gave
    it votes, and a caller with no token who gives it is refused."""
    a = racer(wolves)
    set_password(event, "acorn")
    open_ballot(event)
    stranger = Client()

    assert "ballot" in ask(client, "ballot.get")
    assert code(vote(client, pack_award, a)) == "success"
    assert code(ask(stranger, "ballot.get", password="acorn")) == "no-token"
    assert code(vote(stranger, pack_award, a)) == "no-token"
    assert Vote.objects.count() == 1


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
    voters = [voter_client(event) for _ in range(3)]
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


# ----- review of #54 -----


def test_refusals_come_in_phps_order_and_no_token_after_them(event, wolves, bears):
    """PHP's vote.cast answers closed, then no-award, then too-many
    (`action.vote.cast.inc:16`, `:21` to `:23`, `:35` to `:40`); the
    no-token check of D86 comes after PHP's own, and before ineligible
    (S14). Each call sets every later condition at once."""
    award = BallotAwardFactory(award__event=event, depth=1).award
    stranger = Client()
    outsider = racer(bears, passed_inspection=False)

    assert code(vote(stranger, award, outsider)) == "closed"
    open_ballot(event)
    reply = act(stranger, "vote.cast", awardid="999", votes="[true]")
    assert code(reply) == "no-award"
    assert code(vote(stranger, award, outsider, racer(wolves))) == "too-many"
    assert code(vote(stranger, award, outsider)) == "no-token"
    assert not Vote.objects.exists()


def test_the_voter_row_keeps_the_time_and_the_address(event):
    """`retrieve_voterid()` writes `when_cast` and `ip_addr`
    (`voterid.inc:18` to `:23`)."""
    Client().get(
        "/action.php",
        {"query": "ballot.get", "voting_token": str(holder(event).voting_token)},
        REMOTE_ADDR="10.1.2.3",
    )

    [voter] = Voter.objects.filter(event=event)
    assert voter.cast_at is not None
    assert voter.ip_address == "10.1.2.3"


def test_a_racer_of_another_subgroup_is_refused(client, event, wolves):
    """S14: `award.winner`'s subgroup check, which vote.cast now makes."""
    den = SubgroupFactory(group=wolves, name="Den 1")
    other = SubgroupFactory(group=wolves, name="Den 2")
    award = BallotAwardFactory(
        award__event=event, award__group=wolves, award__subgroup=den
    ).award
    open_ballot(event)

    assert code(vote(client, award, racer(wolves, subgroup=other))) == "ineligible"
    assert code(vote(client, award, racer(wolves, subgroup=den))) == "success"


def test_a_racer_whose_partition_the_list_leaves_out_is_refused(client, event, wolves):
    """S14, D35: a judged award goes only to a racer of its partition list."""
    kept, left = racer(wolves), racer(wolves)
    award = BallotAwardFactory(award__event=event).award
    set_partition_list(event, [kept.partition], award=award)
    open_ballot(event)

    assert code(vote(client, award, left)) == "ineligible"
    assert code(vote(client, award, kept)) == "success"


def test_a_tally_tie_lists_the_lower_racer_id_first(client, event, wolves):
    """As PHP's SQLite answers it: a php-scratch run of #54 gave three cars
    with two votes each in racer id order."""
    award = BallotAwardFactory(award__event=event, depth=3).award
    a, b, c = racer(wolves), racer(wolves), racer(wolves)
    open_ballot(event)
    for chosen in ([c, a], [b], [a, c, b]):
        vote(voter_client(event), award, *chosen)
    log_in(client, Perm.JUDGING)

    [tally] = ask(client, "ballot.results")["awards"]

    assert [x["carnumber"] for x in tally["candidates"]] == [
        a.car_number,
        b.car_number,
        c.car_number,
    ]


# ----- the voting token (#206, D86) -----


def test_two_phones_with_one_token_edit_one_ballot(event, wolves, pack_award):
    """D86: the token is the voter. The second phone sees the first one's
    choices, its change replaces them, and the results count one voter."""
    a, b = racer(wolves), racer(wolves)
    token = holder(event).voting_token
    open_ballot(event)
    first, second = voter_client(event, token), voter_client(event, token)

    assert code(vote(first, pack_award, a)) == "success"
    assert choices(second, pack_award) == [a.pk]
    assert code(vote(second, pack_award, b)) == "success"

    assert choices(first, pack_award) == [b.pk]
    assert Voter.objects.filter(event=event).count() == 1
    [tally] = balloting.tallies(event)
    assert [(c.racer_id, c.score) for c in tally.candidates] == [(b.pk, 1)]


def bad_tokens(event):
    """Every kind of token that names no racer of the event."""
    gone = holder(event)
    gone_token = gone.voting_token
    gone.delete()
    elsewhere = RacerFactory(group=GroupFactory(event=EventFactory()))
    return {
        "none": None,
        "empty": "",
        "unknown": str(uuid.uuid4()),
        "malformed": "not-a-token",
        "nul": "\x00" + str(uuid.uuid4()),
        "long": "a" * 5000,
        "other-event": str(elsewhere.voting_token),
        "deleted": str(gone_token),
    }


@pytest.mark.parametrize(
    "kind",
    ["none", "empty", "unknown", "malformed", "nul", "long", "other-event", "deleted"],
)
def test_a_call_with_no_valid_token_is_refused_and_changes_no_vote(
    event, wolves, pack_award, kind
):
    """D86: on ballot.get and on vote.cast alike, with the same code, and
    no Voter row is added. A bad token also replaces the session's, so a
    phone that held a good one votes no more."""
    a = racer(wolves)
    open_ballot(event)
    phone = voter_client(event)
    vote(phone, pack_award, a)
    before = list(Vote.objects.values_list("voter_id", "racer_id"))
    token = bad_tokens(event)[kind]
    fields = {} if token is None else {"voting_token": token}

    stranger = Client()
    get_reply = ask(stranger, "ballot.get", **fields)
    cast_reply = vote(stranger, pack_award, a)
    switched = ask(phone, "ballot.get", **fields)
    after_switch = vote(phone, pack_award)

    assert get_reply == {
        "outcome": {
            "summary": "failure",
            "code": "no-token",
            "description": balloting.NO_TOKEN.description,
        }
    }
    assert code(cast_reply) == "no-token"
    if token is None:
        # No token named: the phone keeps its own.
        assert "ballot" in switched
        assert code(after_switch) == "success"
        assert not Vote.objects.exists()
    else:
        assert code(switched) == "no-token"
        assert code(after_switch) == "no-token"
        assert list(Vote.objects.values_list("voter_id", "racer_id")) == before
    assert Voter.objects.filter(event=event).count() == 1


def test_a_token_from_ballot_get_replaces_the_sessions_token(event, wolves, pack_award):
    """A family with two racers holds two tokens: naming the second one
    switches the phone to that racer's ballot."""
    a, b = racer(wolves), racer(wolves)
    first, second = holder(event).voting_token, holder(event).voting_token
    open_ballot(event)
    phone = voter_client(event, first)
    vote(phone, pack_award, a)

    hold(phone, second)
    vote(phone, pack_award, b)

    assert choices(voter_client(event, first), pack_award) == [a.pk]
    assert choices(voter_client(event, second), pack_award) == [b.pk]


def test_the_voter_is_named_by_a_digest_of_the_token(event, pack_award):
    """D87 carries tokens in snapshots, and Voters.identifier is exported,
    so the voter row holds a SHA-256 of the token, never the token."""
    token = holder(event).voting_token
    voter_client(event, token)

    [voter] = Voter.objects.filter(event=event)
    assert str(token) not in voter.identifier
    assert token.hex not in voter.identifier
    assert voter.identifier.startswith("token:")
    assert len(voter.identifier) == len("token:") + 64


def test_a_token_reads_in_any_guid_form(event, pack_award):
    token = holder(event).voting_token
    for form in (str(token).upper(), token.hex, "{" + str(token) + "}"):
        assert "ballot" in hold(Client(), form)
    assert Voter.objects.filter(event=event).count() == 1


def test_older_session_voters_and_a_deleted_racers_ballot_keep_counting(
    event, wolves, pack_award
):
    """#54's voters, named by a random session id, and the ballot of a racer
    deleted since, still count in the results; neither can be edited."""
    a = racer(wolves)
    old = Voter.objects.create(event=event, identifier="0123456789abcdef" * 2)
    Vote.objects.create(event=event, voter=old, award=pack_award, racer=a)
    gone = holder(event)
    open_ballot(event)
    phone = voter_client(event, gone.voting_token)
    vote(phone, pack_award, a)

    gone.delete()

    [tally] = balloting.tallies(event)
    assert [(c.racer_id, c.score) for c in tally.candidates] == [(a.pk, 2)]
    assert code(vote(phone, pack_award)) == "no-token"
    assert Vote.objects.count() == 2


def test_a_racer_who_has_not_passed_check_in_still_votes(event, wolves, pack_award):
    """A token works whether or not its racer passed check-in; the
    candidate rules (S14) are unchanged."""
    voter = RacerFactory(group=wolves, passed_inspection=False)
    open_ballot(event)
    phone = voter_client(event, voter.voting_token)

    assert code(vote(phone, pack_award, racer(wolves))) == "success"
    assert code(vote(phone, pack_award, voter)) == "ineligible"


def test_a_read_only_event_shows_the_ballot_and_refuses_a_vote(
    event, wolves, pack_award
):
    """A read-only event (#212) still answers ballot.get, adding the voter
    row PHP adds on a read, and refuses vote.cast."""
    a = racer(wolves)
    open_ballot(event)
    phone = voter_client(event)
    vote(phone, pack_award, a)
    Event.objects.filter(pk=event.pk).update(read_only=True)
    late = voter_client(event)

    assert choices(phone, pack_award) == [a.pk]
    assert choices(late, pack_award) == []
    assert code(vote(phone, pack_award)) == "event-read-only"
    assert Voter.objects.filter(event=event).count() == 2
    assert Vote.objects.count() == 1
