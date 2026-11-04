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
from django.utils import timezone

from awards.editing import set_ad_hoc_award, set_winner
from awards.models import Award
from awards.services import set_partition_list
from core.models import RaceState
from kiosks.models import Kiosk, Scene, SceneKiosk
from racing.models import PlaylistEntry
from roster.models import Racer
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
    "/awards/edit/",
    "/judging/",
    "/judging/board/",
    "/awards/present/",
    "/awards/present/current/",
    "/kiosks/",
    "/kiosks/board/",
    "/kiosks/scenes/",
    "/photos/racers/",
    "/photos/cars/",
    "/photos/head/list/",
    "/photos/car/racers/?photo=a.jpg",
    "/results/",
    "/results/rounds/",
    "/standings/",
    "/standings/list/",
    "/history/",
    "/export/",
    "/import/roster/",
    "/import/awards/",
    "/import/results/",
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
    """Groups of passed racers, each round 1 scheduled, rounds 1 and 2 of
    each group queued, each group's judged award, narrowed to its partition
    and given to its first racer, and each group's kiosk, checking in and
    named in the first scene, showing please-check-in for its group. With
    `race`, the first round races a heat, and the kiosk presents the first
    award."""
    rounds = list(race_setup(event, groups=groups).values())
    for round in rounds:
        award = Award.objects.create(
            event=event,
            name=f"Best {round.group.name}",
            award_type_id=1,
            group=round.group,
            sort=round.group.pk,
        )
        racers = Racer.objects.filter(event=event, group=round.group).order_by("pk")
        set_partition_list(event, [racers[0].partition], award=award)
        set_winner(event, award, racers[0])
        set_ad_hoc_award(event, racers[1], f"Shiniest {round.group.name}")
        name = f"TV {round.group.name}"
        Kiosk.objects.create(
            event=event,
            address=f"10.0.0.{round.group.pk}",
            name=name,
            page="kiosks/please-check-in.kiosk",
            params=f'{{"classids":[{round.group.pk}]}}',
            last_contact=timezone.now(),
        )
        first_scene = Scene.objects.filter(event=event).order_by("sort_order").first()
        SceneKiosk.objects.create(
            event=event, scene=first_scene, kiosk_name=name, page="kiosks/flag.kiosk"
        )
        if race and round is rounds[0]:
            RaceState.objects.filter(event=event).update(
                current_award_key=f"award-{award.pk}"
            )
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
