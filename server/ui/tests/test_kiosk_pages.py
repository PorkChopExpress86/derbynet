"""The kiosk display pages (#52): `kiosk.php`, its preview, the registry's 15
pages, and the PHP defects the port fixes (S12, S28, S29, C34)."""

import json
import re
from pathlib import Path

import pytest
from django.test import Client

from accounts.permissions import Perm
from core.models import RaceSettings
from kiosks.models import Kiosk
from kiosks.pages import PAGES
from roster.models import Group, Racer
from ui.tests.race import race_setup, standings_day

pytestmark = pytest.mark.django_db


def body(response) -> str:
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def shown_page(html: str) -> str:
    return re.search(r'<body class="kiosk-page" data-page="([^"]+)"', html).group(1)


def config(html: str) -> dict:
    found = re.search(
        r'<script id="kiosk-config" type="application/json">(.*?)</script>', html
    )
    return json.loads(found.group(1))


# ----- kiosk.php ----------------------------------------------------------------


def test_no_address_and_no_page_redirects_to_this_kiosks_address(event):
    client = Client()
    response = client.get("/kiosk.php")

    assert response.status_code == 302
    address = response["Location"].split("?address=", 1)[1]
    assert response["Location"].startswith("/kiosk.php?address=")
    # The session keeps the address, so the next visit gets the same one.
    assert client.get("/kiosk.php")["Location"].endswith(address)


def test_the_debian_prefix_answers_too(event):
    response = Client().get("/derbynet/kiosk.php")

    assert response.status_code == 302
    assert response["Location"].startswith("/derbynet/kiosk.php?address=")


def test_a_new_address_registers_and_shows_identify(event):
    html = body(Client().get("/kiosk.php", {"address": "tv-1"}))

    assert shown_page(html) == "kiosks/identify.kiosk"
    kiosk = Kiosk.objects.get(event=event, address="tv-1")
    assert kiosk.last_contact is not None
    assert config(html)["address"] == "tv-1"
    assert config(html)["loaded"] == "kiosks/identify.kiosk"
    assert "tv-1" in html


def test_the_kiosks_page_and_parameters_render(event):
    Kiosk.objects.create(
        event=event,
        address="tv-1",
        name="Main",
        page="kiosks/qrcode.kiosk",
        params='{"title":"Scan me","content":"https://example.org"}',
        reload_requested=True,
    )

    html = body(Client().get("/kiosk.php", {"address": "tv-1"}))

    assert shown_page(html) == "kiosks/qrcode.kiosk"
    assert config(html)["parameters"] == {
        "title": "Scan me",
        "content": "https://example.org",
    }
    assert ">Scan me<" in html
    assert not Kiosk.objects.get(address="tv-1").reload_requested


def test_a_stored_page_outside_the_registry_shows_identify_and_keeps_its_string(
    event,
):
    """kiosk.assign stores any page string, as PHP's does. The kiosk shows
    identify, and kiosk.js compares the stored string, so it never loops."""
    Kiosk.objects.create(
        event=event, address="tv-1", name="Aux2", page="local/kiosks/feedback.kiosk"
    )

    html = body(Client().get("/kiosk.php", {"address": "tv-1"}))

    assert shown_page(html) == "kiosks/identify.kiosk"
    assert config(html)["loaded"] == "local/kiosks/feedback.kiosk"


@pytest.mark.parametrize(
    "page", ["../settings.php", "kiosks/../../settings.php", "kiosks/nope.kiosk"]
)
def test_a_preview_of_a_path_outside_the_registry_renders_identify(event, page):
    html = body(Client().get("/kiosk.php", {"page": page}))

    assert shown_page(html) == "kiosks/identify.kiosk"


def test_a_preview_registers_no_kiosk(event):
    html = body(
        Client().get(
            "/kiosk.php",
            {"page": "kiosks/qrcode.kiosk", "parameters": '{"title":"Hi"}'},
        )
    )

    assert shown_page(html) == "kiosks/qrcode.kiosk"
    assert config(html)["address"] is None
    assert config(html)["parameters"]["title"] == "Hi"
    assert not Kiosk.objects.filter(event=event).exists()


def _session_permissions(client) -> object:
    from django.contrib.sessions.backends.db import SessionStore

    key = client.cookies.get("sessionid")
    if key is None:
        return None
    store = SessionStore(session_key=key.value)
    return {k: v for k, v in store.load().items() if "perm" in k.lower()}


def test_s28_loading_kiosk_php_adds_no_permission(event):
    client = Client()
    for query in (
        {},
        {"address": "tv-1"},
        {"page": "kiosks/award-presentations.kiosk"},
    ):
        client.get("/kiosk.php", query)

    assert not _session_permissions(client)
    # So a kiosk visitor still cannot reach what PRESENT_AWARDS guards.
    assert client.get("/kiosks/").status_code in (302, 403)
    reply = client.post("/action.php", {"action": "award.present", "key": ""})
    assert reply.json()["outcome"]["code"] == "notauthorized"
    assert Perm.PRESENT_AWARDS


def test_s28_the_award_kiosk_clears_no_award_on_load(event):
    html = body(
        Client().get("/kiosk.php", {"page": "kiosks/award-presentations.kiosk"})
    )

    assert "award.present" not in html
    script = Path(__file__).parents[1] / "static" / "ui" / "kiosk-awards.js"
    assert "award.present" not in script.read_text()


def test_c34_the_award_kiosk_plays_only_the_webm_confetti(event):
    html = body(
        Client().get("/kiosk.php", {"page": "kiosks/award-presentations.kiosk"})
    )

    assert "confetti.webm" in html and "confetti.mov" not in html


def test_every_kiosk_view_allows_same_origin_framing(event):
    for url in ("/kiosk.php?page=kiosks/now-racing.kiosk", "/fullscreen.php"):
        assert Client().get(url)["X-Frame-Options"] == "SAMEORIGIN"


# ----- the registry's pages ---------------------------------------------------------


@pytest.fixture
def raced(event):
    return standings_day(event)


@pytest.mark.parametrize("key", sorted(PAGES))
def test_each_registry_page_renders_for_a_fixture_database(event, raced, key):
    Kiosk.objects.create(event=event, address="tv", page=key)

    by_address = body(Client().get("/kiosk.php", {"address": "tv"}))
    preview = body(Client().get("/kiosk.php", {"page": key}))

    assert shown_page(by_address) == shown_page(preview) == key


def test_the_standalone_pages_and_fullscreen(event, raced):
    for url, key in (("/ondeck/", "ondeck"), ("/slideshow/", "slideshow")):
        html = body(Client().get(url))
        assert shown_page(html) == f"kiosks/{key}.kiosk"
        assert config(html)["address"] is None
        assert "data-standalone" in html
    assert Client().get("/ondeck.php")["Location"] == "/ondeck/"
    html = body(Client().get("/fullscreen.php"))
    assert 'value="/kiosk.php"' in html


# ----- please-check-in ---------------------------------------------------------------


def _check_in_day(event):
    race_setup(event, groups=(("Lions", 2), ("Tigers", 2)))
    Racer.objects.filter(event=event, car_number__in=[101, 201]).update(
        passed_inspection=False
    )
    return {g.name: g.pk for g in Group.objects.filter(event=event)}


def test_please_check_in_lists_racers_not_checked_in_by_group(event):
    groups = _check_in_day(event)
    racers = Racer.objects.filter(event=event, passed_inspection=False)
    html = body(Client().get("/kiosk.php", {"page": "kiosks/please-check-in.kiosk"}))

    assert re.findall(r"<h2>(\w+)</h2>", html) == ["Lions", "Tigers"]
    for r in racers:
        assert f"{r.last_name}, {r.first_name}" in html

    narrowed = body(
        Client().get(
            "/kiosk/check-in/",
            {"parameters": f'{{"classids":[{groups["Tigers"]}]}}'},
        )
    )
    assert re.findall(r"<h2>(\w+)</h2>", narrowed) == ["Tigers"]


@pytest.mark.parametrize(
    "parameters",
    ['{"classids":["1) OR 1=1 --"]}', '{"classids":"1) OR 1=1"}', '{"classids":[1.5]}'],
)
def test_s12_a_forged_classids_filters_nothing_and_never_reaches_sql(event, parameters):
    _check_in_day(event)
    Kiosk.objects.create(
        event=event,
        address="tv",
        page="kiosks/please-check-in.kiosk",
        params=parameters,
    )

    for response in (
        Client().get("/kiosk.php", {"address": "tv"}),
        Client().get(
            "/kiosk.php",
            {"page": "kiosks/please-check-in.kiosk", "parameters": parameters},
        ),
        Client().get("/kiosk/check-in/", {"parameters": parameters}),
    ):
        assert re.findall(r"<h2>(\w+)</h2>", body(response)) == ["Lions", "Tigers"]


def test_please_check_in_shows_the_emblem_once_everyone_is_in(event):
    race_setup(event)
    html = body(Client().get("/kiosk.php", {"page": "kiosks/please-check-in.kiosk"}))

    assert "All Racers Have Checked In" in html
    assert "data-all-in" in html


# ----- standings -------------------------------------------------------------------


def standings_rows(html: str) -> list[tuple[str, str, bool]]:
    """(car, place, marked not eligible) per row, in order."""
    out = []
    for row in re.findall(r'<li class="k-row".*?</li>', html, re.S):
        car = re.search(r"data-car>(\d+)<", row).group(1)
        place = re.search(r"data-place>(.*?)</span>\s*<span data-car", row, re.S)
        place = "" if "No place" in place.group(1) else place.group(1).strip()
        out.append((car, place, "data-not-eligible" in row))
    return out


def kiosk_standings(key: str) -> str:
    params = f'{{"key":"{key}"}}'
    return body(
        Client().get(
            "/kiosk.php", {"page": "kiosks/standings.kiosk", "parameters": params}
        )
    )


def test_s29_the_standings_kiosk_is_public(event, raced):
    """Kept public on purpose (D5, #15): an anonymous visitor sees it."""
    assert standings_rows(kiosk_standings("st-ff"))


def test_d33_racers_not_eligible_show_marked_with_no_place(event, raced):
    from racing.services.standings import standings_snapshot

    rows = standings_rows(kiosk_standings("st-ff"))

    assert ("102", "", True) in rows and ("202", "", True) in rows
    ladder = standings_snapshot(event).standings.ladders["st-ff"]
    places = [p for _, p, marked in rows if not marked]
    assert places == [str(i + 1) for i, _ in enumerate(ladder)]


def test_d35_parents_outside_the_pack_list_have_no_place_and_others_close_up(
    event, raced
):
    pack = standings_rows(kiosk_standings("st-ff"))

    parents = [r for r in pack if r[0].startswith("3")]
    assert len(parents) == 4 and all(p == "" and marked for _, p, marked in parents)
    assert [p for _, p, marked in pack if not marked] == ["1", "2", "3", "4", "5", "6"]
    group = standings_rows(kiosk_standings(f"st-r{raced['rounds']['Parents'].pk}"))
    assert [p for _, p, _ in group] == ["1", "2", "3", "4"]


def test_d37_a_closed_lanes_times_show_dropped(event):
    from racing.models import HeatLane
    from racing.services.standings import standings_snapshot

    found = standings_day(event, close_lane_3=True)
    round_id = found["rounds"]["Lions"].pk
    html = kiosk_standings(f"st-r{round_id}")

    dropped = re.findall(r'title="Heat (\d+), lane (\d+), dropped', html)
    lane_3 = HeatLane.objects.filter(round_id=round_id, lane=3).exclude(
        finish_time=None
    )
    assert {lane for _, lane in dropped} == {"3"}
    assert len(dropped) == lane_3.count()
    key = f"st-r{round_id}"
    expected = [
        str(row.outcome[key])
        for row in standings_snapshot(event).standings.rows
        if key in row.outcome
    ]
    assert [p for _, p, marked in standings_rows(html) if not marked] == expected


def test_a_changed_key_fetches_the_new_list(event, raced):
    html = body(Client().get("/kiosk/standings/", {"key": "st-ff"}))

    assert 'data-shown-key="st-ff"' in html


# ----- other pages ---------------------------------------------------------------


def test_scale_mph_uses_the_track_length(event):
    RaceSettings.objects.filter(event=event).update(track_length=32)
    html = body(Client().get("/kiosk.php", {"page": "kiosks/scale-MPH.kiosk"}))

    assert 'data-track-length="32"' in html
    assert "the 32-foot track" in html
    assert f"{32 / 3.0 * 3600 * 25 / 5280:4.1f}" in html


def test_now_racing_draws_a_row_per_lane_with_its_color_and_hides_times_for_points(
    event,
):
    RaceSettings.objects.filter(event=event).update(
        lane_count=3, lane_colors="red,blue", use_points=True
    )
    html = body(Client().get("/kiosk.php", {"page": "kiosks/now-racing.kiosk"}))

    assert re.findall(r'data-lane="(\d)" data-color="(\w+)"', html) == [
        ("1", "red"),
        ("2", "blue"),
        ("3", "none"),
    ]
    assert "data-time>" not in html and "k-points" in html


def test_the_reversed_columnar_page_puts_the_last_lane_first(event):
    RaceSettings.objects.filter(event=event).update(lane_count=3)
    plain = body(
        Client().get("/kiosk.php", {"page": "kiosks/now-racing-columnar.kiosk"})
    )
    reverse = body(
        Client().get(
            "/kiosk.php", {"page": "kiosks/now-racing-columnar-reversed.kiosk"}
        )
    )

    assert re.findall(r'k-column" data-lane="(\d)"', plain) == ["1", "2", "3"]
    assert re.findall(r'k-column" data-lane="(\d)"', reverse) == ["3", "2", "1"]


def test_a_preview_writes_neither_the_session_nor_the_database(event, raced):
    """kiosk.php by address registers the kiosk (test_every_view exempts it);
    a preview, which the dashboard and scene editor can open, changes
    nothing."""
    from ui.tests.db import no_db_writes
    from ui.tests.sessions import no_session_writes

    client = Client()
    for key in PAGES:
        with no_session_writes(), no_db_writes():
            body(client.get("/kiosk.php", {"page": key}))


# ----- on deck -------------------------------------------------------------------


def test_d34_the_on_deck_report_keeps_the_staged_heat_through_a_reschedule(event):
    """Heat 3 running and heat 4 staged; a reschedule fits a late racer into
    the round. The on-deck report, the poll the on-deck kiosk draws, shows
    heats 3 and 4 with the same cars in the same lanes, after the reload the
    reschedule asks of the kiosks."""
    from racing.engine.scheduling.adjuster import NodeBudget
    from racing.services.reschedule import reschedule
    from racing.services.tests.test_reschedule import (
        UNLIMITED,
        late_racer,
        make_current,
        new_round,
        race,
    )

    round = new_round(range(101, 111), event=event)
    race(round, 1, 2)
    make_current(round, 3)
    Kiosk.objects.create(event=event, address="tv", page="kiosks/ondeck.kiosk")
    client = Client()

    def on_deck(heats=(3, 4)) -> dict:
        reply = client.get(
            "/action.php", {"query": "poll", "values": "ondeck,current-heat"}
        ).json()
        assert reply["current-heat"]["heat"] == 3
        found = {}
        for cell in reply["ondeck"]["chart"]:
            if cell["roundid"] == round.pk and cell["heat"] in heats:
                found.setdefault(cell["heat"], []).append(
                    (cell["lane"], cell["carnumber"])
                )
        return found

    before = on_deck()
    reschedule(round, late_racer(round, 120).id, budget=NodeBudget(UNLIMITED))
    body(client.get("/kiosk.php", {"address": "tv"}))

    assert on_deck() == before
    assert sorted(before) == [3, 4] and all(len(v) == 4 for v in before.values())
