"""The vote page and its status poll (#54), and the judging page's ballot
sheet. The browser flow is in `ui/tests/browser/test_vote.py`."""

import json

import pytest

from accounts.permissions import Perm
from awards.models import BallotAward, Vote
from awards.tests.factories import AwardFactory, BallotAwardFactory, VoterFactory
from core.models import RaceSettings, RaceState, SiteState
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


def test_the_password_needs_set_up(login, event):
    """`judging.js:398` writes it with settings.write, which needs SET_UP."""
    crew = login("RaceCrew")
    assert not Perm.SET_UP & 8687
    response = post(crew, "/judging/ballot/password/", {"ballot_password": "acorn"})
    assert response.status_code == 422
    assert RaceSettings.objects.get(event=event).ballot_password == ""

    response = post(
        login("RaceCoordinator"),
        "/judging/ballot/password/",
        {"ballot_password": "acorn"},
    )
    assert response.status_code == 200
    assert RaceSettings.objects.get(event=event).ballot_password == "acorn"
