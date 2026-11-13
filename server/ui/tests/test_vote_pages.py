"""The vote page and its status poll (#54), and the judging page's ballot
sheet. The browser flow is in `ui/tests/browser/test_vote.py`."""

import json
import uuid

import pytest
from django.test import Client

from accounts.views import make_mcheckin_token
from awards import balloting
from awards.models import BallotAward, Vote
from awards.tests.factories import AwardFactory, BallotAwardFactory, VoterFactory
from core.models import RaceSettings, RaceState, SiteState
from core.tests.factories import EventFactory
from roster.tests.factories import GroupFactory, RacerFactory, SubgroupFactory
from ui.tests.db import no_db_writes
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db


def config(response) -> dict:
    text = response.content.decode()
    start = text.index('<script id="vote-config" type="application/json">')
    start = text.index(">", start) + 1
    return json.loads(text[start : text.index("</script>", start)])


@pytest.fixture
def pack(event):
    wolves = GroupFactory(event=event, name="Wolves", sort_order=2)
    bears = GroupFactory(event=event, name="Bears", sort_order=1)
    found = {
        "wolf": RacerFactory(
            group=wolves, car_number=12, last_name="Zed", passed_inspection=True
        ),
        "bear": RacerFactory(
            group=bears, car_number=31, last_name="Abel", passed_inspection=True
        ),
        "late": RacerFactory(group=wolves, car_number=5, passed_inspection=False),
        "out": RacerFactory(
            group=wolves, car_number=6, passed_inspection=True, exclude=True
        ),
    }
    found["wolves"], found["bears"] = wolves, bears
    return found


# ----- /ballot/status -----


@pytest.mark.parametrize("is_open", [True, False])
def test_status_answers_only_whether_balloting_is_open(client, event, is_open):
    RaceState.objects.filter(event=event).update(balloting_open=is_open)
    RaceSettings.objects.filter(event=event).update(ballot_password="acorn")

    with no_db_writes(), no_session_writes():
        response = client.get("/ballot/status")

    assert response.status_code == 200
    assert json.loads(response.content) == {
        "balloting": "open" if is_open else "closed"
    }
    assert b"acorn" not in response.content
    assert b"ballot_password" not in response.content
    assert b"db-connection" not in response.content


def test_status_with_no_event_open_is_closed(client, db):
    SiteState.objects.filter(pk=1).update(active_event=None)
    assert json.loads(client.get("/ballot/status").content) == {"balloting": "closed"}


# ----- the vote page -----


def test_the_old_url_redirects_with_its_order(client, event):
    response = client.get("/vote.php?order=name")
    assert response.status_code == 301
    assert response["Location"] == "/vote/?order=name"


def test_the_page_lists_passed_racers_eligible_for_awards(client, pack):
    """`website/vote.php:67`: passedinspection = 1 AND exclude = 0."""
    with no_db_writes(), no_session_writes():
        response = client.get("/vote/")

    found = config(response)
    assert set(found["racers"]) == {str(pack["wolf"].pk), str(pack["bear"].pk)}
    page = response.content.decode()
    assert pack["wolf"].first_name not in page
    assert pack["wolf"].last_name not in page


def test_each_award_lists_only_racers_who_can_win_it(client, event, pack):
    wolf_award = AwardFactory(event=event, name="Best Wolf", group=pack["wolves"])
    pack_award = AwardFactory(event=event, name="Best in Show")

    awards = config(client.get("/vote/"))["awards"]

    assert awards[str(wolf_award.pk)]["racers"] == [pack["wolf"].pk]
    assert sorted(awards[str(pack_award.pk)]["racers"]) == sorted(
        [pack["wolf"].pk, pack["bear"].pk]
    )


def test_a_subgroup_award_lists_its_subgroup_only(client, event, pack):
    other = SubgroupFactory(group=pack["wolves"], name="Den 9")
    RacerFactory(group=pack["wolves"], subgroup=other, passed_inspection=True)
    award = AwardFactory(
        event=event, group=pack["wolves"], subgroup=pack["wolf"].subgroup
    )

    assert config(client.get("/vote/"))["awards"][str(award.pk)]["racers"] == [
        pack["wolf"].pk
    ]


def test_the_three_orders(client, pack):
    """`website/vote.php:68` to `:71`: car number, group sort order, last
    name."""
    orders = config(client.get("/vote/?order=class"))
    wolf, bear = pack["wolf"].pk, pack["bear"].pk

    assert orders["order"] == "class"
    assert orders["orders"] == {
        "car": [wolf, bear],
        "class": [bear, wolf],
        "name": [bear, wolf],
    }


def test_an_unknown_order_is_by_car(client, pack):
    assert config(client.get("/vote/?order=ballot.php"))["order"] == "car"


def test_subgroups_show_only_when_used(client, event, pack):
    page = client.get("/vote/").content.decode()
    assert "data-subgroup" not in page
    RaceSettings.objects.filter(event=event).update(use_subgroups=True)
    page = client.get("/vote/").content.decode()
    assert "data-subgroup" in page


def test_the_page_holds_no_password_and_no_ballot(client, event, pack):
    RaceSettings.objects.filter(event=event).update(ballot_password="acorn")
    RaceState.objects.filter(event=event).update(balloting_open=True)
    BallotAwardFactory(award__event=event, depth=2)

    page = client.get("/vote/").content.decode()

    assert "acorn" not in page
    assert config(client.get("/vote/"))["open"] is True


# ----- the ballot sheet on the judging page -----


def post(client, url, data=None, target="judging-sheet-body"):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target)


def test_the_ballot_sheet_needs_judging(login, event):
    assert login("anonymous").get("/judging/ballot/").status_code in (302, 403)
    assert login("RaceCrew").get("/judging/ballot/").status_code == 200


def test_the_ballot_sheet_shows_depths_and_tallies(login, event, pack):
    award = BallotAwardFactory(award__event=event, award__name="Best", depth=2).award
    AwardFactory(event=event, name="Not voted on")
    for _ in range(2):
        Vote.objects.create(
            event=event,
            voter=VoterFactory(event=event),
            award=award,
            racer=pack["wolf"],
        )
    client = login("RaceCrew")

    with no_db_writes():
        sheet = client.get("/judging/ballot/").content.decode()

    assert 'value="2" selected' in sheet
    assert "Not on ballot" in sheet
    assert "2 votes</span> for" in sheet
    assert f'data-candidate="{pack["wolf"].pk}"' in sheet


def test_the_open_switch_opens_and_closes_balloting(login, event):
    client = login("RaceCrew")

    response = post(client, "/judging/ballot/open/", {"open": "1"}, target="toasts")
    assert response.status_code == 200
    assert RaceState.objects.get(event=event).balloting_open
    post(client, "/judging/ballot/open/", {"open": "0"}, target="toasts")
    assert not RaceState.objects.get(event=event).balloting_open


def test_a_depth_of_zero_takes_the_award_off_and_clears_its_votes(login, event, pack):
    award = BallotAwardFactory(award__event=event, depth=2).award
    Vote.objects.create(
        event=event, voter=VoterFactory(event=event), award=award, racer=pack["wolf"]
    )
    client = login("RaceCrew")

    response = post(client, f"/judging/ballot/award/{award.pk}/depth/", {"depth": "0"})

    assert response.status_code == 200
    assert not BallotAward.objects.filter(award=award).exists()
    assert not Vote.objects.exists()
    post(client, f"/judging/ballot/award/{award.pk}/depth/", {"depth": "3"})
    assert BallotAward.objects.get(award=award).depth == 3


@pytest.mark.parametrize("depth", ["7", "-1", "x", ""])
def test_a_depth_outside_the_choices_is_refused(login, event, depth):
    award = AwardFactory(event=event)
    response = post(
        login("RaceCrew"), f"/judging/ballot/award/{award.pk}/depth/", {"depth": depth}
    )
    assert response.status_code == 422


def test_a_depth_for_no_award_is_refused(login, event):
    response = post(
        login("RaceCrew"), "/judging/ballot/award/9999/depth/", {"depth": "1"}
    )
    assert response.status_code == 422


def test_the_ballot_sheet_has_no_password_field(login, event):
    """D86: a voting token, not the ballot password, lets a voter in, so the
    sheet drops the field and its URL; the stored setting is left as it
    is, for settings.list and snapshots."""
    RaceSettings.objects.filter(event=event).update(ballot_password="acorn")
    coordinator = login("RaceCoordinator")

    sheet = coordinator.get("/judging/ballot/").content.decode()
    response = post(
        coordinator, "/judging/ballot/password/", {"ballot_password": "oak"}
    )

    assert "ballot_password" not in sheet
    assert "acorn" not in sheet
    assert "Ballot password" not in sheet
    assert response.status_code == 404
    assert RaceSettings.objects.get(event=event).ballot_password == "acorn"


# ----- the vote link and the voting code (#206, D86) -----


def test_the_vote_link_keeps_the_token_in_the_session_only(client, event, pack):
    token = pack["wolf"].voting_token

    response = client.get(f"/vote/code/{token}")

    assert response.status_code == 302
    assert response["Location"] == "/vote/"
    assert client.session[balloting.SESSION_TOKEN] == str(token)
    page = client.get("/vote/")
    assert str(token) not in page.content.decode()
    assert config(page)["hasToken"] is True


def bad_codes(event):
    gone = RacerFactory(group=GroupFactory(event=event, name="Gone"))
    gone_token = gone.voting_token
    gone.delete()
    elsewhere = RacerFactory(group=GroupFactory(event=EventFactory()))
    return {
        "unknown": str(uuid.uuid4()),
        "malformed": "not-a-token",
        "empty": "",
        "slashes": "a/b/c",
        "percent-nul": "%00" + str(uuid.uuid4()),
        "other-event": str(elsewhere.voting_token),
        "deleted": str(gone_token),
    }


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "malformed",
        "empty",
        "slashes",
        "percent-nul",
        "other-event",
        "deleted",
    ],
)
def test_a_bad_vote_link_answers_one_page_and_drops_the_sessions_token(
    client, event, pack, kind
):
    """The same 404 page for every bad code, so it never says whether a
    code was a deleted racer's, and the phone's earlier token is gone."""
    client.get(f"/vote/code/{pack['wolf'].voting_token}")
    code = bad_codes(event)[kind]

    response = client.get(f"/vote/code/{code}")

    assert response.status_code == 404
    text = response.content.decode()
    assert "This voting code is not valid" in text
    assert "Ask the check-in table for your racer's code." in text
    assert "deleted" not in text.lower()
    assert balloting.SESSION_TOKEN not in client.session
    assert config(client.get("/vote/"))["hasToken"] is False


def test_the_page_with_no_token_asks_for_a_code(client, event, pack):
    RaceState.objects.filter(event=event).update(balloting_open=True)

    page = client.get("/vote/").content.decode()

    assert 'data-vote-state="code"' in page
    assert "Scan a racer&#x27;s voting code to vote" in page or (
        "Scan a racer's voting code to vote" in page
    )
    assert config(client.get("/vote/"))["hasToken"] is False


def voting_code_url(racer):
    return f"/checkin/racer/{racer.pk}/voting-code/"


def test_the_voting_code_sheet_shows_the_racers_vote_link(login, event, pack):
    crew = login("RaceCrew")

    with no_db_writes(), no_session_writes():
        response = crew.get(voting_code_url(pack["wolf"]))

    text = response.content.decode()
    link = f"http://testserver/vote/code/{pack['wolf'].voting_token}"
    assert response.status_code == 200
    assert f"data-voting-url>{link}</p>" in text
    assert "checkin.show('Voting code')" in text
    assert pack["wolf"].first_name in text and "Zed" in text
    # The link opens the racer's ballot on a phone.
    phone = Client()
    assert phone.get(link.removeprefix("http://testserver")).status_code == 302
    assert phone.session[balloting.SESSION_TOKEN] == str(pack["wolf"].voting_token)


def test_the_card_offers_the_voting_code_and_holds_no_token(login, event, pack):
    page = login("RaceCrew").get("/checkin/").content.decode()

    assert voting_code_url(pack["wolf"]) in page
    assert 'data-action="voting-code"' in page
    for racer in (pack["wolf"], pack["bear"]):
        assert str(racer.voting_token) not in page


def test_a_check_in_phone_without_a_login_sees_no_voting_code(event, pack):
    """The check-in link's grant (#16) opens the page but not the sheet: it
    is for a logged-in crew role (D5)."""
    phone = Client()
    assert phone.get(f"/checkin/?tok={make_mcheckin_token()}").status_code == 302

    page = phone.get("/checkin/").content.decode()
    response = phone.get(voting_code_url(pack["wolf"]), HTTP_HX_REQUEST="true")

    assert page.count("data-racer=") >= 4
    assert 'data-action="voting-code"' not in page
    assert response.status_code == 422
    assert str(pack["wolf"].voting_token) not in response.content.decode()


def test_a_role_without_check_in_sees_no_voting_code(login, event, pack):
    """A logged-in role whose own bits lack CHECK_IN_RACERS, even with the
    check-in link's grant added to its session, does not see the sheet."""
    photo = login("Photo")
    photo.get(f"/checkin/?tok={make_mcheckin_token()}")

    response = photo.get(voting_code_url(pack["wolf"]), HTTP_HX_REQUEST="true")

    assert response.status_code == 422
    assert str(pack["wolf"].voting_token) not in response.content.decode()


def test_the_voting_code_of_another_events_racer_is_not_found(login, event):
    elsewhere = RacerFactory(group=GroupFactory(event=EventFactory()))
    response = login("RaceCrew").get(voting_code_url(elsewhere))
    assert response.status_code == 404


def test_a_tab_changes_only_the_ballot_it_loaded(client, event, pack):
    """One browser, two racers' codes (#206 review): the tab that loaded
    the first code keeps posting its list after the second code opened in
    another tab. Each tab sends the ballot key its page was rendered with,
    and a key that is not the session's code's is refused with no-token,
    so neither ballot changes behind the voter's back."""
    award = BallotAwardFactory(award__event=event, depth=3).award
    RaceState.objects.filter(event=event).update(balloting_open=True)
    first, second = pack["late"], pack["out"]
    wolf, bear = pack["wolf"], pack["bear"]

    def cast(key, *racers):
        reply = client.post(
            "/action.php",
            {
                "action": "vote.cast",
                "awardid": award.pk,
                "votes": json.dumps([r.pk for r in racers]),
                "ballot_key": key,
            },
        )
        return json.loads(reply.content)["outcome"]["code"]

    client.get(f"/vote/code/{first.voting_token}")
    first_key = config(client.get("/vote/"))["ballotKey"]
    assert cast(first_key, wolf) == "success"
    client.get(f"/vote/code/{second.voting_token}")
    second_key = config(client.get("/vote/"))["ballotKey"]
    assert cast(second_key, bear) == "success"

    stale = cast(first_key, wolf, bear)
    stale_get = json.loads(
        client.get(
            "/action.php", {"query": "ballot.get", "ballot_key": first_key}
        ).content
    )

    assert first_key and second_key and first_key != second_key
    assert str(first.voting_token) not in first_key
    assert stale == "no-token"
    assert stale_get["outcome"]["code"] == "no-token"
    by_voter = sorted(
        Vote.objects.values_list("voter__identifier", "racer_id"),
    )
    assert sorted(r for _, r in by_voter) == sorted([wolf.pk, bear.pk])
    assert len({v for v, _ in by_voter}) == 2
