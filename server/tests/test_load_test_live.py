"""The race-day load script (#37) for 25 s against a real Django server.

A Django dev server runs as `.github/workflows/server.yml` starts it, on a
fresh SQLite database of its own. It pins SQLite even on the Postgres CI
leg, because the load question #37 asks is the race-day one: SQLite in WAL
mode on a Raspberry Pi, where the timer's writes and the polls share one
file. The script sets the event up with one group of three racers, so the
timer races its four heats and the schedule restarts at least once, and
every client type runs at its real rate. The tests check each client made
about the requests its pacing allows, that nothing failed, and that the
server recorded every heat the timer finished. Two more check the logins
the script uses: kiosks poll anonymously, and the dashboard and replay
polls need a role with PRESENT_AWARDS and CONTROL_RACE, which
RaceCoordinator has and Timer has not.
"""

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
DURATION = 25


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("server")
    env = {
        **os.environ,
        "DATABASE_URL": "",
        "DERBYNET_DATA_DIR": str(root / "data"),
        "DERBYNET_CONFIG_DIR": str(root / "config"),
    }
    manage = [sys.executable, "manage.py"]
    for command in (["migrate", "-v0"], ["create_test_event"]):
        subprocess.run(manage + command, cwd=SERVER_DIR, env=env, check=True)
    url = f"http://127.0.0.1:{free_port()}"
    with open(root / "server.log", "wb") as log:
        process = subprocess.Popen(
            manage + ["runserver", url.removeprefix("http://"), "--noreload"],
            cwd=SERVER_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{url}/action.php?query=role.list", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail((root / "server.log").read_text())
        yield url, process.pid, root / "server.log"
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def report(server, tmp_path_factory):
    url, pid, log = server
    out = tmp_path_factory.mktemp("report") / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.load_test",
            "--url",
            url,
            "--setup",
            "--racers",
            "3",
            "--groups",
            "1",
            "--stage-seconds",
            "0.5",
            "--duration",
            str(DURATION),
            "--pid",
            str(pid),
            "--json",
            str(out),
        ],
        cwd=SERVER_DIR,
        capture_output=True,
        text=True,
        timeout=DURATION + 90,
    )
    assert result.returncode == 0, (
        result.stdout + result.stderr + log.read_text()[-5000:]
    )
    return json.loads(out.read_text())


# Requests each client type can make in DURATION seconds, at most and at
# least. Kiosks start their poll.kiosk at a random point in their first 5 s
# and their content poll 1 to 1.5 s in, as the pages do.
EXPECTED = {
    "poll.kiosk": (10 * DURATION // 5 - 1, 10 * DURATION // 5),
    "poll": (10 * (DURATION - 1.5) / 0.5 * 0.8, 10 * DURATION / 0.5),
    # Heartbeats 0.5 s apart, plus STARTED and FINISHED sent at once, as the
    # jar sends them: a heat takes at least 0.5 s staging and 2.5 s racing.
    # The handshake adds HELLO, FLAGS and IDENTIFIED.
    "timer-message": (DURATION / 0.5 * 0.8, DURATION / 0.5 + 2 * DURATION / 3 + 3),
    "poll.coordinator": (DURATION // 2, DURATION // 2 + 1),
    "replay.message": (DURATION / 0.25 * 0.9, DURATION / 0.25),
    "poll.kiosk.all": (DURATION / 2 * 0.85, DURATION / 2 + 1),
}


def requests_of(report: dict, label: str) -> int:
    return sum(
        row["requests"]
        for name, row in report["endpoints"].items()
        if name == label or name.startswith(label + " ")
    )


@pytest.mark.parametrize("label", EXPECTED)
def test_each_client_type_polls_at_its_rate(report, label):
    low, high = EXPECTED[label]
    assert low <= requests_of(report, label) <= high


def test_nothing_failed(report):
    assert report["totals"]["errors"] == 0
    assert report["totals"]["server_errors"] == 0
    assert report["totals"]["locked"] == 0
    assert report["failures"] == []


def test_the_timer_raced_full_heats_and_the_server_recorded_each(report):
    timer = report["timer"]
    assert timer["finished_sent"] >= 4
    assert timer["finished_ok"] == timer["finished_sent"]
    assert timer["finished_recorded"] == timer["finished_ok"]
    assert timer["passes"] >= 1
    # HELLO's reply carries <query/>, which the jar answers with FLAGS.
    assert report["endpoints"]["timer-message FLAGS"]["requests"] >= 1
    started = report["endpoints"]["timer-message STARTED"]["requests"]
    assert started - timer["finished_sent"] in (0, 1)


def test_the_report_samples_the_server(report):
    server = report["server"]
    assert server["rss_max_mb"] > 0
    assert server["cpu_mean_pct"] > 0


def browser(url: str, role: str | None = None, password: str = ""):
    """A cookie-keeping client, logged in as `role` when one is given."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def call(**params) -> dict:
        if "action" in params:
            data = urllib.parse.urlencode(params).encode()
            reply = opener.open(f"{url}/action.php", data, timeout=10)
        else:
            query = urllib.parse.urlencode(params)
            reply = opener.open(f"{url}/action.php?{query}", timeout=10)
        return json.loads(reply.read())

    if role is not None:
        login = call(action="role.login", name=role, password=password)
        assert login["outcome"]["summary"] == "success"
    return call


def refused(reply: dict) -> bool:
    return reply.get("outcome", {}).get("code") == "notauthorized"


def test_kiosk_polls_need_no_login(server):
    kiosk = browser(server[0])
    setting = kiosk(query="poll.kiosk", address="login-check")
    assert "kiosk-setting" in setting
    now_racing = kiosk(query="poll", values="current-heat", roundid=0, heat=0)
    assert "current-heat" in now_racing


def test_dashboard_and_replay_need_the_coordinator(server):
    url = server[0]
    replay = {"action": "replay.message", "status": 0, "finished-replay": 0}
    for client in (browser(url), browser(url, "Timer")):
        assert refused(client(query="poll.kiosk.all"))
        assert refused(client(**replay))
    coordinator = browser(url, "RaceCoordinator", "doyourbest")
    assert "kiosks" in coordinator(query="poll.kiosk.all")
    assert coordinator(**replay)["outcome"]["summary"] == "success"
