"""The race-day load script (#37), `tools/load_test.py`, without a real
server: the pacing, the timer's heats, the report maths, the `/proc` reader
and the exit status, with a fake clock and a fake transport. The last tests
run the script as a command against a stub HTTP server, to check that it
fails on a 5xx and on a slow endpoint, and that it stops cleanly on SIGINT.
"""

import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tools import load_test as lt

SERVER_DIR = Path(__file__).resolve().parent.parent


class FakeClock:
    """A clock that moves only when something waits or a request takes time."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.now += max(0.0, seconds)
        return False  # never stopped


class CountingActor:
    """An actor whose every tick is one request that takes `latency`."""

    def __init__(self, clock: FakeClock, latency: float):
        self.clock, self.latency, self.ticks = clock, latency, []

    def tick(self) -> None:
        self.ticks.append(self.clock())
        self.clock.now += self.latency


# ----- pacing --------------------------------------------------------------------


def test_every_keeps_its_grid_and_skips_a_tick_missed_while_busy():
    # setInterval with a guard against a second request in flight, as
    # coordinator-poll.js and replay.php do.
    assert lt.Every(2.0).next(scheduled=10.0, done=10.1) == pytest.approx(12.0)
    assert lt.Every(0.25).next(scheduled=10.0, done=10.4) == pytest.approx(10.5)
    assert lt.Every(0.25).next(scheduled=10.0, done=10.5) == pytest.approx(10.5)
    assert lt.Every(0.25).rate == pytest.approx(4.0)


def test_after_waits_its_delay_from_the_response():
    # setTimeout chained from the response, as now-racing-poller.js,
    # kiosk-dashboard.js and derby-timer.jar's heartbeat do.
    assert lt.After(0.5).next(scheduled=10.0, done=10.1) == pytest.approx(10.6)
    assert lt.After(2.0).rate == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("pacer", "latency", "expected"),
    [
        (lt.Every(2.0), 0.1, 5),  # 0, 2, 4, 6, 8
        (lt.After(0.5), 0.1, 17),  # every 0.6 s
        (lt.Every(0.25), 0.4, 20),  # every other tick, every 0.5 s
        (lt.Every(5.0), 0.01, 2),  # 0, 5
    ],
)
def test_run_actor_fires_at_the_pacers_rate(pacer, latency, expected):
    clock = FakeClock()
    actor = CountingActor(clock, latency)
    lt.run_actor(
        actor, pacer, first_at=0.0, clock=clock, wait=clock.wait, deadline=10.0
    )
    assert len(actor.ticks) == expected


def test_run_actor_starts_at_its_offset_and_stops_when_told():
    clock = FakeClock()
    actor = CountingActor(clock, 0.0)
    stopped = threading.Event()

    def wait(seconds):
        clock.wait(seconds)
        if clock() >= 3.0:
            stopped.set()
        return stopped.is_set()

    lt.run_actor(
        actor, lt.Every(1.0), first_at=0.5, clock=clock, wait=wait, deadline=60.0
    )
    assert actor.ticks == [0.5, 1.5, 2.5]


def test_run_actor_keeps_going_after_a_tick_raises():
    clock = FakeClock()

    class Flaky(CountingActor):
        def tick(self):
            super().tick()
            if len(self.ticks) == 2:
                raise RuntimeError("one bad response")

    actor = Flaky(clock, 0.0)
    lt.run_actor(
        actor, lt.Every(1.0), first_at=0.0, clock=clock, wait=clock.wait, deadline=4.0
    )
    assert len(actor.ticks) == 4


# ----- the timer -----------------------------------------------------------------

SUCCESS = '<action-response action="timer-message"><success/>\n</action-response>'


def ready(roundid: int, heat: int, mask: int) -> str:
    return (
        '<action-response action="timer-message"><success/>'
        f'<heat-ready lane-mask="{mask}" class="Lions" round="1" roundid="{roundid}"'
        f' heat="{heat}" lanes="4"/></action-response>'
    )


def test_timer_says_hello_identifies_then_heartbeats():
    sim = lt.TimerSim(lanes=4, rng=random.Random(1))
    label, params = sim.next_message(0.0)
    assert (label, params["message"]) == ("timer-message HELLO", "HELLO")
    assert params["interface"] == "jar"
    sim.on_response(label, SUCCESS, 0.1)
    label, params = sim.next_message(0.5)
    assert params["message"] == "IDENTIFIED"
    assert params["lane_count"] == "4"
    assert params["confirmed"] == "1"
    sim.on_response(label, SUCCESS, 0.6)
    label, params = sim.next_message(1.0)
    assert label == "timer-message HEARTBEAT"
    assert params == {
        "action": "timer-message",
        "message": "HEARTBEAT",
        "confirmed": "1",
    }


def test_timer_races_the_heat_it_is_given():
    sim = lt.TimerSim(lanes=4, rng=random.Random(2), stage_seconds=1.0)
    sim.on_response(*sim.next_message(0.0)[:1], SUCCESS, 0.0)
    label, _ = sim.next_message(0.5)
    sim.on_response(label, ready(7, 3, 0b0101), 0.5)  # lanes 1 and 3
    # Staged: heartbeats until the stage delay is over, then STARTED.
    assert sim.next_message(1.0)[1]["message"] == "HEARTBEAT"
    label, params = sim.next_message(1.5)
    assert params["message"] == "STARTED"
    sim.on_response(label, SUCCESS, 1.5)
    # Running: heartbeats until the slowest car is in, then FINISHED.
    assert sim.next_message(2.0)[1]["message"] == "HEARTBEAT"
    label, params = sim.next_message(10.0)
    assert params["message"] == "FINISHED"
    assert (params["roundid"], params["heat"]) == ("7", "3")
    assert set(params) == {"action", "message", "roundid", "heat", "lane1", "lane3"}
    for lane in ("lane1", "lane3"):
        assert 2.5 <= float(params[lane]) <= 4.0
        assert len(params[lane].split(".")[1]) == 4
    assert sim.finished_sent == 1 and sim.finished_ok == 0
    # The FINISHED reply carries the next heat-ready.
    sim.on_response(label, ready(7, 4, 0b1111), 10.1)
    assert sim.finished_ok == 1
    assert sim.staged == (7, 4, 0b1111)


def test_timer_finished_waits_for_the_slowest_lane():
    sim = lt.TimerSim(lanes=4, rng=random.Random(3), stage_seconds=0.0)
    sim.on_response("timer-message IDENTIFIED", ready(1, 1, 0b1111), 0.0)
    label, _ = sim.next_message(0.0)
    sim.on_response(label, SUCCESS, 0.0)  # STARTED at 0.0
    slowest = max(float(t) for t in sim.race_times.values())
    assert sim.next_message(slowest - 0.01)[1]["message"] == "HEARTBEAT"
    assert sim.next_message(slowest)[1]["message"] == "FINISHED"


def test_timer_drops_its_heat_on_abort_and_counts_a_refused_finish():
    sim = lt.TimerSim(lanes=4, rng=random.Random(4), stage_seconds=0.0)
    sim.on_response("timer-message IDENTIFIED", ready(1, 1, 0b1111), 0.0)
    sim.on_response(
        "timer-message HEARTBEAT", SUCCESS.replace("<success/>", "<abort/>"), 0.1
    )
    assert sim.staged is None
    assert sim.next_message(0.5)[1]["message"] == "HEARTBEAT"
    sim.on_response("timer-message HEARTBEAT", ready(1, 2, 0b1111), 0.6)
    label, _ = sim.next_message(0.6)
    sim.on_response(label, SUCCESS, 0.6)
    label, _ = sim.next_message(9.0)
    sim.on_response(
        label, '<action-response><failure code="x">no</failure></action-response>', 9.0
    )
    assert (sim.finished_sent, sim.finished_ok) == (1, 0)


def test_timer_reports_how_long_it_has_waited_for_a_heat():
    sim = lt.TimerSim(lanes=4, rng=random.Random(5))
    sim.on_response("timer-message HELLO", SUCCESS, 0.0)
    sim.on_response("timer-message IDENTIFIED", SUCCESS, 1.0)
    assert sim.idle_seconds(4.0) == pytest.approx(3.0)
    sim.on_response("timer-message HEARTBEAT", ready(1, 1, 1), 4.0)
    assert sim.idle_seconds(9.0) == 0.0


# ----- responses, statistics and the report ---------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (200, '{"current-heat": {}}', None),
        (200, '{"outcome": {"summary": "success"}}', None),
        (200, '{"outcome": {"summary": "failure", "code": "x"}}', "failure"),
        (200, "<action-response><success/></action-response>", None),
        (
            200,
            '<action-response><failure code="notauthorized"/></action-response>',
            "failure",
        ),
        (200, "OperationalError: database is locked", "locked"),
        (500, "Server Error (500)", "5xx"),
        (503, "", "5xx"),
        (404, "", "http"),
        (None, "", "transport"),
    ],
)
def test_classify(status, body, kind):
    assert lt.classify(status, body) == kind


def test_percentile_is_nearest_rank():
    values = list(range(1, 101))
    assert lt.percentile(values, 50) == 50
    assert lt.percentile(values, 95) == 95
    assert lt.percentile(values, 99) == 99
    assert lt.percentile(values, 100) == 100
    assert lt.percentile([7.0], 99) == 7.0
    assert lt.percentile([3, 1, 2], 50) == 2  # sorts first
    assert lt.percentile([], 50) is None


def make_stats() -> lt.Stats:
    stats = lt.Stats()
    for ms in range(1, 101):
        stats.record("poll", ms / 1000, None)
    stats.record("poll.kiosk", 0.004, None)
    stats.record("poll.kiosk", 0.006, "5xx")
    stats.record("poll.kiosk", 0.008, "locked")
    stats.record("setup role.login", 0.5, None)
    return stats


CLIENTS = [
    lt.ClientType("kiosk content", 2, lt.After(0.5), ("poll",)),
    lt.ClientType("kiosk", 2, lt.Every(5.0), ("poll.kiosk",)),
]


def test_summarize_counts_errors_and_latency_per_endpoint():
    report = lt.summarize(make_stats(), elapsed=10.0, clients=CLIENTS)
    poll = report["endpoints"]["poll"]
    assert poll["requests"] == 100 and poll["errors"] == 0
    assert (
        poll["p50_ms"],
        poll["p95_ms"],
        poll["p99_ms"],
        poll["max_ms"],
    ) == pytest.approx((50, 95, 99, 100))
    kiosk = report["endpoints"]["poll.kiosk"]
    assert (
        kiosk["requests"],
        kiosk["errors"],
        kiosk["server_errors"],
        kiosk["locked"],
    ) == (
        3,
        2,
        1,
        1,
    )
    assert report["totals"] == {
        "requests": 104,
        "errors": 2,
        "server_errors": 1,
        "locked": 1,
        "transport_errors": 0,
    }


def test_summarize_compares_each_clients_rate_with_its_target():
    report = lt.summarize(make_stats(), elapsed=10.0, clients=CLIENTS)
    content, kiosk = report["rates"]
    assert content["client"] == "kiosk content"
    assert content["target_per_s"] == pytest.approx(4.0)  # 2 kiosks at 2 Hz
    assert content["achieved_per_s"] == pytest.approx(10.0)
    # Chained from the response: at a 50.5 ms mean, each kiosk can poll at
    # most 1 / 0.5505 s.
    assert content["expected_per_s"] == pytest.approx(2 / 0.5505, rel=1e-3)
    assert kiosk["target_per_s"] == pytest.approx(0.4)
    assert kiosk["expected_per_s"] == pytest.approx(0.4)
    assert kiosk["achieved_per_s"] == pytest.approx(0.3)


def test_verdict_names_each_failed_check():
    report = lt.summarize(make_stats(), elapsed=10.0, clients=CLIENTS)
    report["timer"] = {"finished_sent": 5, "finished_ok": 5, "finished_recorded": 4}
    failures = lt.verdict(report, max_p95_ms=50, max_p99_ms=None)
    text = "\n".join(failures)
    assert "5xx" in text
    assert "database is locked" in text
    assert "FINISHED" in text
    assert "poll p95 95.0 ms > 50 ms" in text
    # Setup requests count neither as errors nor against the thresholds.
    assert "setup" not in text


def test_verdict_passes_a_clean_run():
    stats = lt.Stats()
    stats.record("poll", 0.01, None)
    report = lt.summarize(stats, elapsed=1.0, clients=CLIENTS)
    report["timer"] = {"finished_sent": 2, "finished_ok": 2, "finished_recorded": 2}
    assert lt.verdict(report, max_p95_ms=300, max_p99_ms=1000) == []


def test_markdown_holds_the_tables_the_owner_pastes():
    report = lt.summarize(make_stats(), elapsed=10.0, clients=CLIENTS)
    report["timer"] = {"finished_sent": 3, "finished_ok": 3, "finished_recorded": 3}
    report["environment"] = {"Machine": "test box", "Run length": "10 s"}
    report["server"] = {
        "pid": 42,
        "cpu_mean_pct": 12.5,
        "cpu_max_pct": 40.0,
        "rss_max_mb": 80.0,
    }
    report["failures"] = lt.verdict(report, None, None)
    text = lt.render_markdown(report)
    assert text.startswith("# DerbyNet load report")
    assert "| Machine | test box |" in text
    assert "| poll | 100 | 0 | 0 | 50.0 | 95.0 | 99.0 | 100.0 |" in text
    assert "| kiosk content | 2 | after 0.5 s |" in text
    assert "FAIL" in text and "5xx" in text
    assert "12.5" in text


def test_report_lists_the_clients_endpoints_before_the_scripts_own():
    stats = lt.Stats()
    stats.record("setup role.login", 0.1, None)
    stats.record("poll", 0.01, None)
    stats.record("director database.purge", 0.1, None)
    stats.record("poll.kiosk", 0.01, None)
    report = lt.summarize(stats, elapsed=1.0, clients=CLIENTS)
    assert list(report["endpoints"]) == [
        "poll",
        "poll.kiosk",
        "setup role.login",
        "director database.purge",
    ]


# ----- the race director ---------------------------------------------------------


class FakeSession:
    """Answers the director's requests from a script of replies."""

    def __init__(self, heats_run: list[int], now_racing: bool = False):
        self.heats_run, self.now_racing, self.sent = list(heats_run), now_racing, []

    def request(self, label, method, params):
        self.sent.append(params.get("action") or params.get("query"))
        if params.get("query") == "poll.coordinator":
            run = self.heats_run.pop(0)
            body = {
                "current-heat": {"now_racing": self.now_racing},
                "rounds": [{"heats_run": run - 1}, {"heats_run": 1}],
            }
            return 200, json.dumps(body)
        return 200, '{"outcome": {"summary": "success"}}'


def test_director_restarts_racing_and_counts_every_pass():
    # 2 heats were run before the load started; the first pass ends at 10,
    # the schedule restarts from 0, and the run ends 4 heats into pass two.
    session = FakeSession(heats_run=[2, 10, 4])
    director = lt.Director(session, loop=True)
    director.first_roundid = 5
    director.start()
    director.rearm()
    assert session.sent[-2:] == ["database.purge", "heat.select"]
    assert director.passes == 1
    assert director.finished_recorded() == 8 + 4


def test_director_leaves_racing_alone_while_a_heat_is_on():
    session = FakeSession(heats_run=[0, 3], now_racing=True)
    director = lt.Director(session, loop=True)
    director.first_roundid = 5
    director.start()
    director.rearm()
    assert "database.purge" not in session.sent
    assert director.passes == 0


def test_director_never_purges_an_event_it_did_not_set_up():
    session = FakeSession(heats_run=[7, 9, 9])
    director = lt.Director(session, loop=False)
    director.start()
    director.rearm()
    assert "database.purge" not in session.sent
    assert director.finished_recorded() == 2


# ----- the server's CPU and memory ------------------------------------------------


def fake_proc(root: Path, pid: int, ppid: int, utime: int, stime: int, rss_kb: int):
    (root / str(pid)).mkdir(parents=True)
    fields = ["S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime)] + ["0"] * 30
    (root / str(pid) / "stat").write_text(
        f"{pid} (gun icorn) " + " ".join(fields) + "\n"
    )
    (root / str(pid) / "status").write_text(f"Name:\tgunicorn\nVmRSS:\t  {rss_kb} kB\n")


def test_proc_tree_sums_the_process_and_its_descendants(tmp_path):
    fake_proc(tmp_path, 100, 1, 200, 100, 50_000)
    fake_proc(tmp_path, 101, 100, 50, 50, 40_000)
    fake_proc(tmp_path, 102, 101, 10, 0, 10_000)
    fake_proc(tmp_path, 200, 1, 9999, 9999, 99_999)  # not ours
    (tmp_path / "self").mkdir()  # not a pid
    usage = lt.read_proc_tree(100, root=tmp_path, ticks=100)
    assert usage.cpu_seconds == pytest.approx(4.1)
    assert usage.rss_bytes == 100_000 * 1024
    assert usage.processes == 3


def test_proc_tree_of_a_missing_process_is_none(tmp_path):
    assert lt.read_proc_tree(12345, root=tmp_path) is None


def test_cpu_percent_between_two_samples():
    a = lt.ProcUsage(cpu_seconds=10.0, rss_bytes=0, processes=1)
    b = lt.ProcUsage(cpu_seconds=10.5, rss_bytes=0, processes=1)
    assert lt.cpu_percent(a, b, 2.0) == pytest.approx(25.0)


# ----- the command, against a stub server -----------------------------------------


class Stub(BaseHTTPRequestHandler):
    """Answers every client: XML to the timer, JSON to the rest, a 500 to
    `query=<fail>` and a slow reply to `query=<slow>`."""

    fail = ""
    slow = ""
    seen: list = []

    def log_message(self, *args):
        pass

    def reply(self, status: int, body: str, kind: str = "application/json") -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query).get("query", [""])[0]
        Stub.seen.append(query)
        if query == Stub.fail:
            self.reply(500, "Server Error (500)", "text/html")
        else:
            if query == Stub.slow:
                time.sleep(0.05)
            self.reply(200, "{}")

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        action = parse_qs(body).get("action", [""])[0]
        Stub.seen.append(action)
        if action == "timer-message":
            self.reply(200, SUCCESS, "text/xml")
        else:
            self.reply(200, '{"outcome": {"summary": "success"}}')


@pytest.fixture
def stub():
    Stub.fail, Stub.slow, Stub.seen = "", "", []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def run_command(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.load_test", *args],
        cwd=SERVER_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_command_passes_against_a_healthy_server(stub, tmp_path):
    out = tmp_path / "report.json"
    # Each kiosk's first poll.kiosk comes within its first 5 s.
    result = run_command(
        "--url", stub, "--duration", "6", "--kiosks", "2", "--json", str(out)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(out.read_text())
    endpoints = report["endpoints"]
    for name in (
        "poll.kiosk",
        "poll",
        "poll.coordinator",
        "replay.message",
        "poll.kiosk.all",
    ):
        assert endpoints[name]["requests"] > 0, name
    assert endpoints["timer-message HEARTBEAT"]["requests"] > 0
    assert "# DerbyNet load report" in result.stdout
    # Every client logs in as its role, as the pages and derby-timer.jar do.
    assert Stub.seen.count("role.login") >= 3


def test_command_fails_on_a_5xx(stub):
    Stub.fail = "poll.kiosk.all"
    result = run_command("--url", stub, "--duration", "3", "--kiosks", "1")
    assert result.returncode == 1
    assert "poll.kiosk.all" in result.stdout
    assert "5xx" in result.stdout


def test_command_fails_when_p95_crosses_the_threshold(stub):
    Stub.slow = "poll.coordinator"
    result = run_command(
        "--url", stub, "--duration", "3", "--kiosks", "1", "--max-p95-ms", "20"
    )
    assert result.returncode == 1
    assert "poll.coordinator p95" in result.stdout


def test_command_stops_cleanly_on_sigint(stub):
    process = subprocess.Popen(
        [sys.executable, "-m", "tools.load_test", "--url", stub, "--duration", "120"],
        cwd=SERVER_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(3)
    started = time.monotonic()
    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=15)
    assert time.monotonic() - started < 5
    assert process.returncode == 0, stdout + stderr
    assert "# DerbyNet load report" in stdout
    assert "Traceback" not in stderr


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_command_samples_the_server_process(stub, tmp_path):
    out = tmp_path / "report.json"
    result = run_command(
        "--url",
        stub,
        "--duration",
        "3",
        "--kiosks",
        "1",
        "--pid",
        str(os.getpid()),
        "--json",
        str(out),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    server = json.loads(out.read_text())["server"]
    assert server["pid"] == os.getpid()
    assert server["rss_max_mb"] > 0
