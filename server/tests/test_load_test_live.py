"""The race-day load script (#37) for 25 s against a real Django server.

A Django dev server runs as `.github/workflows/server.yml` starts it, on a
fresh SQLite database of its own. The script sets the event up with one
group of three racers, so the timer races its four heats and the schedule
restarts at least once, and every client type runs at its real rate. The test checks
each client made about the requests its pacing allows, that nothing failed,
and that the server recorded every heat the timer finished.
"""

import json
import os
import socket
import subprocess
import sys
import time
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
    "timer-message": (DURATION / 0.5 * 0.8, DURATION / 0.5),
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
    started = report["endpoints"]["timer-message STARTED"]["requests"]
    assert started - timer["finished_sent"] in (0, 1)


def test_the_report_samples_the_server(report):
    server = report["server"]
    assert server["rss_max_mb"] > 0
    assert server["cpu_mean_pct"] > 0
