"""The vote page in a browser (#54), on a phone and a desktop.

- A wrong ballot password shows the message, the right one opens the
  ballot, two votes fill a depth-2 award, and the third car's toggle is
  disabled with its reason.
- Closing balloting from another session turns the page to its closed state
  within 6 s with no reload, and opening it again brings the ballot back.
- A racer outside an award's group is not in that award's grid.
- Votes from an earlier visit show on the award card, the sort control keeps
  `?order=` on this page (C33), a refused vote names the reason in a toast,
  and a ballot with no award says so.
- The judging page's ballot sheet opens balloting and sets an award's votes
  per ballot.
- The screenshots of every state at both sizes.
"""

import json
import re

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import expect

from awards.models import Award, BallotAward, Vote
from core.models import RaceSettings, RaceState
from roster.models import Group, Racer
from roster.services import groups
from roster.services.racers import insert_new_racer, pass_racer
from ui.tests.browser.conftest import assert_accessible, htmx_post, shoot
from ui.tests.roles import PASSWORDS

DESIGN = 1
STOP_TIMERS = """() => {
  const last = setTimeout(() => {});
  for (let id = 0; id <= last; id++) { clearTimeout(id); clearInterval(id); }
}"""


@pytest.fixture(autouse=True)
def _stop_the_polls(page):
    """The page polls /ballot/status every 5 s, so a poll could start after
    the harness drained the requests in flight and reach a flushed
    database. Its timers stop, then what is out is drained, before the page
    closes (as test_replay.py's _stop_the_polls does)."""
    yield
    if page.is_closed():
        return
    for frame in page.frames:
        try:
            frame.evaluate(STOP_TIMERS)
        except Exception:  # noqa: BLE001, S110 - a detached frame has no timers
            pass
    page.in_flight.drain(page)


def racer(event, name, partition, car):
    made = insert_new_racer(
        event,
        first_name=name,
        last_name="Racer",
        car_number=car,
        partition_name=partition,
    ).racer
    pass_racer(made, True)
    return made


def pack(event):
    """Wolves and Bears, a group each (by partition)."""
    for name in ("Wolves", "Bears"):
        groups.add_partition(event, name)
    return {
        "w1": racer(event, "Ava", "Wolves", 101),
        "w2": racer(event, "Ben", "Wolves", 102),
        "w3": racer(event, "Cal", "Wolves", 103),
        "b1": racer(event, "Dee", "Bears", 201),
        "wolves": Group.objects.get(event=event, name="Wolves"),
    }


def ballot_award(event, name, depth, group=None, sort=0):
    award = Award.objects.create(
        event=event, name=name, award_type_id=DESIGN, group=group, sort=sort
    )
    BallotAward.objects.create(event=event, award=award, depth=depth)
    return award


def set_open(event, is_open=True):
    RaceState.objects.filter(event=event).update(balloting_open=is_open)


def state(page):
    return page.locator("[data-vote]")


def award_card(page, award):
    return page.locator(f'[data-award-card="{award.pk}"] button')


def car_card(page, racer):
    return page.locator(f'[data-racer="{racer.pk}"] button')


def toggle(page):
    return page.locator("[data-vote-toggle]")


def close_sheet(page):
    page.locator('[data-sheet="vote-racer"] [data-close]').click()
    expect(page.locator('[data-sheet="vote-racer"]')).to_be_hidden()


def vote_for(page, racer):
    car_card(page, racer).click()
    expect(toggle(page)).to_be_enabled()
    with page.expect_response(lambda r: r.request.method == "POST"):
        toggle(page).click()
    expect(toggle(page)).to_have_attribute("aria-pressed", "true")
    close_sheet(page)


def logged_in_as(page, live_server, role: str):
    """The browser takes the session of a login made beside it. The ballot
    sheet is the subject here, not the login page: under load, the login
    form's POST once took over 30 s to navigate (2 in 400 runs on Postgres)
    and a tap before Alpine bound the role buttons was lost (reproduced by
    holding the Alpine script)."""
    client = Client()
    reply = client.post("/login/", {"name": role, "password": PASSWORDS[role]})
    assert reply.status_code == 302
    cookie = client.cookies[settings.SESSION_COOKIE_NAME]
    page.context.add_cookies(
        [{"name": cookie.key, "value": cookie.value, "url": live_server.url}]
    )


def judge_session() -> Client:
    client = Client()
    client.post("/login/", {"name": "RaceCrew", "password": PASSWORDS["RaceCrew"]})
    return client


def test_password_then_two_votes_fill_a_depth_two_award(page, event):
    """The phone acceptance check, at both sizes."""
    found = pack(event)
    best = ballot_award(event, "Best in Show", 2)
    RaceSettings.objects.filter(event=event).update(ballot_password="acorn")
    set_open(event)

    page.goto("/vote/")
    expect(state(page)).to_have_attribute("data-vote-state", "password")
    expect(page.locator("[data-wrong-password]")).to_be_hidden()
    assert_accessible(page)
    shoot(page, "vote-password")
    page.get_by_label("Ballot password").fill("oak")
    page.get_by_role("button", name="Open the ballot").click()
    expect(page.locator("[data-wrong-password]")).to_be_visible()
    expect(page.locator("[data-wrong-password]")).to_have_text(
        "The password you entered is incorrect. Please try again."
    )
    shoot(page, "vote-wrong-password")
    page.get_by_label("Ballot password").fill("acorn")
    page.get_by_role("button", name="Open the ballot").click()

    expect(state(page)).to_have_attribute("data-vote-state", "open")
    expect(award_card(page, best)).to_contain_text("Vote for up to 2")
    expect(award_card(page, best)).to_contain_text("0 chosen")
    award_card(page, best).click()
    expect(page.locator("[data-award-view]")).to_be_visible()
    vote_for(page, found["w1"])
    vote_for(page, found["w2"])
    expect(page.locator("[data-award-summary]")).to_have_text(
        "Vote for up to 2 · 2 chosen"
    )

    car_card(page, found["w3"]).click()
    expect(toggle(page)).to_be_disabled()
    expect(page.locator("[data-full-reason]")).to_have_text(
        "You already chose 2 cars for Best in Show. Remove one to vote for this car."
    )
    shoot(page, "vote-sheet-full", full_page=False)
    close_sheet(page)
    assert sorted(Vote.objects.values_list("racer__car_number", flat=True)) == [
        101,
        102,
    ]

    # Taking one back frees the third car's toggle.
    car_card(page, found["w2"]).click()
    with page.expect_response(lambda r: r.request.method == "POST"):
        toggle(page).click()
    expect(toggle(page)).to_have_attribute("aria-pressed", "false")
    close_sheet(page)
    car_card(page, found["w3"]).click()
    expect(toggle(page)).to_be_enabled()
    expect(page.locator("[data-full-reason]")).to_be_hidden()
    close_sheet(page)
    assert sorted(Vote.objects.values_list("racer__car_number", flat=True)) == [101]


def test_closing_from_another_session_shows_closed_within_six_seconds(page, event):
    found = pack(event)
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    page.goto("/vote/")
    expect(award_card(page, best)).to_be_visible()
    award_card(page, best).click()
    car_card(page, found["w1"]).click()
    page.evaluate("window.__sameDocument = true")

    reply = judge_session().post(
        "/action.php", {"action": "ballot.open", "state": "closed"}
    )
    assert json.loads(reply.content)["outcome"]["code"] == "success"

    expect(state(page)).to_have_attribute("data-vote-state", "closed", timeout=6000)
    expect(page.get_by_role("heading", name="Voting is closed")).to_be_visible()
    expect(page.locator('[data-sheet="vote-racer"]')).to_be_hidden()
    assert page.evaluate("window.__sameDocument") is True
    assert_accessible(page)
    shoot(page, "vote-closed")

    judge_session().post("/action.php", {"action": "ballot.open", "state": "open"})
    expect(state(page)).to_have_attribute("data-vote-state", "open", timeout=6000)
    expect(award_card(page, best)).to_be_visible()
    assert page.evaluate("window.__sameDocument") is True


def test_a_racer_outside_the_awards_group_is_not_listed(page, event):
    found = pack(event)
    wolf = ballot_award(event, "Best Wolf", 1, group=found["wolves"])
    best = ballot_award(event, "Best in Show", 1, sort=1)
    set_open(event)
    page.goto("/vote/")

    award_card(page, wolf).click()
    for car in ("w1", "w2", "w3"):
        expect(car_card(page, found[car])).to_be_visible()
    expect(car_card(page, found["b1"])).to_be_hidden()
    page.locator("[data-back]").click()
    award_card(page, best).click()
    expect(car_card(page, found["b1"])).to_be_visible()


def test_earlier_votes_show_on_the_award_card(page, event):
    found = pack(event)
    best = ballot_award(event, "Best in Show", 2)
    set_open(event)
    page.goto("/vote/")
    award_card(page, best).click()
    vote_for(page, found["w3"])

    page.reload()

    card = award_card(page, best)
    expect(card).to_contain_text("1 chosen")
    expect(card.locator(f'[data-chosen-racer="{found["w3"].pk}"]')).to_have_text("103")
    assert_accessible(page)
    shoot(page, "vote-awards")


def test_the_sort_control_orders_the_grid_and_keeps_the_url(page, event):
    found = pack(event)
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    page.goto("/vote/?order=name")
    award_card(page, best).click()

    def shown():
        return page.locator("[data-racer-cards] [data-car]").evaluate_all(
            """cars => cars.filter((c) => c.offsetParent)
              .map((c) => [getComputedStyle(c.closest('li')).order, c.textContent])
              .sort((a, b) => a[0] - b[0]).map((p) => p[1])"""
        )

    # Last names are all "Racer": by first name, Ava Ben Cal Dee.
    assert shown() == ["101", "102", "103", "201"]
    page.locator('[data-sort] input[value="class"]').check(force=True)
    expect(page).to_have_url(re.compile(r"/vote/\?order=class$"))
    # Groups by sort order: Wolves were added first.
    assert shown()[:3] == ["101", "102", "103"]
    page.locator('[data-sort] input[value="car"]').check(force=True)
    expect(page).to_have_url(re.compile(r"/vote/\?order=car$"))
    assert_accessible(page)
    shoot(page, "vote-grid")
    car_card(page, found["b1"]).click()
    expect(page.locator("[data-sheet-details]")).to_have_text("Bears")
    assert_accessible(page)
    shoot(page, "vote-sheet", full_page=False)


def test_a_refused_vote_names_the_reason(page, event):
    """The server refused it: the toast says why, and nothing is chosen."""
    found = pack(event)
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    page.goto("/vote/")
    award_card(page, best).click()
    car_card(page, found["w1"]).click()

    def refuse(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "outcome": {
                        "summary": "failure",
                        "code": "too-many",
                        "description": "Too many choices offered",
                    }
                }
            ),
        )

    page.route(
        "**/action.php",
        lambda route: (
            refuse(route) if route.request.method == "POST" else route.continue_()
        ),
    )
    toggle(page).click()

    expect(page.locator("[data-toast]")).to_contain_text(
        "Best in Show takes no more than 1."
    )
    expect(toggle(page)).to_have_attribute("aria-pressed", "false")
    assert not Vote.objects.exists()


def test_no_award_on_the_ballot_says_so(page, event):
    pack(event)
    set_open(event)
    page.goto("/vote/")

    expect(state(page)).to_have_attribute("data-vote-state", "none")
    expect(
        page.get_by_text("There are no awards available for voting.")
    ).to_be_visible()


def test_the_judging_ballot_sheet_opens_balloting_and_sets_depths(
    page, event, live_server
):
    pack(event)
    award = Award.objects.create(event=event, name="Best Paint", award_type_id=DESIGN)
    logged_in_as(page, live_server, "RaceCoordinator")
    page.goto("/judging/")
    page.locator("[data-open-ballot]").click()
    sheet = page.locator("[data-ballot-sheet]")
    expect(sheet).to_be_visible()

    with page.expect_response(lambda r: "/judging/ballot/open/" in r.url):
        sheet.get_by_role("switch").click()
    assert RaceState.objects.get(event=event).balloting_open
    with htmx_post(page, f"/judging/ballot/award/{award.pk}/depth/"):
        sheet.locator(f"#ballot-depth-{award.pk}").select_option("3")
    assert BallotAward.objects.get(award=award).depth == 3
    with htmx_post(page, "/judging/ballot/password/"):
        page.get_by_label("Ballot password").fill("acorn")
        page.get_by_label("Ballot password").press("Tab")
    assert RaceSettings.objects.get(event=event).ballot_password == "acorn"
    assert_accessible(page)
    shoot(page, "judging-ballot-sheet", full_page=False)


def test_a_choice_that_can_no_longer_win_never_locks_the_award(page, event):
    """A car chosen earlier that moved out of the award's group, still
    checked in, leaves the voter's choices: the next vote replaces it, where
    every vote failed with ineligible while the page posted it (#54
    review)."""
    found = pack(event)
    wolf = ballot_award(event, "Best Wolf", 2, group=found["wolves"])
    set_open(event)
    page.goto("/vote/")
    award_card(page, wolf).click()
    vote_for(page, found["w1"])
    bear = found["b1"]
    Racer.objects.filter(pk=found["w1"].pk).update(
        group=bear.group, subgroup=bear.subgroup, partition=bear.partition
    )

    page.reload()
    expect(award_card(page, wolf)).to_contain_text("0 chosen")
    award_card(page, wolf).click()
    vote_for(page, found["w2"])

    assert list(Vote.objects.values_list("racer_id", flat=True)) == [found["w2"].pk]
