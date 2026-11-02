"""Coordinator payloads render the expected round groups, cards, heat text and
buttons (#41). This replaces `testing/puppeteer/coordinator-test.js`, whose
two hand-written polls are in `data/coordinator/`, and adds the polls PHP
recorded on the race day (`tests/golden/http/poll/race-day-polls.http`, #7).

The page renders the payload through `ui.dashboard.presenter.present`, which
reads no database, so a payload PHP captured renders as a live one does.
"""

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from ui.dashboard.presenter import present
from ui.dashboard.state import Shown

DATA = Path(__file__).parent / "data" / "coordinator"


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children: list[Node | str] = []

    def text(self) -> str:
        return " ".join(
            "".join(
                c if isinstance(c, str) else c.text() for c in self.children
            ).split()
        )

    def find_all(self, pred):
        for child in self.children:
            if isinstance(child, Node):
                if pred(child):
                    yield child
                yield from child.find_all(pred)


class Tree(HTMLParser):
    VOID = {"input", "img", "br", "meta", "link", "path", "rect", "circle"}

    def __init__(self, html):
        super().__init__()
        self.root = self.at = Node("root", [])
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.at)
        self.at.children.append(node)
        if tag not in self.VOID:
            self.at = node

    def handle_endtag(self, tag):
        node = self.at
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.at = node.parent

    def handle_data(self, data):
        self.at.children.append(data)


def render(payload) -> Node:
    context = present(payload)
    context.update(shown=Shown(), signature="x", oob=False)
    return Tree(render_to_string("ui/dashboard/live.html", context)).root


def has(attr):
    return lambda n: attr in n.attrs


def layout(root) -> dict[str, list[str]]:
    return {
        g.attrs["data-group"]: [
            c.attrs["data-roundid"] for c in g.find_all(has("data-roundid"))
        ]
        for g in root.find_all(has("data-group"))
    }


def buttons(root) -> list:
    """PHP's test lists each card's round id, its button count and labels."""
    found = []
    for card in root.find_all(has("data-roundid")):
        labels = [b.text() for b in card.find_all(has("data-button"))]
        found += [card.attrs["data-roundid"], len(labels), *labels]
    return found


def lanes(root) -> list[list[str]]:
    rows = []
    for li in root.find_all(has("data-lane")):
        cells = {
            key: next(iter(li.find_all(has(key))), None)
            for key in ("data-car", "data-name", "data-result")
        }
        rows.append(
            [
                li.attrs["data-lane"],
                cells["data-car"].text() if cells["data-car"] else "",
                cells["data-name"].text() if cells["data-name"] else "",
                cells["data-result"].text().removeprefix("Time ").removeprefix("Time")
                if cells["data-result"]
                else "",
            ]
        )
    return rows


def one(root, attr) -> str:
    return next(root.find_all(has(attr))).text()


def test_the_first_puppeteer_poll():
    root = render(json.loads((DATA / "puppeteer-first-poll.json").read_text()))
    assert layout(root) == {
        "now-racing": ["3"],
        "not-yet-scheduled": ["4", "5", "7"],
        "done-racing": ["1", "2"],
    }
    assert len(list(root.find_all(has("data-roundid")))) == 6
    assert lanes(root) == [
        ["1", "343", "Juan Jacobsen", ""],
        ["2", "139", "Jeffress Jamison", ""],
        ["3", "303", "Antoine Akiyama", ""],
        ["4", "", "", ""],
    ]
    assert one(root, "data-heat-title") == "Bears and Frèr, Round 1"
    assert one(root, "data-heat-text") == "Heat 3 of 4"
    current = next(root.find_all(lambda n: n.attrs.get("data-roundid") == "3"))
    assert one(current, "data-progress-text") == "4 heats scheduled, 2 run."
    # PHP shows the current round's buttons apart, with Add New Rounds; here
    # they stay on its card, and Add New Rounds follows the cards.
    assert buttons(root) == [
        "3", 1, "Repeat Round",
        "4", 1, "Schedule",
        "5", 1, "Schedule",
        "7", 0,
        "1", 2, "Make Changes", "Repeat Round",
        "2", 2, "Make Changes", "Repeat Round",
    ]  # fmt: skip
    assert one(root, "data-rounds").count("Add New Rounds") == 1
    switch = next(root.find_all(lambda n: n.attrs.get("role") == "switch"))
    assert switch.attrs["aria-checked"] == "true"


def test_the_master_schedule_puppeteer_poll():
    root = render(json.loads((DATA / "puppeteer-master-poll.json").read_text()))
    assert layout(root) == {
        "now-racing": ["2"],
        "master-schedule": ["-1"],
        "not-yet-scheduled": ["7", "5", "6", "8"],
        "done-racing": ["1", "3", "4"],
    }
    assert lanes(root) == [
        ["1", "282", "Willard Woolfolk", "3.977"],
        ["2", "207", "Blake Burling", "3.646"],
        ["3", "227", "Elliot Eastman", "2.295"],
        ["4", "222", "Dexter Dawes", "3.720"],
    ]
    assert one(root, "data-heat-title") == "White's Wolves, Round 1"
    current = next(root.find_all(lambda n: n.attrs.get("data-roundid") == "2"))
    assert one(current, "data-progress-text") == "13 heats scheduled, 13 run."
    assert buttons(root) == [
        "2", 1, "Repeat Round",
        "-1", 1, "Next Up",
        "7", 2, "Schedule", "Delete Round",
        "5", 0,
        "6", 0,
        "8", 2, "Schedule", "Delete Round",
        "1", 2, "Make Changes", "Repeat Round",
        "3", 2, "Make Changes", "Repeat Round",
        "4", 2, "Make Changes", "Repeat Round",
    ]  # fmt: skip
    assert "Add New Rounds" in one(root, "data-rounds")
    # Under the master schedule, rounds with no chart are flagged, and the
    # playlist controls are hidden, as PHP hides #playlist-group.
    flagged = [
        n.parent.parent.parent.attrs.get("data-roundid")
        for n in root.find_all(has("data-not-scheduled"))
    ]
    assert flagged == ["7", "5", "6", "8"]
    assert "Rounds Playlist" not in one(root, "data-rounds")


RACE_DAY = GOLDEN_DIR / "http" / "poll" / "race-day-polls.http"


def recorded(name):
    return json.loads(read_transcript(RACE_DAY)[name]["body"])


@pytest.mark.parametrize(
    ("exchange", "groups", "title", "heat_text", "racing"),
    [
        ("001 01-empty-database poll.coordinator", {}, "No round is racing", "", False),
        (
            "009 03-checked-in poll.coordinator",
            {"not-yet-scheduled": 2},
            "No round is racing",
            "",
            False,
        ),
        (
            "012 04-scheduled-and-racing-heat-1 poll.coordinator",
            {"now-racing": 1, "not-yet-scheduled": 1},
            None,
            "Heat 1 of",
            True,
        ),
        (
            "023 06-after-a-result poll.coordinator",
            {"now-racing": 1, "not-yet-scheduled": 1},
            None,
            "Heat 2 of",
            True,
        ),
    ],
)
def test_the_race_day_polls_php_recorded(exchange, groups, title, heat_text, racing):
    payload = recorded(exchange)
    root = render(payload)
    assert {k: len(v) for k, v in layout(root).items()} == groups
    if title:
        assert one(root, "data-heat-title") == title
    else:
        name = next(
            r["name"]
            for r in payload["rounds"]
            if r["roundid"] == payload["current-heat"]["roundid"]
        )
        assert one(root, "data-heat-title") == name
    assert one(root, "data-heat-text").startswith(heat_text)
    switch = next(root.find_all(lambda n: n.attrs.get("role") == "switch"))
    assert switch.attrs["aria-checked"] == ("true" if racing else "false")


def test_the_race_day_final_teed_up_offers_its_buttons():
    payload = recorded("027 07-between-rounds-final-teed-up poll.coordinator")
    root = render(payload)
    labels = buttons(root)
    for r in payload["rounds"]:
        assert str(r["roundid"]) in labels
    if any(r["heats_run"] for r in payload["rounds"]):
        assert "Repeat Round" in labels
