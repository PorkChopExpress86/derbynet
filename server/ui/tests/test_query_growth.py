"""A page or a poll reads the same number of queries for a bigger race
(#44 retro). The #44 review found the playlist queue reading twice per step:
48 queries for 6 steps against 30 for 2. A poll runs every 2 s on every open
dashboard and check-in page, so a read per row grows with the race.

Each URL is read at two sizes of race day: groups, racers, scheduled rounds,
a raced heat and queued playlist steps. The count must not grow. Every URL a
template polls must be in the list.
"""

import re
from pathlib import Path

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from racing.models import PlaylistEntry
from ui.tests.race import race_setup, run_heat, scheduled
from ui.tests.roles import PASSWORDS

pytestmark = pytest.mark.django_db

# The page loads and the polls of every ui page. A new page adds its URLs.
URLS = [
    "/dashboard/",
    "/dashboard/live/",
    "/checkin/",
    "/checkin/list/",
    "/checkin/number/",
    "/groups/",
    "/playlist/",
]
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
# An element whose hx-get names a URL and whose trigger polls.
POLL = re.compile(
    r"<[^>]*hx-get=\"\{% url '([\w-]+)' %\}\"[^>]*(?:poll_trigger|every )"
)


def test_every_polled_url_is_measured():
    polled = {
        reverse(name)
        for path in TEMPLATES.rglob("*.html")
        for name in POLL.findall(path.read_text())
    }
    assert polled, "the poll pattern found no poll"
    assert polled - set(URLS) == set()


def race_day(event, groups: list[tuple[str, int]], race: bool) -> None:
    """Groups of passed racers, each round 1 scheduled, and rounds 1 and 2
    of each group queued. With `race`, the first round races a heat."""
    rounds = list(race_setup(event, groups=groups).values())
    for i, round in enumerate(rounds):
        scheduled(round, race=race and i == 0)
    if race:
        run_heat(event)
    seq = PlaylistEntry.objects.filter(event=event).count()
    for round in rounds:
        for number in (1, 2):
            seq += 1
            PlaylistEntry.objects.create(
                event=event,
                seq=seq,
                group=round.group,
                round_number=number,
                bucket_limit=0 if number == 1 else 3,
            )


@pytest.fixture
def coordinator(event):
    client = Client()
    response = client.post(
        "/login/", {"name": "RaceCoordinator", "password": PASSWORDS["RaceCoordinator"]}
    )
    assert response.status_code == 302
    return client


def reads(client, url: str) -> int:
    with CaptureQueriesContext(connection) as seen:
        response = client.get(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 200, (url, response.status_code)
    return len(seen)


@pytest.mark.parametrize("url", URLS)
def test_a_bigger_race_reads_no_more(coordinator, event, url):
    race_day(event, [("G1", 4), ("G2", 4)], race=True)
    reads(coordinator, url)  # the first read fills the per-process caches
    small = reads(coordinator, url)
    race_day(event, [("G3", 6), ("G4", 6)], race=False)
    assert reads(coordinator, url) == small
