"""The replay page and the remote camera in a browser (#53), with
Chromium's two fake cameras.

- The setup's camera, resolution and frame rate survive a reload (D9).
- A heat driven through the legacy API, `heat.select` and the timer's
  STARTED and FINISHED, replays: the page records the post-roll, plays a
  clip cut to the longest-clip setting (D18), posts `replay-started` and
  `replay-ended` to the kiosk frame, and uploads the first showing.
- The parity boxes of `replay.php` and `camera.php`, the remote camera over
  WebRTC with no ICE server, and C32's websocket flush.
- The screenshots of the setup screen and the camera page at both sizes.
"""

import re

import pytest
from playwright.sync_api import expect

from core.models import RaceSettings
from racing.services import replay
from ui.tests.browser.conftest import (
    PLAIN_HTTP_HOST,
    assert_accessible,
    close_tab,
    shoot,
)
from ui.tests.race import race_setup, scheduled

# Each frame records the messages posted to it, so a test can read what the
# replay page told the kiosk frame.
RECORD_MESSAGES = """window.__messages = [];
window.addEventListener('message', (e) => window.__messages.push(e.data));"""


@pytest.fixture
def videos(event, tmp_path):
    folder = tmp_path / "videos"
    folder.mkdir()
    RaceSettings.objects.filter(event=event).update(video_directory=str(folder))
    return folder


def desktop_only(page):
    if page.size != "desktop":
        pytest.skip("the replay station is a laptop")


def stream_info(page):
    return page.locator("#recording-stream-size")


def open_replay(page, url="/replay/"):
    page.goto(url)
    expect(stream_info(page)).to_have_text(re.compile(r"^\d+x\d+, \d+ fps$"))


def replay_state(page):
    return page.locator("#replay")


def proceed(page):
    page.get_by_label("Change to fullscreen").uncheck()
    page.get_by_role("button", name="Proceed").click()
    expect(page.locator("#interior")).to_be_visible()
    expect(replay_state(page)).to_have_attribute("data-replay-state", "watching")


def kiosk_frame(page):
    expect(page.frame_locator("#interior").locator("body")).to_be_visible()
    return page.locator("#interior").element_handle().content_frame()


def fast_replays(event, **fields):
    RaceSettings.objects.filter(event=event).update(
        replay_num_showings=1, replay_rate=100, **fields
    )


def track_settings(page) -> dict:
    return page.evaluate(
        "() => { const t = document.getElementById('preview').srcObject"
        ".getVideoTracks()[0]; const s = t.getSettings();"
        " return {label: t.label, width: s.width, height: s.height,"
        " frameRate: Math.round(s.frameRate)}; }"
    )


# ----- the setup screen ------------------------------------------------------------


def test_the_camera_resolution_and_frame_rate_survive_a_reload(login, event):
    """Acceptance (D9): chosen on the setup screen, asked for as exact
    constraints, remembered by this browser."""
    page = login("RaceCoordinator")
    open_replay(page)
    assert_accessible(page)
    shoot(page, "replay-setup")

    page.get_by_label("Camera", exact=True).select_option(label="fake_device_1")
    expect(stream_info(page)).to_have_text(re.compile(r"^\d+x\d+, \d+ fps$"))
    page.get_by_label("Resolution").select_option("1280x720")
    expect(stream_info(page)).to_have_text(re.compile(r"^1280x720, "))
    page.get_by_label("Frame rate").select_option("15")
    expect(stream_info(page)).to_have_text("1280x720, 15 fps")

    page.reload()

    expect(stream_info(page)).to_have_text("1280x720, 15 fps")
    camera = page.get_by_label("Camera", exact=True)
    expect(camera.locator("option:checked")).to_have_text("fake_device_1")
    expect(page.get_by_label("Resolution")).to_have_value("1280x720")
    expect(page.get_by_label("Frame rate")).to_have_value("15")
    assert track_settings(page) == {
        "label": "fake_device_1",
        "width": 1280,
        "height": 720,
        "frameRate": 15,
    }


def test_a_quick_change_of_camera_and_size_opens_the_last_choice(login, event):
    """A camera still opening at its old size made the next request, for a
    new size, fail as overconstrained (3 runs in 5 before the requests were
    queued, found by the verify run)."""
    page = login("RaceCoordinator")
    open_replay(page)

    page.get_by_label("Camera", exact=True).select_option(label="fake_device_1")
    page.get_by_label("Resolution").select_option("1280x720")

    expect(stream_info(page)).to_have_text(re.compile(r"^1280x720, "))
    expect(page.locator("#camera-error")).to_be_hidden()
    assert track_settings(page)["label"] == "fake_device_1"


def test_a_size_the_camera_cannot_give_says_so_and_opens_its_own(login, event):
    page = login("RaceCoordinator")
    page.evaluate(
        "localStorage.setItem('derbynet.replay.camera', JSON.stringify("
        "{deviceId: 'gone', label: 'unplugged', width: 1280, height: 720,"
        " frameRate: 15}))"
    )
    open_replay(page)
    # An unknown camera falls back to the first, at its own size.
    expect(page.locator("#device-picker option:checked")).to_have_text("fake_device_0")

    page.evaluate(
        "localStorage.setItem('derbynet.replay.camera', JSON.stringify({deviceId:"
        " 'stale', label: 'fake_device_0', width: 1280, height: 720,"
        " frameRate: 200}))"
    )
    page.reload()

    expect(page.locator("#camera-error")).to_contain_text(
        "This camera cannot record at 1280 × 720, 200 fps"
    )
    expect(stream_info(page)).to_have_text(re.compile(r"^\d+x\d+, \d+ fps$"))
    # A failed open, such as a busy camera, keeps the operator's choice.
    assert saved_choice(page) == {
        "deviceId": "stale",
        "label": "fake_device_0",
        "width": 1280,
        "height": 720,
        "frameRate": 200,
    }


def saved_choice(page) -> dict:
    return page.evaluate("JSON.parse(localStorage.getItem('derbynet.replay.camera'))")


def test_an_unplugged_camera_stays_remembered(login, event):
    """The page opens another camera meanwhile, and keeps the choice for
    when the camera is back, until the operator chooses again."""
    page = login("RaceCoordinator")
    gone = {
        "deviceId": "gone",
        "label": "unplugged",
        "width": 1280,
        "height": 720,
        "frameRate": 15,
    }
    page.evaluate(
        "(c) => localStorage.setItem('derbynet.replay.camera', JSON.stringify(c))",
        gone,
    )
    open_replay(page)
    expect(page.locator("#device-picker option:checked")).to_have_text("fake_device_0")
    assert saved_choice(page) == gone

    page.get_by_label("Frame rate").select_option("15")
    expect(stream_info(page)).to_have_text(re.compile(r", 15 fps$"))
    assert saved_choice(page)["label"] == "fake_device_0"


def test_the_setup_shows_the_replay_settings(login, event):
    RaceSettings.objects.filter(event=event).update(
        replay_skipback_ms=4000, replay_post_roll_ms=2000, replay_max_clip_ms=5000
    )
    page = login("RaceCoordinator")
    page.goto("/replay.php?address=Lane%20cam")
    page.wait_for_url(re.compile(r"/replay/\?address=Lane%20cam$"))
    settings = page.locator("[data-replay-settings]")
    expect(settings.locator('[data-setting="clip"]')).to_have_text("5 s")
    expect(settings.get_by_role("link", name="Change in Settings")).to_have_attribute(
        "href", "/settings/#replay"
    )
    expect(page.get_by_label("URL")).to_have_value(
        re.compile(r"^//[^/]+/kiosk\.php\?address=Lane cam$")
    )


def test_a_browser_without_media_recorder_is_told(login, event):
    page = login("RaceCoordinator")
    page.add_init_script("delete window.MediaRecorder")
    page.goto("/replay/")
    warning = page.locator("#recorder-warning")
    expect(warning).to_contain_text("This browser does not support MediaRecorder.")
    expect(warning).to_contain_text("Mozilla")


@pytest.mark.parametrize("path", ["/replay/", "/replay/camera/"])
def test_plain_http_away_from_localhost_links_to_https(
    login, live_server, page_errors, path
):
    page = login("anonymous")
    port = live_server.url.rsplit(":", 1)[1]
    page.goto(f"http://{PLAIN_HTTP_HOST}:{port}/login/?all")
    page.locator('button[data-role="RaceCoordinator"]').click()
    page.get_by_label("Password").fill("doyourbest")
    page.get_by_label("Password").press("Enter")
    page.wait_for_url(re.compile(r"/$"))
    page.goto(f"http://{PLAIN_HTTP_HOST}:{port}{path}")
    warning = page.locator("#no-camera-warning")
    expect(warning).to_contain_text("Access to cameras is blocked.")
    # The host name alone, with no port, as replay.php and camera.php build
    # it from window.location.hostname.
    expect(warning.get_by_role("link")).to_have_attribute(
        "href", f"https://{PLAIN_HTTP_HOST}{path}"
    )


def test_debug_shows_the_log_on_both_pages(login, event):
    page = login("RaceCoordinator")
    # The first page to poll gets HELLO; a second within 10 s does not.
    open_replay(page, "/replay/?debug")
    expect(page.locator("#log")).to_contain_text("HELLO")
    page.goto("/replay/")
    expect(page.locator("#debug")).to_be_hidden()
    page.goto("/replay/camera/?debug")
    expect(page.locator("#log")).to_be_visible()


# ----- a heat replays --------------------------------------------------------------


def post(page, **form):
    reply = page.request.post("/action.php", form=form)
    assert reply.ok and "success" in reply.text(), reply.text()
    return reply


def timer(page, message, **fields):
    return post(page, action="timer-message", message=message, **fields)


def test_a_heat_replays_with_the_clip_window_and_uploads(login, event, videos):
    """Acceptance (D18): with a post-roll of 2 s, a skipback of 4 s and a
    longest clip of 5 s, the clip runs 5 s and ends 2 s after the finish.
    The kiosk frame hears `replay-started` and `replay-ended`, and the first
    showing is uploaded under the START name."""
    fast_replays(
        event,
        replay_skipback_ms=4000,
        replay_post_roll_ms=2000,
        replay_max_clip_ms=5000,
        upload_videos=True,
    )
    wolves = scheduled(race_setup(event, groups=(("Wolves", 4),))["Wolves"], race=False)
    page = login("RaceCoordinator")
    desktop_only(page)
    page.context.add_init_script(RECORD_MESSAGES)
    open_replay(page)
    proceed(page)
    frame = kiosk_frame(page)
    # More than the 5 s clip in the ring before the finish.
    page.wait_for_timeout(3500)

    post(page, action="heat.select", roundid=str(wolves.pk), heat="1", now_racing="1")
    timer(page, "HELLO")
    timer(page, "IDENTIFIED", lane_count="4", timer="Test", human="Test Timer")
    timer(page, "STARTED")
    page.wait_for_timeout(1000)
    timer(page, "FINISHED", lane1="2.1", lane2="2.2", lane3="2.3", lane4="2.4")

    expect(replay_state(page)).to_have_attribute(
        "data-replay-state", "playing", timeout=5000
    )
    expect(page.locator("#playback-background")).to_be_visible()
    assert frame.evaluate("window.__messages") == ["replay-started"]
    assert frame.evaluate("window.Kiosk.paused") is True
    clip = page.evaluate("g_last_clip")
    assert clip["root"] == "Wolves_Round1_Heat01"
    assert clip["end"] - clip["finish"] == 2000
    # A fake camera frame comes every 50 ms, and a loaded machine skips some.
    assert 0 <= clip["end"] - clip["last"] < 250, clip
    assert 4750 <= clip["last"] - clip["first"] <= 5000, clip
    assert clip["first"] >= clip["start"]

    expect(replay_state(page)).to_have_attribute(
        "data-replay-state", "watching", timeout=10000
    )
    expect(page.locator("#playback-background")).to_be_hidden()
    assert frame.evaluate("window.__messages") == ["replay-started", "replay-ended"]
    assert frame.evaluate("window.Kiosk.paused") is False
    for _ in range(100):
        if list(videos.iterdir()):
            break
        page.wait_for_timeout(100)
    [video] = list(videos.iterdir())
    assert video.name == "Wolves_Round1_Heat01.mkv"
    assert video.stat().st_size > 0


@pytest.mark.parametrize("hold_ms", [0, 300])
def test_race_starts_times_out_and_the_late_replay_is_ignored(login, event, hold_ms):
    """RACE_STARTS replays `skipback` ms after the gate opens, and a REPLAY
    after that is ignored until the next START (replay.php:111-158).

    The page starts its timer inside the RACE_STARTS call (replay.js
    `handle_replay_message`), so the 1000 ms bound counts from a time taken
    just before the call. A time taken after it returns is late by however
    long the page was busy in between: CI once read 999.4 ms (#236). With
    `hold_ms` the page stays busy that long after the call, and the clip
    comes less than 1000 ms after the call returns."""
    fast_replays(event, replay_skipback_ms=1000, replay_post_roll_ms=500)
    page = login("RaceCoordinator")
    desktop_only(page)
    open_replay(page)
    before, returned = page.evaluate(
        "(hold) => { handle_replay_message('START Wolves_Round1_Heat02');"
        " const before = performance.now();"
        " handle_replay_message('RACE_STARTS 1000 1 100');"
        " const until = performance.now() + hold;"
        " while (performance.now() < until) {}"
        " return [before, performance.now()]; }",
        hold_ms,
    )
    page.wait_for_function("g_last_clip !== null")
    timed_out = page.evaluate("g_last_clip")
    assert timed_out["root"] == "Wolves_Round1_Heat02"
    assert timed_out["finish"] - before >= 1000, (before, returned, timed_out)
    if hold_ms:
        # Only a timer that started before the hold can end this soon after it.
        assert timed_out["finish"] - returned < 1000, (before, returned, timed_out)
    expect(replay_state(page)).to_have_attribute(
        "data-replay-state", "setup", timeout=5000
    )

    # Each command is read at once, so the state after it is its answer.
    after = (
        "(cmd) => { handle_replay_message(cmd);"
        " return [document.getElementById('replay').dataset.replayState,"
        " g_last_clip.root]; }"
    )
    assert page.evaluate(after, "REPLAY 1000 1 100") == [
        "setup",
        "Wolves_Round1_Heat02",
    ]
    page.evaluate("handle_replay_message('START Wolves_Round1_Heat03')")
    assert page.evaluate(after, "REPLAY 1000 1 100") == [
        "post-roll",
        "Wolves_Round1_Heat03",
    ]


def test_cancel_stops_the_race_starts_timeout(login, event):
    fast_replays(event, replay_skipback_ms=500, replay_post_roll_ms=500)
    page = login("RaceCoordinator")
    desktop_only(page)
    open_replay(page)
    page.evaluate("handle_replay_message('RACE_STARTS 500 1 100')")
    page.evaluate("handle_replay_message('CANCEL')")
    page.wait_for_timeout(1000)
    expect(replay_state(page)).to_have_attribute("data-replay-state", "setup")
    assert page.evaluate("g_last_clip") is None


def test_the_delay_right_click_and_resize(login, event, page_errors):
    """The operator's delay holds the playback after the finish; a
    right-click on the kiosk replays now; a click or a window resize
    returns to the setup."""
    # Proceed loads the kiosk frame again, as replay.php's does, which cuts
    # off the kiosk poll it had out.
    page_errors.allow(
        r"request failed: \S*/action\.php\?query=poll\.kiosk\S* net::ERR_ABORTED$"
    )
    fast_replays(event, replay_skipback_ms=1000, replay_post_roll_ms=500)
    page = login("RaceCoordinator")
    desktop_only(page)
    open_replay(page)
    page.get_by_label("Delay playback by").fill("1.5")
    proceed(page)
    page.wait_for_timeout(1500)

    page.evaluate("handle_replay_message('START Wolves_Round1_Heat07')")
    page.locator("#click-shield").click(button="right")
    page.wait_for_function("g_last_clip !== null")
    # C53: PHP's right-click passes no name and uploads undefined.mkv.
    assert page.evaluate("g_last_clip.root") == "Wolves_Round1_Heat07"
    started = page.evaluate("g_last_clip.finish")
    page.wait_for_function(
        "document.getElementById('replay').dataset.replayState == 'playing'",
        polling="raf",
    )
    assert page.evaluate("performance.now()") - started >= 1500
    expect(replay_state(page)).to_have_attribute(
        "data-replay-state", "watching", timeout=5000
    )

    page.locator("#click-shield").click()
    expect(page.locator("[data-replay-setup]")).to_be_visible()
    expect(page.locator("#interior")).to_be_hidden()

    proceed(page)
    page.set_viewport_size({"width": 1100, "height": 700})
    expect(page.locator("[data-replay-setup]")).to_be_visible()


def test_the_fullscreen_switch_does_not_bring_the_setup_back(login, event):
    page = login("RaceCoordinator")
    desktop_only(page)
    open_replay(page)
    expect(page.get_by_label("Change to fullscreen")).to_be_checked()
    page.get_by_role("button", name="Proceed").click()
    expect(page.locator("#interior")).to_be_visible()
    page.wait_for_function("document.fullscreenElement !== null")
    # Going full screen resized the window, and the setup stays away.
    page.wait_for_timeout(1000)
    expect(page.locator("[data-replay-setup]")).to_be_hidden()
    expect(replay_state(page)).to_have_attribute("data-replay-state", "watching")


# ----- the remote camera -----------------------------------------------------------


def test_the_camera_page(login, event):
    page = login("RaceCoordinator")
    page.goto("/replay/camera/")
    expect(page.locator("[data-stream-size]")).to_have_text(re.compile(r"^\d+ × \d+$"))
    expect(page.locator("#log")).to_be_hidden()
    assert_accessible(page)
    shoot(page, "replay-camera")

    page.get_by_label("Camera", exact=True).select_option(label="fake_device_1")
    expect(page.locator("#preview")).to_be_visible()
    page.get_by_label("Show preview").uncheck()
    expect(page.locator("#preview")).to_be_hidden()
    page.reload()
    expect(page.locator("#device-picker option:checked")).to_have_text("fake_device_1")


def test_the_remote_camera_streams_to_the_replay_page_with_no_ice_server(login, event):
    """With `replay_ice_servers` empty, host candidates connect the camera
    and the replay page, as on a LAN with no internet, and the camera asks
    for the size the replay page solicited."""
    RaceSettings.objects.filter(event=event).update(replay_ice_servers="")
    page = login("RaceCoordinator")
    desktop_only(page)
    camera = page.context.new_page()
    camera.goto("/replay/camera/?debug")
    expect(camera.locator("[data-stream-size]")).to_have_text(re.compile(r" × "))

    open_replay(page, "/replay/?debug")
    page.get_by_label("Resolution").select_option("640x480")
    expect(stream_info(page)).to_have_text("640x480, 20 fps")
    page.get_by_label("Camera", exact=True).select_option("remote")
    expect(page.locator("#waiting-for-remote")).to_be_visible()
    page.get_by_label("Resolution").select_option("1280x720")

    expect(page.locator("#waiting-for-remote")).to_be_hidden(timeout=20000)
    # WebRTC may send a smaller picture at first and grow it, so the replay
    # page's size is whatever has come through so far.
    expect(stream_info(page)).to_have_text(re.compile(r"^[1-9]\d*x[1-9]\d*$"))
    expect(page.locator("#log")).to_contain_text("ontrack")
    # The camera asks its own camera for the largest size solicited, as
    # ideal values; Chromium's fake camera keeps its own 640 x 480.
    camera.wait_for_function(
        "() => { const v = document.getElementById('preview').srcObject;"
        " const c = v && v.getVideoTracks()[0].getConstraints();"
        # Chromium gives an ideal value back bare.
        " return c && c.width == 1280 && c.height == 720; }"
    )
    close_tab(page, camera)


def test_closing_the_camera_tab_waits_for_its_post(login, event):
    """The camera tab's message poll is held, then answered as the tab is
    closed: plain camera.close() left the POST in flight at teardown, 3 runs
    in 3 (PR #205's ui (postgres) failure)."""
    page = login("RaceCoordinator")
    desktop_only(page)
    camera = page.context.new_page()
    camera.goto("/replay/camera/")
    expect(camera.locator("[data-stream-size]")).to_have_text(re.compile(r" × "))
    held = []
    camera.route("**/action.php", lambda route: held.append(route))
    for _ in range(40):
        if held:
            break
        camera.wait_for_timeout(100)
    assert held
    held[0].continue_()
    close_tab(page, camera)


def test_a_message_sent_before_the_websocket_opens_is_sent_once_it_does(login, event):
    """C32: PHP flushed the queue with the undefined `wc.send`, so the
    viewer's first solicitation was lost on the websocket path (#56)."""
    page = login("RaceCoordinator")
    page.goto("/replay/camera/")
    sent = page.evaluate(
        """() => {
      class FakeSocket {
        constructor(url) { this.url = url; this.readyState = 0; this.sent = [];
                           FakeSocket.last = this; }
        send(text) { this.sent.push(JSON.parse(text)); }
        close() {}
      }
      FakeSocket.CONNECTING = 0; FakeSocket.OPEN = 1;
      window.WebSocket = FakeSocket;
      window.g_websocket_url = 'ws://relay.test/';
      const poller = new MessagePoller('viewer-test', () => {});
      poller.send_message({recipient: 'camera-replay', type: 'solicitation'});
      const socket = FakeSocket.last;
      socket.readyState = 1;
      socket.onopen({});
      return socket.sent;
    }"""
    )
    assert sent == [
        {"subscriber": "viewer-test", "topics": []},
        {"recipient": "camera-replay", "type": "solicitation"},
    ]


@pytest.mark.parametrize(
    ("skipback", "post_roll", "longest"),
    [(4000, 2000, 10000), (4000, 2000, 5000), (9000, 3000, 10000), (500, 0, 300)],
)
def test_the_page_cuts_a_clip_as_the_service_does(
    login, event, skipback, post_roll, longest
):
    """D18's clip length is computed in racing.services.replay and in
    replay.js; the two agree."""
    RaceSettings.objects.filter(event=event).update(
        replay_skipback_ms=skipback,
        replay_post_roll_ms=post_roll,
        replay_max_clip_ms=longest,
    )
    page = login("RaceCoordinator")
    desktop_only(page)
    page.goto("/replay/")
    settings = RaceSettings.objects.get(event=event)
    assert page.evaluate("clip_length_ms()") == replay.clip_length_ms(settings)
    # A command's skipback replaces the setting's, in both.
    settings.replay_skipback_ms = 1500
    page.evaluate("parse_replay_options('REPLAY 1500 1 100')")
    assert page.evaluate("clip_length_ms()") == replay.clip_length_ms(settings)


def test_the_delay_counts_after_the_post_roll(login, event):
    """replay.php:300-307 waits the operator's delay after it stops
    recording; recording now stops at the end of the post-roll (D18), so a
    delay shorter than the post-roll still delays the playback."""
    fast_replays(event, replay_skipback_ms=1000, replay_post_roll_ms=500)
    page = login("RaceCoordinator")
    desktop_only(page)
    open_replay(page)
    page.get_by_label("Delay playback by").fill("0.3")
    page.evaluate("handle_replay_message('TEST 1000 1 100')")
    page.wait_for_function("g_last_clip && g_last_clip.played_at")
    clip = page.evaluate("g_last_clip")
    assert clip["played_at"] - clip["finish"] >= 800, clip


def test_race_starts_names_the_clip_when_it_arrives(login, event):
    """replay.php:127 and :152-158: the RACE_STARTS timeout replays under the
    START name it heard with, and an earlier timeout still fires and clears
    the later one, as on_replay clears g_replay_timeout (replay.php:282-295)."""
    fast_replays(event, replay_skipback_ms=500, replay_post_roll_ms=200)
    page = login("RaceCoordinator")
    desktop_only(page)
    open_replay(page)
    sent = page.evaluate(
        "() => { handle_replay_message('START Wolves_Round1_Heat04');"
        " handle_replay_message('RACE_STARTS 500 1 100');"
        " handle_replay_message('START Wolves_Round1_Heat05');"
        " handle_replay_message('RACE_STARTS 2000 1 100');"
        " return performance.now(); }"
    )
    page.wait_for_function("g_last_clip !== null")
    clip = page.evaluate("g_last_clip")
    assert clip["root"] == "Wolves_Round1_Heat04"
    assert clip["finish"] - sent < 1500, clip
    page.wait_for_timeout(2500)
    assert page.evaluate("g_last_clip.finish") == clip["finish"]


def test_a_finish_during_a_clip_replays_again(login, event):
    """replay.php:282-310 has no guard: a second finish stops recording and
    plays again."""
    fast_replays(event, replay_skipback_ms=1000, replay_post_roll_ms=500)
    page = login("RaceCoordinator")
    desktop_only(page)
    open_replay(page)
    page.evaluate("handle_replay_message('TEST 1000 1 100')")
    first = page.evaluate("g_last_clip.finish")
    page.evaluate("handle_replay_message('START Wolves_Round1_Heat06')")
    page.evaluate("handle_replay_message('TEST 1000 1 100')")
    assert page.evaluate("g_last_clip.finish") > first
    assert page.evaluate("g_last_clip.root") == "Wolves_Round1_Heat06"
