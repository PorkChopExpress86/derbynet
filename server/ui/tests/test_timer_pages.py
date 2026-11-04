"""The timer pages (#50): timer status and settings, the timer test, the
fake timer, and the browser timer at `/timer.php`.

The timer side of each test posts `timer-message` to `/action.php` as
`derby-timer.jar` does, from a session with only TIMER_MESSAGE."""

import hashlib
import re
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from django.utils import timezone
from django.utils.html import escapejs

from accounts.permissions import Perm
from core.models import RaceSettings, RaceState
from legacy_api.tests.conftest import log_in, post
from racing.models import TimerStatus
from racing.services import timer
from ui import timer_build
from ui.tests.race import race_setup, scheduled

pytestmark = pytest.mark.django_db

UA = "DerbyNet-Timer/test (jar)"
SIG = re.compile(r'name="sig" value="([^"]+)"')
WEBSITE = timer_build.REPO / "website"


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


@pytest.fixture
def jar(event):
    jar = Client(headers={"User-Agent": UA})
    log_in(jar, Perm.TIMER_MESSAGE, role="Timer")
    return jar


def message(client, fields: str) -> str:
    response = post(client, f"action=timer-message&message={fields}")
    assert response.status_code == 200
    return response.content.decode()


def connect(jar, lanes=4):
    message(jar, "HELLO")
    message(jar, f"IDENTIFIED&lane_count={lanes}&timer=TestTimer&human=Test+Timer")


def hx(client, url, data=None):
    if data is None:
        return client.get(url, HTTP_HX_REQUEST="true")
    return client.post(url, data, HTTP_HX_REQUEST="true")


def heat_ready_mask(body: str) -> int:
    found = re.search(r'<heat-ready lane-mask="(\d+)"', body)
    assert found, body
    return int(found.group(1))


# ----- the pages and their gates -------------------------------------------------


@pytest.mark.parametrize("url", ["/timer/", "/timer/test/"])
def test_the_status_and_test_pages_need_control_race(login, url):
    """S21: `timer-test.php` is anonymous in PHP."""
    for name in ("anonymous", "RaceCrew", "Timer"):
        response = login(name).get(url)
        assert response.status_code == 302, name
        assert response["Location"].startswith("/login/"), name
    assert login("RaceCoordinator").get(url).status_code == 200


def test_the_fake_timer_needs_check_in(login):
    assert login("anonymous").get("/timer/fake/").status_code == 302
    page = login("RaceCrew").get("/timer/fake/")
    assert page.status_code == 200
    assert b"data-fake-timer" in page.content


@pytest.mark.parametrize(
    ("php", "page"),
    [("timer-test.php", "/timer/test/"), ("fake-timer.php", "/timer/fake/")],
)
def test_the_php_pages_redirect(client, php, page):
    for prefix in ("/", "/derbynet/"):
        response = client.get(f"{prefix}{php}?back=coordinator.php")
        assert response.status_code == 301
        assert response["Location"] == f"{page}?back=coordinator.php"


def test_every_timer_control_needs_control_race(login, event):
    crew = login("RaceCrew")
    for url, data in [
        ("/timer/status/", None),
        ("/timer/settings/", None),
        ("/timer/log/", None),
        ("/timer/test/live/", None),
        ("/timer/start/", {}),
        ("/timer/logging/", {"timer-send-logs": "1"}),
        ("/timer/settings/port/", {"port": "a"}),
        ("/timer/settings/device/", {"device": "a"}),
        ("/timer/settings/flag/", {"flag": "a", "value": "1"}),
        ("/timer/test/mode/", {"test-mode": "1"}),
        ("/timer/test/lane/", {"lane": "1", "occupied": "0"}),
        ("/timer/test/lanes/", {"n-lanes": "3"}),
        ("/timer/test/reverse/", {"reverse-lanes": "1"}),
    ]:
        assert hx(crew, url, data).status_code == 403, url
    row = TimerStatus.objects.get(event=event)
    assert (row.test_lane_mask, RaceState.objects.get(event=event).timer_test) == (
        0,
        False,
    )


# ----- timer status ----------------------------------------------------------------


def test_the_status_card_shows_the_timer_and_its_client(coordinator, jar):
    connect(jar)
    message(jar, "HELLO&interface=browser&build=7")
    message(jar, "IDENTIFIED&lane_count=4&timer=T&human=Test+Timer&ident=v2&options=x")
    html = coordinator.get("/timer/").content.decode()
    assert 'data-timer-state="2"' in html
    assert "CONNECTED" in html and "sec." not in html
    assert "4 lanes" in html
    assert "Test Timer" in html and "v2" in html
    assert 'data-detail="interface">browser-7<' in html
    assert f'data-detail="agent">{UA}<' in html
    assert 'data-action="fake-timer"' not in html


def test_the_fake_timer_link_shows_while_no_timer_is_connected(coordinator):
    html = coordinator.get("/timer/").content.decode()
    assert "NOT CONNECTED" in html
    assert 'href="/timer/fake/"' in html


def test_a_malfunction_and_the_refused_results_show(coordinator, jar, event):
    connect(jar)
    message(jar, "MALFUNCTION&detectable=0&error=Gate+jammed")
    RaceState.objects.filter(event=event).update(refused_results=2)
    html = coordinator.get("/timer/").content.decode()
    assert "Gate jammed" in html
    assert "2 unexpected results from the timer have been received" in html
    assert 'href="/dashboard/"' in html


def test_start_race_shows_when_the_timer_offers_remote_start(coordinator, jar):
    connect(jar)
    assert 'data-action="start-race"' not in coordinator.get("/timer/").content.decode()
    message(jar, "HEARTBEAT&remote-start=YES")
    assert 'data-action="start-race"' in coordinator.get("/timer/").content.decode()
    assert hx(coordinator, "/timer/start/", {}).status_code == 200
    assert "<remote-start/>" in message(jar, "HEARTBEAT")


def test_the_status_poll_answers_204_through_a_heartbeat(coordinator, jar, event):
    connect(jar)
    first = hx(coordinator, "/timer/status/")
    sig = SIG.search(first.content.decode()).group(1)
    assert first["X-Timer-Contact"].isdigit()
    TimerStatus.objects.filter(event=event).update(
        last_contact=timezone.now() - timezone.timedelta(seconds=5)
    )
    again = coordinator.get("/timer/status/", {"sig": sig}, HTTP_HX_REQUEST="true")
    assert again.status_code == 204
    message(jar, "HEARTBEAT")
    later = coordinator.get("/timer/status/", {"sig": sig}, HTTP_HX_REQUEST="true")
    assert later.status_code == 204
    assert int(later["X-Timer-Contact"]) > int(again["X-Timer-Contact"])
    message(jar, "IDENTIFIED&lane_count=6")
    assert hx(coordinator, f"/timer/status/?sig={sig}").status_code == 200


def test_remote_logging_writes_the_setting(coordinator, event, jar):
    connect(jar)
    assert (
        hx(coordinator, "/timer/logging/", {"timer-send-logs": "1"}).status_code == 200
    )
    assert RaceSettings.objects.get(event=event).timer_send_logs
    assert "<remote-log send='true'/>" in message(jar, "HEARTBEAT")
    hx(coordinator, "/timer/logging/", {"timer-send-logs": "0"})
    assert not RaceSettings.objects.get(event=event).timer_send_logs


def test_the_log_tail_reads_from_its_seek(coordinator, jar, event, tmp_path, settings):
    RaceSettings.objects.filter(event=event).update(logs_directory=str(tmp_path))
    connect(jar)
    timer.append_log(event, b"first line\n")
    found = coordinator.get("/timer/log/?seek=0").json()
    assert found["data"] == "first line\n"
    assert found["size"] == 11
    assert found["path"].startswith(str(tmp_path))
    assert "data" not in coordinator.get("/timer/log/?seek=11").json()
    timer.append_log(event, b"second\n")
    assert coordinator.get("/timer/log/?seek=11").json()["data"] == "second\n"
    settings.LOCK_SETTINGS = True
    assert "path" not in coordinator.get("/timer/log/?seek=0").json()


def test_the_log_tail_with_no_log(coordinator):
    found = coordinator.get("/timer/log/?seek=junk").json()
    assert found == {"found": False, "size": 0, "path": ""}


# ----- the settings sheet ----------------------------------------------------------

FLAGS = (
    "FLAGS&flag-debug=bool:false&desc-debug=Debug+output&flag-delay=int:300"
    "&flag-name=string:abc&flag-odd=other:x&ports=COM1,COM2"
    "&device-SmartLine=SmartLine+timer&device-Champ=Champ+timer"
)


def test_the_sheet_lists_ports_devices_and_each_flag_type(coordinator, jar):
    connect(jar)
    message(jar, FLAGS)
    html = hx(coordinator, "/timer/settings/").content.decode()
    assert '<option value="" selected>Auto port</option>' in html
    assert '<option value="COM2">COM2</option>' in html
    assert '<option value="" selected>Auto device</option>' in html
    assert '<option value="Champ">Champ timer</option>' in html
    kinds = dict(re.findall(r'data-flag="(\w+)" data-kind="(\w+)"', html))
    assert kinds == {"debug": "bool", "delay": "text", "name": "text", "odd": "value"}
    assert 'role="switch"' in html and "Debug output" in html
    assert html.count('data-action="save-flag"') == 2


def test_the_sheet_says_when_the_timer_reported_nothing(coordinator):
    assert "data-no-settings" in hx(coordinator, "/timer/settings/").content.decode()


def test_assignments_go_out_on_the_next_heartbeat(coordinator, jar):
    """Acceptance: a flag, a port and a device from the sheet."""
    connect(jar)
    message(jar, FLAGS)
    sheet = {"HTTP_HX_TARGET": "timer-sheet-body"}
    for url, data in [
        ("/timer/settings/flag/", {"flag": "debug", "kind": "bool", "value": "1"}),
        ("/timer/settings/flag/", {"flag": "delay", "value": "450"}),
        ("/timer/settings/port/", {"port": "COM2"}),
        ("/timer/settings/device/", {"device": "Champ"}),
    ]:
        response = coordinator.post(url, data, HTTP_HX_REQUEST="true", **sheet)
        assert response.status_code == 200, url
        assert "The timer gets it next." in response.content.decode()
    body = message(jar, "HEARTBEAT")
    assert '<assign-flag flag="debug" value="true"/>' in body
    assert '<assign-flag flag="delay" value="450"/>' in body
    assert '<assign-port port="COM2"/>' in body
    assert '<assign-device device="Champ"/>' in body
    assert "<assign-" not in message(jar, "HEARTBEAT")


def test_auto_port_and_device_send_no_name(coordinator, jar):
    connect(jar)
    message(jar, FLAGS)
    hx(coordinator, "/timer/settings/port/", {"port": ""})
    hx(coordinator, "/timer/settings/device/", {"device": ""})
    body = message(jar, "HEARTBEAT")
    assert "<assign-port/>" in body and "<assign-device/>" in body


def test_a_flag_needs_its_name(coordinator):
    assert hx(coordinator, "/timer/settings/flag/", {"value": "1"}).status_code == 422


# ----- the timer test --------------------------------------------------------------


def test_lane_two_off_leaves_it_out_of_heat_ready(coordinator, jar, event):
    """Acceptance: with simulate racing on, toggling lane 2 off sends a
    heat-ready lane mask without lane 2."""
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    message(jar, "HELLO")
    message(jar, "IDENTIFIED&lane_count=4&timer=TestTimer")
    message(jar, "HEARTBEAT")
    assert hx(coordinator, "/timer/test/mode/", {"test-mode": "1"}).status_code == 200
    assert heat_ready_mask(message(jar, "HEARTBEAT")) == 0b1111
    response = hx(coordinator, "/timer/test/lane/", {"lane": "2", "occupied": "0"})
    assert response.status_code == 200
    assert 'data-lane-toggle="2"' in response.content.decode()
    mask = heat_ready_mask(message(jar, "HEARTBEAT"))
    assert mask == 0b1101
    # The same toggle again changes nothing.
    hx(coordinator, "/timer/test/lane/", {"lane": "2", "occupied": "0"})
    assert TimerStatus.objects.get(event=event).test_lane_mask == 0b0010
    hx(coordinator, "/timer/test/lane/", {"lane": "2", "occupied": "1"})
    assert heat_ready_mask(message(jar, "HEARTBEAT")) == 0b1111


@pytest.mark.parametrize("lane", ["0", "5", "x", ""])
def test_a_lane_the_track_lacks_is_refused(coordinator, event, lane):
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    response = hx(coordinator, "/timer/test/lane/", {"lane": lane, "occupied": "0"})
    assert response.status_code == 422
    assert TimerStatus.objects.get(event=event).test_lane_mask == 0


def test_the_live_table_shows_the_test_heat(coordinator, jar, event):
    RaceSettings.objects.filter(event=event).update(lane_count=4)
    connect(jar)
    hx(coordinator, "/timer/test/lane/", {"lane": "2", "occupied": "0"})
    message(jar, "HEARTBEAT")
    message(jar, "STARTED")
    message(jar, "FINISHED&lane1=2.5&lane3=2.4&lane4=2.6")
    first = hx(coordinator, "/timer/test/live/")
    html = first.content.decode()
    assert 'data-test-running="true"' in html
    assert re.search(r'aria-pressed="false" aria-label="Car in lane 2"', html)
    rows = re.findall(
        r'<tr data-lane="(\d)">.*?data-time>([\d.]*)</td>.*?data-place>(\d*)</td>',
        html,
        re.S,
    )
    assert rows == [
        ("1", "2.5", "2"),
        ("2", "", ""),
        ("3", "2.4", "1"),
        ("4", "2.6", "3"),
    ]
    sig = SIG.search(html).group(1)
    assert hx(coordinator, f"/timer/test/live/?sig={sig}").status_code == 204
    hx(coordinator, "/timer/test/mode/", {"test-mode": "0"})
    assert hx(coordinator, f"/timer/test/live/?sig={sig}").status_code == 200


def test_lane_count_and_reverse_lanes_write_the_settings(coordinator, event):
    assert hx(coordinator, "/timer/test/lanes/", {"n-lanes": "6"}).status_code == 200
    assert (
        hx(coordinator, "/timer/test/reverse/", {"reverse-lanes": "1"}).status_code
        == 200
    )
    found = RaceSettings.objects.get(event=event)
    assert (found.lane_count, found.reverse_lanes) == (6, True)
    hx(coordinator, "/timer/test/reverse/", {"reverse-lanes": "0"})
    assert not RaceSettings.objects.get(event=event).reverse_lanes
    assert hx(coordinator, "/timer/test/lanes/", {"n-lanes": "-1"}).status_code == 422


def test_the_lane_count_is_refused_while_heats_are_unraced(coordinator, event):
    scheduled(race_setup(event)["Wolves"], race=False)
    response = hx(coordinator, "/timer/test/lanes/", {"n-lanes": "6"})
    assert response.status_code == 422
    assert "Remove unraced schedules" in response.content.decode()
    assert RaceSettings.objects.get(event=event).lane_count == 4


# ----- the browser timer ------------------------------------------------------------


def test_the_browser_timer_needs_the_timer_role(login):
    response = login("anonymous").get("/timer.php")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")
    assert login("RaceCrew").get("/timer.php").status_code == 302
    assert login("Timer").get("/timer.php").status_code == 200


def test_only_control_race_sees_the_timer_settings_button(login):
    """D53: the race-day laptop logs in as RaceCoordinator."""
    assert b"timer-settings-button" not in login("Timer").get("/timer.php").content
    page = login("RaceCoordinator").get("/timer.php").content.decode()
    assert 'id="timer-settings-button"' in page


def test_the_browser_timer_is_its_template_with_the_version_filled(login):
    page = login("Timer").get("/timer.php").content.decode()
    for token in ("@BRANCH@", "@REV_COUNT@", "@BUILD_DATE@", "@PHP", "@ELECTRON"):
        assert token not in page
    assert re.search(r'branch: "[^"]+",\s+revision: \d+,\s+date: "[^"]*"', page)
    assert "serial_port_event_listener.js" not in page
    assert "g_standalone = true" not in page
    assert f"window.location.replace('{escapejs('/timer/no-serial/')}')" in page
    assert page.startswith("<!DOCTYPE html>")
    assert '<script type="text/javascript" src="js/timer/main.js"></script>' in page


def test_the_debian_prefix_serves_the_timer_and_its_files(login):
    timer_role = login("Timer")
    assert timer_role.get("/derbynet/timer.php").status_code == 200
    assert timer_role.get("/derbynet/js/jquery.js").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "js/jquery.js",
        "js/jquery-ui.min.js",
        "js/mobile.js",
        "js/ajax-setup.js",
        "js/modal.js",
        "css/global.css",
        "css/mobile.css",
        "img/derbynet.png",
        "img/gear_icon.png",
        "img/small-check.png",
        "img/small-cross.png",
        "img/status/ok.png",
        "img/status/not_connected.png",
        "img/status/trouble.png",
        "img/status/unknown.png",
        "img/timer/trouble-tiny.png",
    ],
)
def test_the_legacy_files_are_served_at_their_paths(client, path):
    response = client.get(f"/{path}")
    assert response.status_code == 200
    body = b"".join(response.streaming_content)
    assert body == (timer_build.ASSETS / path).read_bytes()


@pytest.mark.parametrize(
    "path",
    ["js/../../timer_build.py", "js/%2e%2e/%2e%2e/views.py", "img/nope.png", "js/"],
)
def test_nothing_else_is_served(client, path):
    assert client.get(f"/{path}").status_code == 404


def test_the_legacy_files_are_copies_of_website():
    """The copies the browser timer loads match website/ until #4 deletes it."""
    if not WEBSITE.exists():
        pytest.skip("website/ is gone")
    generated = ("js/timer/", "css/timer.css")
    copies = [
        p
        for p in timer_build.ASSETS.rglob("*")
        if p.is_file()
        and not p.relative_to(timer_build.ASSETS).as_posix().startswith(generated)
    ]
    assert len(copies) == 16
    for copy in copies:
        original = WEBSITE / copy.relative_to(timer_build.ASSETS)
        assert (
            hashlib.sha256(copy.read_bytes()).digest()
            == hashlib.sha256(original.read_bytes()).digest()
        ), copy


def test_the_committed_template_matches_templates_timer_html():
    assert timer_build.check_template(), timer_build.OUT_OF_DATE.format(
        timer_build.TEMPLATE
    )


def test_the_template_conversion_refuses_a_moved_token():
    source = timer_build.SOURCE_HTML.read_text()
    with pytest.raises(timer_build.BuildError):
        timer_build.django_template(source.replace("@PHP_END@", ""))
    with pytest.raises(timer_build.BuildError):
        timer_build.django_template(source.replace('id="timer-settings-button"', ""))
    with pytest.raises(timer_build.BuildError):
        timer_build.django_template(source + "{{ x }}")


def test_profiles_js_fills_the_template(tmp_path, monkeypatch):
    monkeypatch.setattr(timer_build, "PROFILES", tmp_path / "js" / "profiles.js")
    written = timer_build.profiles_js('[\n  {"name": "Fake"}\n]\n')
    text = written.read_text()
    assert 'var _all_the_timer_profiles =  [\n  {"name": "Fake"}\n];' in text
    with pytest.raises(timer_build.BuildError):
        timer_build.profiles_js("Exception in thread main")


def test_copy_sources_copies_every_script_and_the_css(tmp_path, monkeypatch):
    monkeypatch.setattr(timer_build, "ASSETS", tmp_path)
    written = timer_build.copy_sources()
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    src = timer_build.TIMER_SRC
    assert names == {f"js/timer/{p.name}" for p in (src / "js").glob("*.js")} | {
        "css/timer.css"
    }


def test_the_no_serial_page(login):
    html = login("Timer").get("/timer/no-serial/").content.decode()
    assert "Chrome" in html and "Edge" in html
    assert "data-not-secure" in html
    assert "/timer.php" in html


def test_the_dashboard_links_reach_the_timer_pages(coordinator):
    html = coordinator.get("/dashboard/").content.decode()
    assert 'href="/timer/" data-action="timer-status"' in html
    assert 'href="/timer/test/" data-action="timer-test"' in html
    assert "dashboard.openTimer('/timer.php')" in html


def test_the_fake_timer_posts_to_action_php(coordinator, event):
    RaceSettings.objects.filter(event=event).update(lane_count=6)
    html = coordinator.get("/timer/fake/").content.decode()
    assert "fakeTimer('/action.php', '/', 6)" in html


def test_the_legacy_timer_page_reference_is_gone_from_the_puppeteer_list():
    """The Playwright test of /timer.php replaces its load in
    all-pages-test.js."""
    source = Path(timer_build.REPO / "testing" / "puppeteer" / "all-pages-test.js")
    assert '"timer.php"' not in source.read_text()


def test_build_timer_check_passes_and_fails(tmp_path, monkeypatch):
    call_command("build_timer", "--check", stdout=StringIO())
    stale = tmp_path / "browser.html"
    stale.write_text("old")
    monkeypatch.setattr(timer_build, "TEMPLATE", stale)
    with pytest.raises(CommandError, match="does not match"):
        call_command("build_timer", "--check", stdout=StringIO())


def test_build_timer_without_profiles_writes_the_template_and_copies(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(timer_build, "TEMPLATE", tmp_path / "t" / "browser.html")
    monkeypatch.setattr(timer_build, "ASSETS", tmp_path / "assets")
    out = StringIO()
    call_command("build_timer", "--no-profiles", stdout=out)
    assert timer_build.check_template()
    assert (tmp_path / "assets" / "js" / "timer" / "main.js").exists()
    assert "profiles.js" not in out.getvalue()


def test_a_track_with_no_lane_count_shows_one_lane(event):
    from ui.timer import state

    RaceSettings.objects.filter(event=event).update(lane_count=0)
    assert [lane.lane for lane in state.test(event).lanes] == [1]


def _workflow_paths(workflow: Path) -> dict[str, list[str]]:
    """Each trigger's `paths:` list in a workflow's `on:` block."""
    paths: dict[str, list[str]] = {}
    trigger = None
    for line in workflow.read_text().splitlines():
        if re.match(r"\S", line) and not line.startswith("on:"):
            if paths:
                break
            continue
        if found := re.match(r"  (\w+):", line):
            trigger = found[1]
        elif re.match(r"    paths:", line):
            paths[trigger] = []
        elif (found := re.match(r"      - (\S+)", line)) and trigger in paths:
            paths[trigger].append(found[1])
    return paths


def test_the_server_workflow_runs_when_a_build_timer_input_changes():
    """`build_timer` reads files outside server/, and the ui job runs it, so
    the server workflow's path filters name each one, the same for a push and
    a pull request (#50 retro: a partial edit left the two lists apart)."""
    paths = _workflow_paths(timer_build.REPO / ".github" / "workflows" / "server.yml")
    assert set(paths) == {"push", "pull_request"}
    assert paths["push"] == paths["pull_request"]
    assert len(set(paths["push"])) == len(paths["push"])
    inputs = [
        timer_build.SOURCE_HTML,
        timer_build.PROFILES_TEMPLATE,
        timer_build.TIMER_SRC / "js" / "main.js",
        timer_build.JAVA_SRC / "org",
        *timer_build.JAVA_LIBS,
    ]
    names = [p.relative_to(timer_build.REPO).as_posix() for p in inputs]
    unfiltered = [
        name
        for name in names
        if not any(
            name == f or (f.endswith("/**") and name.startswith(f[:-2]))
            for f in paths["push"]
        )
    ]
    assert unfiltered == []
