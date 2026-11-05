"""The replay page and the remote camera (#53): the replay login (D5, D9,
D16), the 301 from the PHP paths (D20), and what each page is given: the
empty websocket URL, `upload-videos`, the ICE servers, the clip window's
settings (D18) and the kiosk frame's URL."""

import json
import re

import pytest
from django.test import Client
from django.urls import reverse

from core.models import RaceSettings
from racing.services import replay

pytestmark = pytest.mark.django_db

CONFIG = re.compile(
    r'<script id="replay-config" type="application/json">(.*?)</script>'
)


def config(response) -> dict:
    return json.loads(CONFIG.search(response.content.decode())[1])


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


def test_the_replay_page_gets_its_settings(login, event):
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
        "ice_servers": [{"urls": ["stun:a.example:3478", "turn:b.example"]}],
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
