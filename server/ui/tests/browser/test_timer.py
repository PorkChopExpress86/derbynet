"""The timer pages in a browser (#50): the browser timer at `/timer.php`
with every file it loads, its Timer settings button by role (D53), the
page a browser without Web Serial gets, the fake timer racing a heat in auto
mode, the parity boxes of `timer-test.php` and `fake-timer.php`, and the
screenshots.

The timer side posts `timer-message` from the page's own session, as the
fake timer and the browser timer do."""

import re

import pytest
from playwright.sync_api import expect

from core.models import RaceSettings, RaceState
from racing.models import HeatLane, TimerSetting, TimerStatus
from racing.services import heats, timer
from ui import timer_build
from ui.tests.browser.conftest import (
    PLAIN_HTTP_HOST,
    assert_accessible,
    dismiss_toasts,
    htmx_post,
    htmx_settled,
    log_in,
    shoot,
)
from ui.tests.race import race_setup, scheduled

ABORTED = r"request failed: \S*/timer/(status|test/live)/\S* net::ERR_ABORTED$"


@pytest.fixture(scope="module", autouse=True)
def _built_timer(tmp_path_factory):
    """A fresh checkout or worktree has no copied timer scripts, and every
    test here then failed on its own missing file (#53 retro), in three
    tickets' full browser runs in a row (#212 retro). So build them, once,
    as `manage.py build_timer` does: a few seconds, with `javac` or docker.
    xdist workers share the base temp folder, so the first one builds under
    a file lock there and the others find the files."""
    import fcntl

    shared = tmp_path_factory.getbasetemp().parent
    with (shared / "timer-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if timer_build.built():
            return
        try:
            timer_build.copy_sources()
            timer_build.profiles_js()
        except timer_build.BuildError as error:
            pytest.fail(
                f"`manage.py build_timer` failed: {error}. Run it by hand.",
                pytrace=False,
            )


FLAGS = (
    "FLAGS&flag-debug=bool:false&desc-debug=Debug output&flag-delay=int:300"
    "&desc-delay=Milliseconds between polls&ports=COM1,COM2"
    "&device-SmartLine=SmartLine timer&device-Champ=Champ timer"
)


@pytest.fixture(autouse=True)
def _polls_may_be_cut_short(page_errors):
    """A control aborts the poll of its card still in flight
    (`hx-sync="...:replace"`)."""
    page_errors.allow(ABORTED)


def desktop_only(page):
    if page.size != "desktop":
        pytest.skip("the browser timer needs desktop Chrome or Edge")


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check runs once, at 390x844")


def timer_message(page, fields: str) -> str:
    """A timer message from the page's session, as `derby-timer.jar` posts."""
    reply = page.request.post(
        "/action.php",
        data=f"action=timer-message&message={fields}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert reply.ok
    return reply.text()


def connect(page, lanes=4):
    timer_message(page, "HELLO")
    timer_message(
        page, f"IDENTIFIED&lane_count={lanes}&timer=SmartLine&human=Test Timer"
    )


# ----- the browser timer -----------------------------------------------------------


def test_the_browser_timer_loads_every_file(login, page_errors):
    """Acceptance: `/timer.php` in headless Chrome with no 404 for any
    script, stylesheet, image or worker. It replaces the `timer.php` load of
    `testing/puppeteer/all-pages-test.js`."""
    page = login("RaceCoordinator")
    desktop_only(page)
    failed = []
    page.on(
        "response",
        lambda r: r.status >= 400 and failed.append(f"{r.status} {r.url}"),
    )
    page.on("requestfailed", lambda r: failed.append(f"failed {r.url}"))
    loaded = []
    page.on("requestfinished", lambda r: loaded.append(r.url))
    worker = []
    page.on("worker", lambda w: worker.append(w.url))
    page.goto("/timer.php")
    page.wait_for_load_state("networkidle")
    assert page.url.endswith("/timer.php"), "the page went to the no-serial page"
    # main.js asks for a click once it finds no port it may use.
    expect(page.locator("#need-gesture-modal")).to_be_visible()
    expect(page.locator("#profiles-list li").first).to_be_visible()
    assert failed == []
    assert worker and worker[0].endswith("/js/timer/clock-worker.js")
    for path in (
        "/css/timer.css",
        "/js/jquery.js",
        "/js/timer/profiles.js",
        "/js/timer/main.js",
        "/img/derbynet.png",
        "/img/gear_icon.png",
        "/img/status/unknown.png",
    ):
        assert any(url.endswith(path) for url in loaded), path
    assert page.evaluate("g_version.revision") >= 0
    assert page.evaluate("all_profiles().length") > 5
    shoot(page, "browser-timer")


def test_only_a_race_coordinator_gets_the_timer_settings_button(page, event):
    """D53: `timer.settings` needs CONTROL_RACE, so a Timer login sees no
    button; the race-day laptop logs in as RaceCoordinator."""
    desktop_only(page)
    log_in(page, "Timer")
    page.goto("/timer.php")
    expect(page.locator("#probe-button")).to_be_visible()
    expect(page.locator("#timer-settings-button")).to_have_count(0)
    log_in(page, "RaceCoordinator")
    page.goto("/timer.php")
    expect(page.locator("#timer-settings-button")).to_be_visible()


def skip_timer_files(page, page_errors):
    """The guard leaves `/timer.php` while the browser's preload scanner is
    still fetching the timer's files; the page leaves before it would run
    them, so they are refused here. A refusal can come after the guard's
    navigation, and the harness then drops the request it cut off."""
    page.route(re.compile(r"/(js|css|img)/"), lambda route: route.abort())
    page_errors.allow(r"request failed: \S*/(js|css|img)/\S* net::ERR_FAILED$")
    page_errors.allow(r"^console: Failed to load resource: net::ERR_FAILED$")


def test_a_browser_without_web_serial_gets_a_page_that_says_so(login, page_errors):
    page = login("RaceCoordinator")
    page.add_init_script("delete Navigator.prototype.serial")
    skip_timer_files(page, page_errors)
    page.goto("/timer.php")
    page.wait_for_url(re.compile(r"/timer/no-serial/$"))
    found = page.locator("[data-no-serial]")
    expect(found).to_contain_text("Chrome")
    expect(found).to_contain_text("Edge")
    # localhost is a secure context.
    expect(page.locator("[data-not-secure]")).to_be_hidden()
    assert_accessible(page)
    shoot(page, "timer-no-serial")


def test_plain_http_away_from_localhost_links_to_https(login, live_server, page_errors):
    page = login("RaceCoordinator")
    skip_timer_files(page, page_errors)
    port = live_server.url.rsplit(":", 1)[1]
    page.goto(f"http://{PLAIN_HTTP_HOST}:{port}/login/?all")
    log_in_here = page.locator('button[data-role="RaceCoordinator"]')
    log_in_here.click()
    page.get_by_label("Password").fill("doyourbest")
    page.get_by_label("Password").press("Enter")
    page.wait_for_url(re.compile(r"/$"))
    page.goto(f"http://{PLAIN_HTTP_HOST}:{port}/timer.php")
    page.wait_for_url(re.compile(r"/timer/no-serial/$"))
    link = page.locator("[data-not-secure] a")
    expect(link).to_have_attribute(
        "href", f"https://{PLAIN_HTTP_HOST}:{port}/timer.php"
    )


# ----- the fake timer --------------------------------------------------------------


def test_the_fake_timer_races_a_heat_in_auto_mode(login, event):
    """Acceptance: the fake timer in auto mode races a 4-lane heat against
    Django, and the race dashboard shows the result."""
    page = login("RaceCoordinator")
    phone_only(page)
    wolves = scheduled(race_setup(event, groups=(("Wolves", 4),), lanes=4)["Wolves"])
    page.goto("/timer/fake/")
    expect(page.locator("[data-summary]")).to_contain_text("Ready for heat 1 of")
    expect(page.locator("[data-lanes] li")).to_have_count(4)
    shoot(page, "fake-timer")
    page.locator("[data-auto-mode]").click()
    expect(page.locator("[data-auto-mode]")).to_have_attribute("aria-checked", "true")
    # 1 s for the heat to settle, 5 s for the crew, under 4 s down the track.
    heat_1 = HeatLane.objects.filter(round=wolves, heat=1)
    for _ in range(200):
        if heat_1.exclude(finish_time=None).count() == 4:
            break
        page.wait_for_timeout(100)
    times = sorted(heat_1.values_list("finish_time", flat=True))
    assert len(times) == 4 and all(2.8 <= t <= 3.8 for t in times), times
    page.goto("/dashboard/")
    card = page.locator(f'[data-roundid="{wolves.pk}"] [data-progress-text]')
    expect(card).to_contain_text("1 run")


def test_fake_timer_parity_lanes_start_and_stop(login, event):
    """Parity: lane count, Start and a Stop per lane."""
    page = login("RaceCoordinator")
    phone_only(page)
    wolves = race_setup(event, groups=(("Wolves", 4),), lanes=4)["Wolves"]
    scheduled(wolves, race=False)
    page.goto("/timer/fake/")
    start = page.get_by_role("button", name="Start", exact=True)
    expect(page.locator("[data-summary]")).to_have_text("Not racing.")
    page.get_by_role("button", name="More: Lanes on the track").click()
    expect(page.locator("[data-lanes] li")).to_have_count(5)
    page.get_by_role("button", name="Fewer: Lanes on the track").click()
    expect(page.locator("[data-lanes] li")).to_have_count(4)
    # The page asks again each second; racing on stages heat 1.
    assert heats.select_heat(event, round_id=wolves.pk, heat=1, now_racing=True) is None
    expect(start).to_be_enabled(timeout=5000)
    start.click()
    for lane in range(1, 5):
        page.get_by_role("button", name=f"Stop lane {lane}").click()
    heat_1 = HeatLane.objects.filter(round=wolves, heat=1)
    for _ in range(50):
        if heat_1.exclude(finish_time=None).count() == 4:
            break
        page.wait_for_timeout(100)
    assert heat_1.exclude(finish_time=None).count() == 4


# ----- timer status ----------------------------------------------------------------


def open_status(page):
    page.goto("/timer/")
    expect(page.locator("#timer-status")).to_be_visible()
    return page


def test_status_parity_details_settings_and_start_race(login, event):
    """Parity: timer status and details, timer settings for port, device
    and every flag type, Start Race, and the Fake Timer link."""
    page = open_status(login("RaceCoordinator"))
    status = page.locator("#timer-status")
    expect(status).to_contain_text("NOT CONNECTED")
    expect(status.get_by_role("link", name="Fake Timer")).to_have_attribute(
        "href", "/timer/fake/"
    )
    connect(page)
    timer_message(page, FLAGS.replace(" ", "+"))
    timer_message(page, "HEARTBEAT&remote-start=YES")
    expect(status).to_contain_text("CONNECTED", timeout=5000)
    expect(status).to_contain_text("Test Timer")
    expect(status.locator('[data-detail="agent"]')).to_contain_text("Chrome")
    expect(status.get_by_role("link", name="Fake Timer")).to_have_count(0)
    expect(page.locator("[data-contact-age]")).to_contain_text("Last contact")
    shoot(page, "timer-status")
    assert_accessible(page)

    with htmx_post(page, "/timer/start/"):
        status.get_by_role("button", name="Start Race").click()
    assert "<remote-start/>" in timer_message(page, "HEARTBEAT")

    dismiss_toasts(page)
    page.get_by_role("button", name="Timer settings").click()
    sheet = page.locator('[data-sheet="timer"]')
    expect(sheet.get_by_label("Port")).to_be_visible()
    htmx_settled(page)
    shoot(page, "timer-settings", full_page=False)
    assert_accessible(page)
    with htmx_post(page, "/timer/settings/port/"):
        sheet.get_by_label("Port").select_option("COM2")
    pending = TimerSetting.objects.filter(event=event).exclude(pending=None)
    assert list(pending.values_list("kind", "pending")) == [
        (TimerSetting.Kind.PORTS, "COM2")
    ]

    dismiss_toasts(page)
    page.get_by_role("button", name="Timer settings").click()
    expect(sheet.get_by_label("Device")).to_be_visible()
    htmx_settled(page)
    with htmx_post(page, "/timer/settings/device/"):
        sheet.get_by_label("Device").select_option("Champ")

    dismiss_toasts(page)
    page.get_by_role("button", name="Timer settings").click()
    debug = sheet.get_by_role("switch", name="debug")
    expect(debug).to_be_visible()
    htmx_settled(page)
    with htmx_post(page, "/timer/settings/flag/"):
        debug.click()

    dismiss_toasts(page)
    page.get_by_role("button", name="Timer settings").click()
    delay = sheet.get_by_label("delay")
    expect(delay).to_have_value("300")
    htmx_settled(page)
    delay.fill("450")
    dismiss_toasts(page)
    sheet.get_by_role("button", name="Cancel").click()
    expect(delay).to_have_value("300")
    delay.fill("450")
    with htmx_post(page, "/timer/settings/flag/"):
        sheet.get_by_role("button", name="Save").click()
    expect(sheet).to_be_hidden()

    body = timer_message(page, "HEARTBEAT")
    assert '<assign-port port="COM2"/>' in body
    assert '<assign-device device="Champ"/>' in body
    assert '<assign-flag flag="debug" value="true"/>' in body
    assert '<assign-flag flag="delay" value="450"/>' in body


def test_status_parity_remote_logging_and_log_tail(login, event, tmp_path):
    """Parity: the remote logging switch, the log file location and the log
    tail."""
    RaceSettings.objects.filter(event=event).update(logs_directory=str(tmp_path))
    page = login("RaceCoordinator")
    connect(page)
    timer.append_log(event, b"timer says hello\n")
    open_status(page)
    log = page.locator("[data-log-text]")
    expect(log).to_contain_text("timer says hello")
    expect(page.locator("[data-log-path]")).to_contain_text(str(tmp_path))
    timer.append_log(event, b"and then some more\n")
    expect(log).to_contain_text("and then some more", timeout=5000)
    switch = page.get_by_role("switch", name="Remote logging")
    with htmx_post(page, "/timer/logging/"):
        switch.click()
    assert RaceSettings.objects.get(event=event).timer_send_logs
    assert "<remote-log send='true'/>" in timer_message(page, "HEARTBEAT")


def test_status_shows_the_unexpected_results(login, event):
    page = login("RaceCoordinator")
    RaceState.objects.filter(event=event).update(refused_results=3)
    open_status(page)
    found = page.locator("[data-refused-results]")
    expect(found).to_contain_text("3 unexpected results")
    found.get_by_role("link", name="Race dashboard").click()
    page.wait_for_url(re.compile(r"/dashboard/$"))


# ----- the timer test --------------------------------------------------------------


def test_timer_test_parity_simulate_lanes_and_results(login, event):
    """Parity: simulate racing and the live results table; lane count,
    reverse lanes and the lane toggles."""
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    page = login("RaceCoordinator")
    connect(page)
    page.goto("/timer/test/")
    live = page.locator("#timer-test-live")
    simulate = live.get_by_role("switch", name="Simulate racing")
    with htmx_post(page, "/timer/test/mode/"):
        simulate.click()
    assert RaceState.objects.get(event=event).timer_test
    expect(simulate).to_have_attribute("aria-checked", "true")

    lane_2 = live.get_by_role("button", name="Car in lane 2")
    with htmx_post(page, "/timer/test/lane/"):
        lane_2.click()
    expect(lane_2).to_have_attribute("aria-pressed", "false")
    assert TimerStatus.objects.get(event=event).test_lane_mask == 0b0010
    body = timer_message(page, "HEARTBEAT")
    assert 'lane-mask="13"' in body

    timer_message(page, "STARTED")
    timer_message(page, "FINISHED&lane1=2.5&lane3=2.4&lane4=2.6")
    row_3 = live.locator('tr[data-lane="3"]')
    expect(row_3.locator("[data-time]")).to_have_text("2.4", timeout=3000)
    expect(row_3.locator("[data-place]")).to_have_text("1")
    shoot(page, "timer-test")
    assert_accessible(page)

    with htmx_post(page, "/timer/test/reverse/"):
        live.get_by_role("switch", name="Number lanes in reverse").click()
    assert RaceSettings.objects.get(event=event).reverse_lanes
    dismiss_toasts(page)
    with htmx_post(page, "/timer/test/lanes/"):
        live.get_by_role("button", name="More: Lanes on the track").click()
    assert RaceSettings.objects.get(event=event).lane_count == 5
    expect(live.locator("tr[data-lane]")).to_have_count(5)

    with htmx_post(page, "/timer/test/mode/"):
        live.get_by_role("switch", name="Simulate racing").click()
    assert not RaceState.objects.get(event=event).timer_test


def test_identified_waits_for_hello(login, event, page_errors):
    """PHP's send_hello() sends IDENTIFIED from HELLO's success callback, so a
    HELLO that fails sends no IDENTIFIED."""
    page = login("RaceCoordinator")
    phone_only(page)
    page_errors.allow(r"request failed: \S*/action.php net::ERR_FAILED$")
    page_errors.allow(r"^console: Failed to load resource: net::ERR_FAILED$")
    sent = []

    def route(route):
        body = route.request.post_data or ""
        sent.append(body)
        if "message=HELLO" in body:
            route.abort()
        else:
            route.continue_()

    page.route("**/action.php", route)
    page.goto("/timer/fake/")
    for _ in range(30):
        if sum("HEARTBEAT" in b for b in sent) >= 2:
            break
        page.wait_for_timeout(100)
    assert any("message=HELLO" in b for b in sent)
    assert not any("IDENTIFIED" in b for b in sent), sent
    page.goto("/timer/")


def test_the_log_tail_backs_off(login, event):
    """timer-test.js:345-354: 50 ms after data, then twice the wait up to 1 s;
    each read starts at the last size seen. The whole log stays on the
    page, as in PHP."""
    page = login("RaceCoordinator")
    phone_only(page)
    reads = []
    big = "x" * 210_000 + "\n"

    def route(route):
        seek = int(route.request.url.split("seek=")[1])
        reads.append((seek, page.evaluate("performance.now()")))
        if len(reads) <= 2:
            data = big if len(reads) == 1 else "end of log\n"
            route.fulfill(json={"found": True, "size": seek + len(data), "data": data})
        else:
            route.fulfill(json={"found": True, "size": seek})

    page.route("**/timer/log/?*", route)
    open_status(page)
    for _ in range(60):
        if len(reads) >= 8:
            break
        page.wait_for_timeout(100)
    page.unroute("**/timer/log/?*")
    seeks = [s for s, _ in reads]
    assert seeks[:3] == [0, len(big), len(big) + 11]
    gaps = [round(b - a) for (_, a), (_, b) in zip(reads, reads[1:], strict=False)]
    # 50 ms after each of the two reads with data, then twice the wait each
    # time, up to 1 s. The first gap also holds drawing 210 KB of log.
    assert gaps[0] >= 40, gaps
    gaps = gaps[1:]
    expected = [50, 100, 200, 400, 800, 1000]
    assert len(gaps) >= len(expected), gaps
    # A loaded machine only adds delay, so each wait is a floor; the cap
    # shows as the step after 800 ms staying well short of 1600 ms.
    for gap, want in zip(gaps, expected, strict=False):
        assert gap >= want - 10, (gaps, expected)
    assert gaps[5] < 1400, gaps
    text = page.locator("[data-log-text]").text_content()
    assert text == big + "end of log\n"
