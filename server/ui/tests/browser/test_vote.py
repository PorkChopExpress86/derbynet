"""The vote page in a browser (#54), on a phone and a desktop.

- With no voting code the page asks for one, a bad vote link says the code
  is not valid, a racer's vote link opens the ballot (#206, D86), two
  votes fill a depth-2 award, and the third car's toggle is disabled with
  its reason.
- The Voting code sheet on the check-in page shows the racer's QR code and
  link; a phone opening the link votes, and a second phone (a new browser
  context) opening it shows the same choices (#206, D86).
- Closing balloting from another session turns the page to its closed state
  within 6 s with no reload, and opening it again brings the ballot back.
- A racer outside an award's group is not in that award's grid.
- Votes from an earlier visit show on the award card, the sort control keeps
  `?order=` on this page (C33), a refused vote names the reason in a toast,
  and a ballot with no award says so.
- A status poll sent before an award opened and answered after leaves the
  award open (#220).
- The judging page's ballot sheet opens balloting and sets an award's votes
  per ballot, and has no ballot password field (D86).
- The screenshots of every state at both sizes.
"""

import json
import re
import uuid

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import expect

from awards.models import Award, BallotAward, Vote
from core.models import RaceState
from roster.models import Group, Racer
from roster.services import groups
from roster.services.racers import insert_new_racer, pass_racer
from ui.tests.browser.conftest import (
    SIZES,
    assert_accessible,
    htmx_post,
    shoot,
    stop_timers,
)
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
    """Wolves and Bears, a group each (by partition), and a voter: a racer of
    a group of its own who has not passed check-in, so no award grid shows
    it, whose voting token the page votes with (#206)."""
    for name in ("Wolves", "Bears", "Families"):
        groups.add_partition(event, name)
    return {
        "w1": racer(event, "Ava", "Wolves", 101),
        "w2": racer(event, "Ben", "Wolves", 102),
        "w3": racer(event, "Cal", "Wolves", 103),
        "b1": racer(event, "Dee", "Bears", 201),
        "voter": insert_new_racer(
            event,
            first_name="Vera",
            last_name="Voter",
            car_number=301,
            partition_name="Families",
        ).racer,
        "wolves": Group.objects.get(event=event, name="Wolves"),
    }


def enter(page, found, query=""):
    """Open the vote page as a QR code does: the voter's vote link, which
    keeps the token in the session and redirects to /vote/ (#206)."""
    page.goto(f"/vote/code/{found['voter'].voting_token}")
    expect(page).to_have_url(re.compile(r"/vote/$"))
    if query:
        page.goto(f"/vote/{query}")


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


def test_a_vote_link_then_two_votes_fill_a_depth_two_award(page, event):
    """The phone acceptance check, at both sizes."""
    found = pack(event)
    best = ballot_award(event, "Best in Show", 2)
    set_open(event)

    page.goto("/vote/")
    expect(state(page)).to_have_attribute("data-vote-state", "code")
    expect(
        page.get_by_role("heading", name="Scan a racer's voting code to vote")
    ).to_be_visible()
    assert_accessible(page)
    shoot(page, "vote-code")
    enter(page, found)

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


def test_a_bad_vote_link_says_the_code_is_not_valid(page, event, page_errors):
    """The page answers 404, which Chromium logs to the console; that log is
    allowed here only, and the one 404 must be the bad link's."""
    page_errors.allow(r"status of 404 \(Not Found\)$")
    missing = []
    page.on("response", lambda r: r.status == 404 and missing.append(r.url))

    page.goto("/vote/code/not-a-code")

    expect(
        page.get_by_role("heading", name="This voting code is not valid")
    ).to_be_visible()
    expect(
        page.get_by_text("Ask the check-in table for your racer's code.")
    ).to_be_visible()
    assert_accessible(page)
    shoot(page, "vote-bad-code")
    assert [url.removeprefix(page.url.split("/vote/")[0]) for url in missing] == [
        "/vote/code/not-a-code"
    ]


def test_closing_from_another_session_shows_closed_within_six_seconds(page, event):
    found = pack(event)
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    enter(page, found)
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
    enter(page, found)

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
    enter(page, found)
    award_card(page, best).click()
    vote_for(page, found["w3"])

    page.reload()

    card = award_card(page, best)
    expect(card).to_contain_text("1 chosen")
    expect(card.locator(f'[data-chosen-racer="{found["w3"].pk}"]')).to_have_text("103")
    assert_accessible(page)
    shoot(page, "vote-awards")


def expect_grid_order(page, racers):
    """The grid shows `racers` in this order: each card's `order` style is
    its place, read again until it matches."""
    for place, r in enumerate(racers):
        expect(page.locator(f'[data-racer="{r.pk}"]')).to_have_css("order", str(place))


def test_the_sort_control_orders_the_grid_and_keeps_the_url(page, event):
    found = pack(event)
    # First names that put each order apart: by name Ava Ben Cal Dee is
    # 102 201 103 101, by class (Wolves first, then by name) 102 103 101
    # 201, and by car 101 102 103 201. pack()'s own names give one order for
    # all three, so a sort that changed nothing passed (#220).
    for key, first in (("w1", "Dee"), ("w2", "Ava"), ("w3", "Cal"), ("b1", "Ben")):
        Racer.objects.filter(pk=found[key].pk).update(first_name=first)
    w1, w2, w3, b1 = (found[key] for key in ("w1", "w2", "w3", "b1"))
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    enter(page, found, "?order=name")
    award_card(page, best).click()

    # Alpine shows the award view on the next animation frame, after the
    # click returns, so a read right after the click can find no car (PR
    # #223; #220). Each check here retries.
    expect(page.locator("[data-racer-cards] [data-car]:visible")).to_have_count(4)
    expect_grid_order(page, [w2, b1, w3, w1])
    page.locator('[data-sort] input[value="class"]').check(force=True)
    expect(page).to_have_url(re.compile(r"/vote/\?order=class$"))
    expect_grid_order(page, [w2, w3, w1, b1])
    page.locator('[data-sort] input[value="car"]').check(force=True)
    expect(page).to_have_url(re.compile(r"/vote/\?order=car$"))
    expect_grid_order(page, [w1, w2, w3, b1])
    assert_accessible(page)
    shoot(page, "vote-grid")
    car_card(page, b1).click()
    expect(page.locator("[data-sheet-details]")).to_have_text("Bears")
    assert_accessible(page)
    shoot(page, "vote-sheet", full_page=False)


# Counts each finished poll(), whatever it did, and records every change to
# the page's state or to whether the award view shows.
WATCH_POLLS = """() => {
  const store = Alpine.store('vote');
  const poll = store.poll;
  window.__polls = 0;
  store.poll = async function () {
    try { await poll.call(this); } finally { window.__polls++; }
  };
  window.__changes = [];
  const vote = document.querySelector('[data-vote]');
  const view = document.querySelector('[data-award-view]');
  new MutationObserver(() => {
    window.__changes.push(`state=${vote.dataset.voteState}`);
  }).observe(vote, {attributeFilter: ['data-vote-state']});
  new MutationObserver(() => {
    window.__changes.push(`view=${getComputedStyle(view).display}`);
  }).observe(view, {attributeFilter: ['style']});
}"""


def test_a_status_poll_sent_before_the_award_opened_leaves_it_open(page, event):
    """The other suspect of #220: a `/ballot/status` poll sent from the
    award list, answered after the visitor opened an award. Held until then,
    the server's own answer is still `open`, the same as the page's, so
    `poll()` returns at once: it neither reloads the ballot (which hides the
    grid while `ballot.get` is out) nor calls `closeAward()`."""
    found = pack(event)
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    held = []
    page.route(
        "**/ballot/status",
        lambda route: route.continue_() if held else held.append(route),
    )
    enter(page, found)
    # The first poll goes out 5 s after Alpine starts, well after this.
    page.evaluate(WATCH_POLLS)
    expect(award_card(page, best)).to_be_visible()
    for _ in range(140):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "no /ballot/status poll in 7 s"

    award_card(page, best).click()
    cars = page.locator("[data-racer-cards] [data-car]:visible")
    expect(cars).to_have_count(4)
    page.evaluate("window.__changes = []")
    with page.expect_response("**/ballot/status") as answer:
        held[0].continue_()
    assert answer.value.json() == {"balloting": "open"}
    page.wait_for_function("window.__polls >= 1")
    # x-show writes its style on the next frame.
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => setTimeout(done)))"
    )
    # One read, not a retrying expect: a reload or a closeAward() that is
    # undone by the time an expect retries is the fault looked for here.
    assert page.evaluate("window.__changes") == []
    expect(page.locator("[data-award-view]")).to_be_visible()
    expect(cars).to_have_text(["101", "102", "103", "201"])
    car_card(page, found["w1"]).click()
    expect(toggle(page)).to_be_enabled()


def test_a_refused_vote_names_the_reason(page, event):
    """The server refused it: the toast says why, and nothing is chosen."""
    found = pack(event)
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    enter(page, found)
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
    found = pack(event)
    set_open(event)
    enter(page, found)

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
    expect(sheet.get_by_label("Ballot password")).to_have_count(0)
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
    enter(page, found)
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


def phone_context(browser, live_server, page_errors):
    """A phone of its own, as a parent's is: a new browser context, which
    shares no cookie with the page or with another phone."""
    context = browser.new_context(**SIZES["phone"], base_url=live_server.url)
    phone = context.new_page()
    phone.on("pageerror", lambda error: page_errors.add(f"pageerror: {error}"))
    return context, phone


def put_away(context, phone):
    """Stop the phone's polls and let what is out land before it closes."""
    stop_timers(phone)
    phone.wait_for_load_state("networkidle")
    context.close()


def test_the_voting_code_opens_one_ballot_on_every_phone(
    page, event, live_server, browser, page_errors
):
    """#206 acceptance: the check-in card's Voting code sheet shows the QR
    code and its link; a phone opening the link votes, and a second phone
    opening the same link shows that vote and edits the same ballot."""
    found = pack(event)
    best = ballot_award(event, "Best in Show", 2)
    set_open(event)
    logged_in_as(page, live_server, "RaceCrew")
    page.goto("/checkin/")
    page.locator(
        f'[data-racer="{found["voter"].pk}"] [data-action="voting-code"]'
    ).click()
    sheet = page.locator("[data-voting-code]")
    expect(sheet).to_be_visible()
    expect(sheet.locator("svg")).to_be_visible()
    link = sheet.locator("[data-voting-url]")
    expect(link).to_have_text(
        f"{live_server.url}/vote/code/{found['voter'].voting_token}"
    )
    assert_accessible(page)
    shoot(page, "checkin-voting-code", full_page=False)
    url = link.inner_text()

    first_context, first = phone_context(browser, live_server, page_errors)
    first.goto(url)
    expect(first).to_have_url(re.compile(r"/vote/$"))
    award_card(first, best).click()
    vote_for(first, found["w1"])
    put_away(first_context, first)

    second_context, second = phone_context(browser, live_server, page_errors)
    second.goto(url)
    card = award_card(second, best)
    expect(card).to_contain_text("1 chosen")
    expect(card.locator(f'[data-chosen-racer="{found["w1"].pk}"]')).to_have_text("101")
    card.click()
    vote_for(second, found["w2"])
    put_away(second_context, second)

    assert sorted(Vote.objects.values_list("racer__car_number", flat=True)) == [
        101,
        102,
    ]
    assert Vote.objects.values("voter").distinct().count() == 1


def test_a_code_refused_mid_visit_asks_for_a_code_again(page, event):
    """The session's code stops naming a racer while the page is open (here
    its racer's token changes, as a deleted racer's code names none): the
    next vote is refused with no-token (D86), the toast says the code is no longer
    valid, and the page asks for a code; no vote is stored."""
    found = pack(event)
    best = ballot_award(event, "Best in Show", 1)
    set_open(event)
    enter(page, found)
    award_card(page, best).click()
    car_card(page, found["w1"]).click()
    Racer.objects.filter(pk=found["voter"].pk).update(voting_token=uuid.uuid4())

    with page.expect_response(lambda r: r.request.method == "POST") as reply:
        toggle(page).click()

    assert reply.value.json()["outcome"]["code"] == "no-token"
    expect(page.locator("[data-toast]")).to_contain_text(
        "This voting code is no longer valid. Scan a racer's voting code again."
    )
    expect(state(page)).to_have_attribute("data-vote-state", "code")
    expect(page.locator('[data-sheet="vote-racer"]')).to_be_hidden()
    expect(page.locator("[data-award-view]")).to_be_hidden()
    assert not Vote.objects.exists()
