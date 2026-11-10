"""The replay page and the remote camera (#53): the replay login (D5, D9,
D16), the 301 from the PHP paths (D20), and what each page is given: the
empty websocket URL, `upload-videos`, the ICE servers, the clip window's
settings (D18) and the kiosk frame's URL."""

import io
import json
import re

import pytest
from django.test import Client
from django.urls import reverse

from core import race_settings
from core.models import RaceSettings, SiteState
from racing.services import replay

pytestmark = pytest.mark.django_db

CONFIG = re.compile(
    r'<script id="replay-config" type="application/json">(.*?)</script>'
)


def config(response) -> dict:
    return json.loads(CONFIG.search(response.content.decode())[1])


def warnings_from(caplog) -> list[str]:
    """The warnings `replay.ice_servers()` logged."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == replay.__name__ and r.levelname == "WARNING"
    ]


def dropped(url: str, why: str) -> str:
    return (
        f"replay_ice_servers: {url} is not sent to the replay page or the camera: {why}"
    )


@pytest.mark.parametrize(
    ("php", "page"), [("/replay.php", "/replay/"), ("/camera.php", "/replay/camera/")]
)
def test_a_logged_out_visitor_is_sent_to_log_in_and_back(event, php, page):
    """D16: PHP's pages are anonymous. The PHP path answers 301 with its
    query (D20), and the page sends a visitor to the login page, which
    returns to it."""
    client = Client()

    moved = client.get(f"{php}?address=Lane%20cam")
    assert moved.status_code == 301
    assert moved["Location"] == f"{page}?address=Lane%20cam"

    response = client.get(page)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login") + "?next=")
    assert response["Location"].endswith(page.replace("/", "%2F"))


@pytest.mark.parametrize("page", ["/replay/", "/replay/camera/"])
@pytest.mark.parametrize("role", ["Photo", "RaceCrew"])
def test_a_role_without_race_control_cannot_run_replay(login, page, role):
    """D16: the Photo role cannot run replay."""
    response = login(role).get(page)

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))


def test_the_replay_page_gets_its_settings(login, event, caplog):
    """The stored settings reach the page. The turn: URL, which Chromium
    refuses with no credential, is dropped with a warning (#259)."""
    RaceSettings.objects.filter(event=event).update(
        upload_videos=False,
        replay_skipback_ms=3000,
        replay_num_showings=3,
        replay_rate=25,
        replay_post_roll_ms=1500,
        replay_max_clip_ms=4000,
        replay_ice_servers="stun:a.example:3478, turn:b.example",
        websocket_url="ws://example/ws",
    )

    response = login("RaceCoordinator").get("/replay/")

    assert response.status_code == 200
    assert config(response) == {
        "skipback_ms": 3000,
        "showings": 3,
        "rate": 25,
        "post_roll_ms": 1500,
        "max_clip_ms": 4000,
        "upload_videos": False,
        "ice_servers": [{"urls": ["stun:a.example:3478"]}],
        # v1 polls whatever the websocket setting holds (#56).
        "websocket_url": "",
        "action": "/action.php",
        "debug": False,
    }
    html = response.content.decode()
    shown = dict(re.findall(r'data-setting="([\w-]+)">([^<]*)<', html))
    assert shown == {
        "replay-rate": "25%",
        "replay-num-showings": "3",
        "replay-skipback": "3 s",
        "replay-post-roll": "1.5 s",
        "replay-max-clip": "4 s",
        # 3 s + 1.5 s, cut to the 4 s maximum (D18).
        "clip": "4 s",
    }
    assert 'href="/settings/#replay"' in html
    assert 'id="debug" class="replay-log-panel" hidden' in html
    assert warnings_from(caplog) == [
        "replay_ice_servers: turn:b.example is not sent to the replay page or"
        " the camera: a TURN URL needs a username and password, which the"
        " setting cannot hold yet"
    ]


def test_the_kiosk_frame_url_passes_the_address_on(login):
    client = login("RaceCoordinator")

    plain = client.get("/replay/").content.decode()
    with_address = client.get("/replay/?address=Lane+cam&debug").content.decode()

    assert 'value="//testserver/kiosk.php"' in plain
    # replay.php:24-26 appends the address as given.
    assert 'value="//testserver/kiosk.php?address=Lane cam"' in with_address
    assert 'id="debug" class="replay-log-panel">' in with_address


def test_the_camera_page_gets_the_ice_servers_and_debug(login, event):
    RaceSettings.objects.filter(event=event).update(replay_ice_servers="")
    client = login("RaceCoordinator")

    found = config(client.get("/replay/camera/?debug"))

    assert found["ice_servers"] == []
    assert found["websocket_url"] == ""
    assert found["debug"] is True
    assert 'id="log" class="camera-log" role="log" aria-label="Camera log">' in (
        client.get("/replay/camera/?debug").content.decode()
    )


# One URL for each way the settings field refuses one (#241), which the
# server then drops (#259). `URLS` and `OVER_REFUSED` in
# `browser/test_settings_ice_servers.py` show Chromium throwing on each but
# the last, which it takes and the field refuses.
DROPPED = (
    "turn:b.example",
    "TURNS:b.example",
    "stun:",
    "stun:a.example:0",
    "stun:a.example:65536",
    "stun:a.example?transport=udp",
    "stun://a.example",
    "stun:a|b",
    "stun:ünï.example",
)


@pytest.mark.parametrize("url", DROPPED)
def test_an_ice_url_the_field_refuses_is_dropped_with_a_warning(url, caplog):
    text = f"stun:a.example:3478, {url} STUN:c.example:65535"

    assert replay.ice_servers(text) == [
        {"urls": ["stun:a.example:3478", "STUN:c.example:65535"]}
    ]
    why = replay.TURN_REFUSED if url.lower().startswith("turn") else replay.NOT_STUN
    assert warnings_from(caplog) == [dropped(url, why)]


def test_a_setting_of_only_refused_urls_sends_no_ice_server(caplog):
    assert replay.ice_servers("turn:b.example stun:") == []
    assert warnings_from(caplog) == [
        dropped("turn:b.example", replay.TURN_REFUSED),
        dropped("stun:", replay.NOT_STUN),
    ]


def test_the_camera_and_the_replay_page_get_the_same_ice_servers(login, event):
    """#259: both pages drop the same URLs, so neither offers a server the
    other never heard of."""
    RaceSettings.objects.filter(event=event).update(
        replay_ice_servers="turn:b.example stun:a.example:3478 stun:"
    )
    client = login("RaceCoordinator")

    viewer = config(client.get("/replay/"))["ice_servers"]
    camera = config(client.get("/replay/camera/"))["ice_servers"]

    assert viewer == camera == [{"urls": ["stun:a.example:3478"]}]


def test_a_turn_url_stored_under_the_legacy_key_and_imported_is_dropped(
    login, event, caplog
):
    """#259: the settings field refuses a turn: URL, but the legacy key's
    writer stores it, and a snapshot carries it (DJANGO_ONLY_KEYS) into the
    event it imports. The replay page of that event gets the STUN URL
    only, with a warning for the other."""
    race_settings.write(event, "replay-ice-servers", "turn:b.example stun:a.example")
    client = login("RaceCoordinator")
    snapshot = client.get("/action.php?query=snapshot.get").content
    put = client.post(
        "/action.php", {"action": "snapshot.put", "snapshot": io.BytesIO(snapshot)}
    )
    assert "<success/>" in put.content.decode()
    imported = SiteState.objects.get(pk=1).active_event
    assert imported.pk != event.pk
    stored = RaceSettings.objects.get(event=imported).replay_ice_servers
    assert stored == "turn:b.example stun:a.example"
    caplog.clear()

    found = config(client.get("/replay/"))["ice_servers"]

    assert found == [{"urls": ["stun:a.example"]}]
    assert warnings_from(caplog) == [dropped("turn:b.example", replay.TURN_REFUSED)]


def test_the_default_ice_server_is_phps_stun_server(event):
    settings = RaceSettings.objects.get(event=event)

    assert replay.ice_servers(settings.replay_ice_servers) == [
        {"urls": ["stun:stun.l.google.com:19302"]}
    ]
    assert replay.ice_servers("  \n ,") == []


@pytest.mark.parametrize(
    ("skipback", "post_roll", "longest", "clip"),
    [(4000, 2000, 10000, 6000), (4000, 2000, 5000, 5000), (9000, 3000, 10000, 10000)],
)
def test_a_clip_is_skipback_and_post_roll_cut_to_the_longest(
    event, skipback, post_roll, longest, clip
):
    """D18."""
    settings = RaceSettings.objects.get(event=event)
    settings.replay_skipback_ms = skipback
    settings.replay_post_roll_ms = post_roll
    settings.replay_max_clip_ms = longest

    assert replay.clip_length_ms(settings) == clip
