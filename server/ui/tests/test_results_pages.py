"""Results by racer, standings, the Retrospective and the export page (#49),
read as a visitor's and a race official's browser reads them."""

import re

import pytest
from django.utils import timezone

from core.models import ActionLog, RaceSettings
from racing.engine.results import DID_NOT_FINISH
from racing.models import HeatLane
from racing.services.standings import standings_snapshot
from roster.models import Racer
from ui.tests.race import race_round, race_setup, run_heat, scheduled, standings_day

pytestmark = pytest.mark.django_db


def cards(html: str, attribute: str = "data-racer") -> list[str]:
    return re.findall(rf'{attribute}="(\d+)"', html)


# ----- results by racer --------------------------------------------------------


@pytest.fixture
def raced(event):
    wolves = race_setup(event, groups=(("Wolves", 4), ("Bears", 4)))
    scheduled(wolves["Wolves"])
    run_heat(event)
    return wolves


def test_results_list_each_racer_of_each_round_with_their_heats(login, event, raced):
    html = login("anonymous").get("/results/").content.decode()

    assert "Wolves, Round 1" in html and "Bears, Round 1" in html
    lane = HeatLane.objects.filter(event=event, finish_time__isnull=False).first()
    assert f'data-result="{lane.pk}"' in html
    assert f"{lane.finish_time:5.3f}" in html
    assert "Heat 1 · Lane" in html
    assert "--" in html  # heats with no result yet
    assert "Now racing" in html


def test_results_show_places_under_points_racing(login, event, raced):
    RaceSettings.objects.filter(event=event).update(use_points=True)

    html = login("anonymous").get("/results/").content.decode()

    assert re.search(r"data-shown>\s*1st", html)


def test_a_dnf_time_shows_dnf_beside_it(login, event, raced):
    lane = HeatLane.objects.filter(event=event, finish_time__isnull=False).first()
    HeatLane.objects.filter(pk=lane.pk).update(finish_time=DID_NOT_FINISH)

    html = login("anonymous").get("/results/").content.decode()

    assert re.search(r"data-dnf>DNF</span>9\.999", html)


def test_racerid_keeps_one_racer(login, event, raced):
    racer = Racer.objects.filter(event=event, group__name="Wolves").first()

    html = login("anonymous").get(f"/results/?racerid={racer.pk}").content.decode()

    assert set(cards(html)) == {str(racer.pk)}
    assert "View all racers" in html


@pytest.mark.parametrize(
    "junk", ["1 OR 1=1", "1;DROP TABLE x", "-3", "abc", "99999999999"]
)
def test_s13_a_racerid_that_is_not_a_number_shows_every_racer(
    login, event, raced, junk
):
    """PHP put the raw racerid into its SQL (S13)."""
    response = login("anonymous").get("/results/", {"racerid": junk})

    assert response.status_code == 200
    assert len(set(cards(response.content.decode()))) == 4


def test_the_old_url_redirects_with_its_racerid(login, event, raced):
    response = login("anonymous").get("/racer-results.php?racerid=4")
    assert response.status_code == 301
    assert response["Location"] == "/results/?racerid=4"


def test_the_poll_answers_204_until_a_heat_finishes(login, event, raced):
    client = login("anonymous")
    html = client.get("/results/rounds/").content.decode()
    sig = re.search(r'name="sig" value="(\w+)"', html).group(1)
    assert client.get("/results/rounds/", {"sig": sig}).status_code == 204

    run_heat(event)

    assert client.get("/results/rounds/", {"sig": sig}).status_code == 200


# ----- standings ---------------------------------------------------------------


@pytest.fixture
def day(event):
    return standings_day(event)


def standings_list(client, view: str = "") -> str:
    return client.get("/standings/list/", {"view": view}).content.decode()


def listed(html: str) -> list[tuple[str, str, bool]]:
    """(car, place, marked not eligible) per card, in order."""
    out = []
    for card in re.findall(r"<li class=\"flex min-w-0 gap-3.*?</li>\n  ", html, re.S):
        car = re.search(r"data-car>(\d+)<", card).group(1)
        place = re.search(r"data-place>(.*?)</span>\s*<div", card, re.S).group(1)
        place = "" if "No place" in place else place.strip()
        out.append((car, place, "data-not-eligible" in card))
    return out


def test_the_standings_are_public_and_list_the_catalog(login, event, day):
    html = login("anonymous").get("/standings/").content.decode()

    assert "Averaging all heat times" in html
    for name in ("Pack", "Lions", "Tigers", "Parents"):
        assert f">{name}</option>" in html


def test_d33_racers_not_eligible_show_with_no_place(login, event, day):
    client = login("anonymous")
    standings = standings_snapshot(event).standings

    rows = listed(standings_list(client, "st-ff"))

    assert ("102", "", True) in rows and ("202", "", True) in rows
    places = [p for _, p, marked in rows if not marked]
    assert places == [str(n) for n in range(1, len(places) + 1)]
    ladder = standings.ladders["st-ff"]
    assert [p for p in places] == [str(i + 1) for i, _ in enumerate(ladder)]


def test_d35_the_pack_leaves_out_parents_and_their_group_places_them(login, event, day):
    client = login("anonymous")

    pack = listed(standings_list(client, "st-ff"))
    parents = [r for r in pack if r[0].startswith("3")]
    assert len(parents) == 4 and all(p == "" and marked for _, p, marked in parents)
    numbered = [p for _, p, marked in pack if not marked]
    assert numbered == ["1", "2", "3", "4", "5", "6"]

    round_id = day["rounds"]["Parents"].pk
    group = listed(standings_list(client, f"st-r{round_id}"))
    assert [p for _, p, _ in group] == ["1", "2", "3", "4"]
    assert not any(marked for _, _, marked in group)
    assert "partition is not in the award&#x27;s list" in standings_list(
        client, "st-ff"
    )


def test_d37_a_closed_lanes_times_show_dropped(login, event):
    found = standings_day(event, close_lane_3=True)
    round_id = found["rounds"]["Lions"].pk
    client = login("anonymous")

    html = standings_list(client, f"st-r{round_id}")

    lane_3 = HeatLane.objects.filter(round_id=round_id, lane=3).exclude(
        finish_time=None
    )
    assert lane_3.exists()
    dropped = re.findall(r'title="Heat (\d+), lane (\d+), dropped[^"]*"', html)
    assert {int(lane) for _, lane in dropped} == {3}
    assert len(dropped) == lane_3.count()
    snapshot = standings_snapshot(event)
    expected = [
        str(row.outcome[f"st-r{round_id}"])
        for row in snapshot.standings.rows
        if f"st-r{round_id}" in row.outcome
    ]
    assert [p for _, p, marked in listed(html) if not marked] == expected


def test_the_select_pushes_the_page_url(login, event, day):
    round_id = day["rounds"]["Lions"].pk
    response = login("anonymous").get("/standings/list/", {"view": f"st-r{round_id}"})

    assert response["HX-Push-Url"] == f"/standings/?view=st-r{round_id}"


def test_an_unknown_view_shows_the_first(login, event, day):
    html = login("anonymous").get("/standings/?view=<script>").content.decode()

    assert 'value="st-ff" selected' in html


# ----- the Retrospective -------------------------------------------------------


def test_the_retrospective_shows_lane_bias_and_the_timeline(login, event):
    race_setup(event, groups=(("Wolves", 8),))
    from racing.models import Round

    scheduled(Round.objects.get(event=event))
    race_round(event)
    # The timer's messages are logged by `action.php`, not the services.
    ActionLog.objects.create(
        event=event,
        request={"action": "timer-message", "message": "FINISHED", "lane1": "3.1"},
    )
    racer = Racer.objects.get(event=event, car_number=101)
    ActionLog.objects.create(
        event=event, request={"action": "racer.delete", "racer": racer.pk}
    )

    html = login("RaceCrew").get("/history/").content.decode()

    assert re.search(
        r"There is no evidence of significant lane bias\.|appear to be biased", html
    )
    assert re.search(r"F statistic is \d\.\d{3} with df1=3 and df2=\d+", html)
    assert re.search(r"\d\.\d{3} sample variance\.", html)
    assert "Heat finished: lane1: 3.1s" in html
    assert "Delete racer <b>Wolves1 Racer</b>, 101, Wolves" in html
    assert "data-debug-link" not in html


def test_the_debug_view_is_for_set_up(login, event):
    ActionLog.objects.create(
        event=event, received=timezone.now(), request={"action": "photo.crop"}
    )

    crew = login("RaceCrew").get("/history/?debug").content.decode()
    coordinator = login("RaceCoordinator").get("/history/?debug").content.decode()

    assert "photo.crop" not in crew
    assert "&quot;action&quot;: &quot;photo.crop&quot;" in coordinator


def test_the_retrospective_needs_check_in(login, event):
    assert login("anonymous").get("/history/").status_code == 302
    assert login("Photo").get("/history/").status_code == 302


def test_a_dnf_time_shows_dnf_on_the_standings(login, event, day):
    lions = day["rounds"]["Lions"]
    lane = HeatLane.objects.filter(round=lions, racer__car_number=104).first()
    HeatLane.objects.filter(pk=lane.pk).update(finish_time=DID_NOT_FINISH)

    html = standings_list(login("anonymous"), f"st-r{lions.pk}")

    assert re.search(r"data-time>DNF 9\.999<", html)
