"""A page or a poll reads the same number of queries for a bigger race
(#44 retro). The #44 review found the playlist queue reading twice per step:
48 queries for 6 steps against 30 for 2. A poll runs every 2 s on every open
dashboard and check-in page, so a read per row grows with the race.

Each URL is read at two sizes of race day: groups, racers, scheduled rounds,
a raced heat and queued playlist steps. The count must not grow. Every URL a
template fetches with hx-get, polled or not, must be in the list when it takes
no argument (#70 retro): the #70 review found `/checkin/weigh/`, fetched when
the add sheet's partition changes, unmeasured.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from awards.editing import set_ad_hoc_award, set_winner
from awards.models import Award, BallotAward, Vote, Voter
from awards.services import set_partition_list
from core.models import RaceState
from kiosks import services as kiosk_services
from kiosks.models import Kiosk, Scene, SceneKiosk
from racing.models import PlaylistEntry
from roster.models import Racer
from roster.services.weigh_in import record_weigh_in, set_partition_weigh_in
from ui.tests.race import race_setup, run_heat, scheduled
from ui.tests.roles import PASSWORDS

pytestmark = pytest.mark.django_db

# The page loads, the polls and the argument-free hx-get fragments of every
# ui page. A new page adds its URLs; the first test names a missing one.
URLS = [
    "/dashboard/",
    "/dashboard/live/",
    "/checkin/",
    "/checkin/list/",
    "/checkin/number/",
    "/checkin/weigh/",
    "/groups/",
    "/playlist/",
    "/awards/edit/",
    "/judging/",
    "/judging/board/",
    "/judging/ballot/",
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
    "/timer/",
    "/timer/status/",
    "/timer/test/",
    "/timer/test/live/",
    "/timer/fake/",
    "/replay/",
    "/replay/camera/",
    "/ondeck/",
    "/slideshow/",
    "/vote/",
    "/ballot/status",
    "/kiosk/check-in/",
    "/kiosk/standings/",
    "/settings/",
    "/settings/roles/",
    "/setup/",
    "/setup/purge/?what=racers",
    "/setup/fake-roster/",
    "/setup/events/",
    "/about/",
    # Sheets and fragments a page fetches with hx-get.
    "/awards/edit/new/",
    "/awards/edit/partitions/",
    "/checkin/bulk/",
    "/checkin/racer/new/",
    "/checkin/qr/",
    "/dashboard/lanes/sheet/",
    "/dashboard/new-round/sheet/",
    "/dashboard/replay/settings/sheet/",
    "/groups/aggregate/new/",
    "/groups/group/new/",
    "/groups/partitions/",
    "/groups/rule/?rule=one-group",
    "/playlist/scheduling/",
    "/timer/settings/",
    *(
        f"/kiosk.php?page=kiosks/{name}.kiosk"
        for name in (
            "now-racing",
            "now-racing-columnar",
            "ondeck",
            "please-check-in",
            "results-by-racer",
            "standings",
            "award-presentations",
            "identify",
        )
    ),
]
# A fetched URL left out, with the reason.
NOT_MEASURED = {
    "/dashboard/results/sheet/": "names the heat on screen by its round's id",
}
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
# An hx-get that names a URL with no argument, polled or not.
FETCH = re.compile(r"hx-get=\"\{% url '([\w-]+)' %\}\"")


def test_every_fetched_url_is_measured():
    fetched = {
        reverse(name)
        for path in TEMPLATES.rglob("*.html")
        for name in FETCH.findall(path.read_text())
    }
    assert "/dashboard/live/" in fetched, "the hx-get pattern found no poll"
    measured = {urlsplit(url).path for url in URLS}
    assert fetched - measured - set(NOT_MEASURED) == set()


def race_day(event, groups: list[tuple[str, int]], race: bool) -> None:
    """Groups of passed racers, each round 1 scheduled, rounds 1 and 2 of
    each group queued, each group's judged award, narrowed to its partition,
    given to its first racer and on the ballot with two votes, each group's
    partition recording weights with a maximum and its first racer weighed
    over it (#70), and each group's kiosk, checking in and
    named in the first scene, showing please-check-in for its group, with a
    kiosk on each other page in `kiosks.services.kiosk_pages()` (#237). With
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
        set_partition_weigh_in(racers[0].partition, "recorded", "oz", "5")
        record_weigh_in(racers[0], weight="5.2")
        # On the ballot, with a vote from each of two voters (#54).
        BallotAward.objects.create(event=event, award=award, depth=2)
        for n, racer in enumerate(racers[:2]):
            voter = Voter.objects.create(event=event, identifier=f"{award.pk}-{n}")
            Vote.objects.create(event=event, voter=voter, award=award, racer=racer)
        name = f"TV {round.group.name}"
        Kiosk.objects.create(
            event=event,
            address=f"10.0.0.{round.group.pk}",
            name=name,
            page="kiosks/please-check-in.kiosk",
            params=f'{{"classids":[{round.group.pk}]}}',
            last_contact=timezone.now(),
        )
        # A kiosk per group on every other page, since the dashboard draws
        # each kiosk's page as a thumbnail (#237): reads they share are made
        # once per request (`ui.kiosk_pages.state.once`), so they do not grow
        # with the kiosks. A page added to the list is counted here too.
        for page in kiosk_services.kiosk_pages():
            if page["full"] == "kiosks/please-check-in.kiosk":
                continue
            Kiosk.objects.create(
                event=event,
                address=f"10.0.{round.group.pk}.{page['brief']}",
                page=page["full"],
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
