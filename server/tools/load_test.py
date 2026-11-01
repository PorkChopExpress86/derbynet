"""Race-day load on a DerbyNet server (#37).

    uv run python -m tools.load_test --url http://127.0.0.1:8000 --setup --duration 300

It plays the clients of a race day at the rates and with the requests the
real ones send, each in a thread of its own with its own session:

- 10 kiosks (`--kiosks`). Each polls `query=poll.kiosk&address=<id>` every
  5 s (`website/js/kiosk-poller.js`) and, as a now-racing kiosk, chains a
  `query=poll` 500 ms after each response (`website/js/now-racing-poller.js`,
  with the `values`, `roundid`, `heat` and `row-height` of
  `website/js/now-racing.js`). Kiosks do not log in.
- One timer, as `derby-timer.jar` talks (`timer/src/.../HttpTask.java`,
  `Message.java`): it logs in as `Timer`, sends `HELLO` and `IDENTIFIED`,
  then a `timer-message` 500 ms after each response. That is a `HEARTBEAT`
  unless a heat is due: on `<heat-ready>` it waits `--stage-seconds`, sends
  `STARTED`, and once the slowest car is in sends `FINISHED` with the
  heat's `roundid`, `heat` and a time from 2.5 to 4 s for each lane of the
  lane mask.
- The coordinator page, logged in as `RaceCoordinator`: `query=poll.coordinator`
  with the heat it shows, every 2 s, skipping a tick while one is in flight
  (`website/js/coordinator-poll.js`).
- The replay station, logged in as `RaceCoordinator` (D16): POST
  `action=replay.message&status=0&finished-replay=0` every 250 ms, skipping a
  tick while one is in flight (`website/replay.php`).
- The kiosk dashboard, logged in as `RaceCoordinator` (it needs
  PRESENT_AWARDS): `query=poll.kiosk.all`, chained 2 s after each response
  (`website/js/kiosk-dashboard.js`).

`--setup` first resets the event's database (`database.execute`, as
`testing/reset-database.sh` does), imports `--racers` racers in four groups,
checks them in, schedules every round on the master schedule with
`--lanes` lanes, points every kiosk at the now-racing page and starts
racing. When the timer then runs out of heats, the results are purged and
racing starts again from the first heat, so the timer races full heats for
the whole run. Without `--setup` the event is used as it is and nothing is
purged; racing stops when its schedule does.

At the end it checks the server recorded every `FINISHED` it accepted (the
`heats_run` of `poll.coordinator`'s rounds), and prints a markdown report:
per endpoint the request, error and 5xx counts and the p50, p95, p99 and
maximum latency; each client's rate against its target; and, with `--pid`,
the CPU and memory of that process and its children, read from `/proc`.
`--json` writes the report as JSON too.

It exits 1 on any error (a 5xx, another HTTP error, a failed connection or
a `failure` outcome), any `database is locked`, a `FINISHED` that was refused
or not recorded, or a latency over `--max-p95-ms` or `--max-p99-ms`; 2 when
setup fails. SIGINT or SIGTERM stops it early and still prints the report.

The rates are those the clients use: a chained client waits its delay after
each response, so it polls a little slower than its nominal rate, and the
report shows the rate it could reach given the mean latency as "expected".
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import math
import os
import platform
import random
import re
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlsplit

# ----- pacing ------------------------------------------------------------------


@dataclass(frozen=True)
class Every:
    """`setInterval(period)` guarded against a second request in flight: the
    next request goes at the first tick of the grid after the response."""

    period: float

    @property
    def rate(self) -> float:
        return 1.0 / self.period

    def next(self, scheduled: float, done: float) -> float:
        ticks = max(1, math.ceil((done - scheduled) / self.period - 1e-9))
        return scheduled + ticks * self.period

    def __str__(self) -> str:
        return f"every {self.period:g} s"


@dataclass(frozen=True)
class After:
    """`setTimeout(delay)` set when each response arrives."""

    delay: float

    @property
    def rate(self) -> float:
        return 1.0 / self.delay

    def next(self, scheduled: float, done: float) -> float:
        return done + self.delay

    def __str__(self) -> str:
        return f"after {self.delay:g} s"


def run_actor(
    actor,
    pacer: Every | After,
    first_at: float,
    clock: Callable[[], float],
    wait: Callable[[float], bool],
    deadline: float,
) -> None:
    """Call `actor.tick()` at `first_at`, then as `pacer` says, until
    `deadline` or until `wait` reports a stop. A tick that raises is logged
    and the actor goes on, as a page's next poll would."""
    scheduled = first_at
    while scheduled < deadline:
        if wait(max(0.0, scheduled - clock())):
            return
        try:
            actor.tick()
        except Exception as error:  # one bad reply must not end a client
            print(f"{type(actor).__name__}: {error!r}", file=sys.stderr)
        scheduled = pacer.next(scheduled, clock())


# ----- responses and statistics ------------------------------------------------

_FAILURE_JSON = re.compile(r'"summary"\s*:\s*"failure"')


def classify(status: int | None, body: str) -> str | None:
    """The kind of error a response is, or None for a good one."""
    if status is None:
        return "transport"
    if "database is locked" in body.lower():
        return "locked"
    if status >= 500:
        return "5xx"
    if status >= 400:
        return "http"
    if _FAILURE_JSON.search(body) or "<failure" in body:
        return "failure"
    return None


def percentile(values: Iterable[float], p: float) -> float | None:
    """The nearest-rank percentile: the smallest value with at least `p`
    percent of the values at or below it."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


@dataclass
class _Endpoint:
    latencies: list[float] = field(default_factory=list)
    errors: int = 0
    server_errors: int = 0
    locked: int = 0
    transport_errors: int = 0


class Stats:
    """Every request's latency and outcome, by endpoint label. Labels that
    start with `setup ` or `director ` are the script's own requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._endpoints: dict[str, _Endpoint] = {}

    def record(
        self, endpoint: str, latency: float, kind: str | None, status: int | None = None
    ) -> None:
        with self._lock:
            row = self._endpoints.setdefault(endpoint, _Endpoint())
            row.latencies.append(latency)
            if kind is not None:
                row.errors += 1
            if kind == "5xx" or (status is not None and status >= 500):
                row.server_errors += 1
            if kind == "locked":
                row.locked += 1
            if kind == "transport":
                row.transport_errors += 1

    def snapshot(self) -> dict[str, _Endpoint]:
        with self._lock:
            return {
                name: _Endpoint(list(row.latencies), *_counts(row))
                for name, row in self._endpoints.items()
            }


def _counts(row: _Endpoint) -> tuple[int, int, int, int]:
    return row.errors, row.server_errors, row.locked, row.transport_errors


@dataclass(frozen=True)
class ClientType:
    """A kind of client, how many run, how they pace, and the endpoint labels
    their requests go under (a label matches itself and `<label> <more>`)."""

    name: str
    instances: int
    pacer: Every | After
    endpoints: tuple[str, ...]

    def owns(self, endpoint: str) -> bool:
        return any(
            endpoint == e or endpoint.startswith(e + " ") for e in self.endpoints
        )


def _own(endpoint: str) -> bool:
    return endpoint.startswith(("setup ", "director "))


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def summarize(stats: Stats, elapsed: float, clients: Iterable[ClientType]) -> dict:
    endpoints = {}
    totals = dict.fromkeys(
        ("requests", "errors", "server_errors", "locked", "transport_errors"), 0
    )
    snapshot = stats.snapshot()
    # The clients' endpoints first, in the order they were first seen.
    for name, row in sorted(snapshot.items(), key=lambda item: _own(item[0])):
        entry = {
            "requests": len(row.latencies),
            "errors": row.errors,
            "server_errors": row.server_errors,
            "locked": row.locked,
            "transport_errors": row.transport_errors,
            "p50_ms": _ms(percentile(row.latencies, 50)),
            "p95_ms": _ms(percentile(row.latencies, 95)),
            "p99_ms": _ms(percentile(row.latencies, 99)),
            "max_ms": _ms(max(row.latencies, default=None)),
            "rate_per_s": round(len(row.latencies) / elapsed, 3) if elapsed else None,
        }
        endpoints[name] = entry
        for key in totals:
            totals[key] += entry[key]
    rates = []
    for client in clients:
        latencies = [
            latency
            for name, row in snapshot.items()
            if client.owns(name)
            for latency in row.latencies
        ]
        target = client.instances * client.pacer.rate
        if isinstance(client.pacer, After) and latencies:
            mean = sum(latencies) / len(latencies)
            expected = client.instances / (client.pacer.delay + mean)
        else:
            expected = target
        achieved = len(latencies) / elapsed if elapsed else 0.0
        rates.append(
            {
                "client": client.name,
                "instances": client.instances,
                "pacing": str(client.pacer),
                "target_per_s": round(target, 4),
                "expected_per_s": round(expected, 4),
                "achieved_per_s": round(achieved, 4),
                "achieved_pct": round(100 * achieved / expected, 1)
                if expected
                else None,
            }
        )
    return {
        "elapsed_s": round(elapsed, 1),
        "endpoints": endpoints,
        "totals": totals,
        "rates": rates,
    }


def verdict(
    report: dict, max_p95_ms: float | None, max_p99_ms: float | None
) -> list[str]:
    """Each check the run failed, in words; empty for a run that passed."""
    failures = []
    totals = report["totals"]
    if totals["server_errors"]:
        failures.append(f"{totals['server_errors']} 5xx responses")
    if totals["locked"]:
        failures.append(f"{totals['locked']} 'database is locked' errors")
    if totals["errors"]:
        by_endpoint = ", ".join(
            f"{name} {row['errors']}"
            for name, row in report["endpoints"].items()
            if row["errors"]
        )
        failures.append(f"{totals['errors']} errors ({by_endpoint})")
    timer = report.get("timer") or {}
    if timer.get("finished_ok", 0) < timer.get("finished_sent", 0):
        refused = timer["finished_sent"] - timer["finished_ok"]
        failures.append(f"{refused} FINISHED refused by the server")
    recorded = timer.get("finished_recorded")
    if recorded is not None and recorded != timer.get("finished_ok"):
        failures.append(
            f"{timer.get('finished_ok')} FINISHED accepted but"
            f" {recorded} heats recorded"
        )
    if timer.get("required") and not timer.get("finished_ok"):
        failures.append("the timer finished no heat")
    for name, row in report["endpoints"].items():
        if _own(name):
            continue
        for key, limit in (("p95_ms", max_p95_ms), ("p99_ms", max_p99_ms)):
            if limit is not None and row[key] is not None and row[key] > limit:
                failures.append(f"{name} {key[:3]} {row[key]:.1f} ms > {limit:g} ms")
    return failures


def _cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def render_markdown(report: dict) -> str:
    lines = ["# DerbyNet load report", ""]
    environment = report.get("environment") or {}
    if environment:
        lines += ["| | |", "|---|---|"]
        lines += [f"| {key} | {value} |" for key, value in environment.items()]
        lines.append("")
    failures = report.get("failures", [])
    lines.append("## Verdict")
    lines.append("")
    if failures:
        lines.append("**FAIL**")
        lines.append("")
        lines += [f"- {failure}" for failure in failures]
    else:
        lines.append("**PASS**: no errors, no 5xx, no `database is locked`,")
        lines.append("every FINISHED recorded, and every threshold met.")
    totals = report["totals"]
    lines += [
        "",
        f"{totals['requests']} requests in {report['elapsed_s']} s:"
        f" {totals['errors']} errors, {totals['server_errors']} 5xx,"
        f" {totals['locked']} `database is locked`,"
        f" {totals['transport_errors']} failed connections.",
        "",
        "## Endpoints",
        "",
        "Latency in ms, measured at the client, from sending the request to"
        " reading the whole response. `setup` and `director` rows are the"
        " script's own requests.",
        "",
        "| Endpoint | Requests | Errors | 5xx | p50 | p95 | p99 | max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in report["endpoints"].items():
        cells = [name] + [
            _cell(row[key])
            for key in (
                "requests",
                "errors",
                "server_errors",
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "max_ms",
            )
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Rates",
        "",
        "Requests per second for all instances together. A client paced"
        " *after* each response can reach only `expected`, its delay plus the"
        " mean latency apart.",
        "",
        "| Client | Instances | Pacing | Target/s | Expected/s | Achieved/s"
        " | % of expected |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for rate in report["rates"]:
        lines.append(
            f"| {rate['client']} | {rate['instances']} | {rate['pacing']} |"
            f" {rate['target_per_s']:.2f} | {rate['expected_per_s']:.2f} |"
            f" {rate['achieved_per_s']:.2f} | {_cell(rate['achieved_pct'])} |"
        )
    timer = report.get("timer")
    if timer:
        lines += ["", "## Timer", ""]
        lines.append(f"- FINISHED sent: {timer.get('finished_sent')}")
        lines.append(f"- FINISHED accepted: {timer.get('finished_ok')}")
        recorded = timer.get("finished_recorded")
        lines.append(
            "- Heats recorded by the server: "
            + ("not checked" if recorded is None else str(recorded))
        )
        if "passes" in timer:
            lines.append(
                f"- Restarts of the schedule after its last heat: {timer['passes']}"
            )
    server = report.get("server")
    if server:
        lines += ["", "## Server process", ""]
        labels = {
            "pid": "PID (with its children)",
            "processes": "Processes",
            "cpu_mean_pct": "CPU, mean % of one core",
            "cpu_max_pct": "CPU, highest 1 s % of one core",
            "rss_start_mb": "RSS at start, MB",
            "rss_max_mb": "RSS peak, MB",
            "rss_end_mb": "RSS at end, MB",
            "host_cpu_mean_pct": "Host CPU, mean % of all cores",
            "host_cpu_max_pct": "Host CPU, highest 1 s % of all cores",
            "error": "Error",
        }
        lines += ["| | |", "|---|---:|"]
        lines += [
            f"| {labels.get(key, key)} | {_cell(value)} |"
            for key, value in server.items()
        ]
    return "\n".join(lines) + "\n"


# ----- the server's CPU and memory, from /proc ---------------------------------


@dataclass(frozen=True)
class ProcUsage:
    cpu_seconds: float
    rss_bytes: int
    processes: int


def read_proc_tree(
    pid: int, root: Path = Path("/proc"), ticks: int | None = None
) -> ProcUsage | None:
    """The CPU time and resident memory of `pid` and all its descendants
    (gunicorn's workers), or None when `pid` is gone."""
    if ticks is None:
        ticks = os.sysconf("SC_CLK_TCK")
    parents: dict[int, int] = {}
    cpu: dict[int, int] = {}
    for entry in root.iterdir() if root.is_dir() else ():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[1].split()
        parents[int(entry.name)] = int(fields[1])
        cpu[int(entry.name)] = int(fields[11]) + int(fields[12])
    if pid not in cpu:
        return None
    tree, frontier = {pid}, [pid]
    while frontier:
        parent = frontier.pop()
        for child, ppid in parents.items():
            if ppid == parent and child not in tree:
                tree.add(child)
                frontier.append(child)
    rss = 0
    for member in tree:
        try:
            status = (root / str(member) / "status").read_text()
        except OSError:
            continue
        match = re.search(r"^VmRSS:\s*(\d+)\s*kB", status, re.MULTILINE)
        if match:
            rss += int(match.group(1)) * 1024
    return ProcUsage(sum(cpu[m] for m in tree) / ticks, rss, len(tree))


def cpu_percent(before: ProcUsage, after: ProcUsage, seconds: float) -> float:
    return (
        100 * (after.cpu_seconds - before.cpu_seconds) / seconds if seconds > 0 else 0.0
    )


def _host_cpu() -> tuple[float, float] | None:
    """Busy and total jiffies of the whole host, from /proc/stat."""
    try:
        first = Path("/proc/stat").read_text().splitlines()[0].split()
    except OSError:
        return None
    values = [float(v) for v in first[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0.0)
    return sum(values) - idle, sum(values)


class ProcSampler(threading.Thread):
    """Samples the server process tree, and the host, once a second."""

    def __init__(self, pid: int, stop: threading.Event):
        super().__init__(daemon=True, name="proc-sampler")
        self.pid, self.stop = pid, stop
        self.samples: list[tuple[float, ProcUsage]] = []
        self.host: list[tuple[float, float]] = []

    def sample(self) -> None:
        usage = read_proc_tree(self.pid)
        if usage is not None:
            self.samples.append((time.monotonic(), usage))
        host = _host_cpu()
        if host is not None:
            self.host.append(host)

    def run(self) -> None:
        self.sample()
        while not self.stop.wait(1.0):
            self.sample()

    def summary(self) -> dict:
        self.sample()
        if len(self.samples) < 2:
            return {"pid": self.pid, "error": "no samples; is the process running?"}
        (t0, first), (t1, last) = self.samples[0], self.samples[-1]
        steps = [
            cpu_percent(a, b, tb - ta)
            for (ta, a), (tb, b) in zip(self.samples, self.samples[1:], strict=False)
        ]
        mb = 1024 * 1024
        out = {
            "pid": self.pid,
            "processes": last.processes,
            "cpu_mean_pct": round(cpu_percent(first, last, t1 - t0), 1),
            "cpu_max_pct": round(max(steps), 1),
            "rss_start_mb": round(first.rss_bytes / mb, 1),
            "rss_max_mb": round(max(u.rss_bytes for _, u in self.samples) / mb, 1),
            "rss_end_mb": round(last.rss_bytes / mb, 1),
        }
        if len(self.host) >= 2:
            host_steps = [
                100 * (b[0] - a[0]) / (b[1] - a[1])
                for a, b in zip(self.host, self.host[1:], strict=False)
                if b[1] > a[1]
            ]
            span = self.host[-1][1] - self.host[0][1]
            if span > 0 and host_steps:
                out["host_cpu_mean_pct"] = round(
                    100 * (self.host[-1][0] - self.host[0][0]) / span, 1
                )
                out["host_cpu_max_pct"] = round(max(host_steps), 1)
        return out


# ----- HTTP --------------------------------------------------------------------


class Cookies:
    """A cookie jar several connections of one browser share."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}

    def header(self) -> str:
        with self._lock:
            return "; ".join(f"{k}={v}" for k, v in self._values.items())

    def update(self, set_cookies: Iterable[str]) -> None:
        with self._lock:
            for line in set_cookies:
                name, _, rest = line.partition("=")
                value = rest.split(";", 1)[0]
                expired = "max-age=0" in line.lower().replace(" ", "")
                if value and not expired:
                    self._values[name.strip()] = value
                else:
                    self._values.pop(name.strip(), None)


class HttpSession:
    """One keep-alive connection to `action.php`, as one page's polls use,
    recording each request in `stats`."""

    def __init__(
        self,
        base_url: str,
        stats: Stats,
        cookies: Cookies | None = None,
        timeout: float = 30.0,
    ):
        parts = urlsplit(base_url)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.https = parts.scheme == "https"
        self.path = parts.path.rstrip("/") + "/action.php"
        self.stats, self.timeout = stats, timeout
        self.cookies = cookies or Cookies()
        self._conn: http.client.HTTPConnection | None = None

    def _connection(self) -> tuple[http.client.HTTPConnection, bool]:
        if self._conn is None:
            kind = (
                http.client.HTTPSConnection
                if self.https
                else http.client.HTTPConnection
            )
            self._conn = kind(self.host, self.port, timeout=self.timeout)
            return self._conn, False
        return self._conn, True

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _send(self, method: str, params: dict) -> tuple[int, str]:
        headers = {"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"}
        cookie = self.cookies.header()
        if cookie:
            headers["Cookie"] = cookie
        url, body = self.path, None
        if method == "GET":
            url += "?" + urlencode(params)
        else:
            body = urlencode(params)
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        for attempt in (1, 2):
            conn, reused = self._connection()
            try:
                conn.request(method, url, body=body, headers=headers)
                response = conn.getresponse()
                data = response.read()
            except (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                BrokenPipeError,
            ):
                # The server closed an idle keep-alive connection; a browser
                # retries such a request on a new one.
                self.close()
                if reused and attempt == 1:
                    continue
                raise
            if response.will_close:
                self.close()
            self.cookies.update(response.headers.get_all("Set-Cookie") or [])
            return response.status, data.decode("utf-8", "replace")
        raise AssertionError("unreachable")

    def request(self, label: str, method: str, params: dict) -> tuple[int | None, str]:
        started = time.perf_counter()
        try:
            status, text = self._send(method, params)
        except (OSError, http.client.HTTPException) as error:
            self.close()
            status, text = None, repr(error)
        latency = time.perf_counter() - started
        self.stats.record(label, latency, classify(status, text), status)
        return status, text

    def login(self, role: str, password: str) -> None:
        status, text = self.request(
            "setup role.login",
            "POST",
            {"action": "role.login", "name": role, "password": password},
        )
        if status != 200 or classify(status, text) is not None:
            raise SetupError(f"could not log in as {role}: {status} {text[:200]}")


class SetupError(Exception):
    pass


def _json(text: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# ----- the clients -------------------------------------------------------------

NOW_RACING_VALUES = (
    "current-heat,heat-results,precision,racers,timer-trouble,current-reschedule"
)


class KioskPoller:
    """`kiosk-poller.js`: `poll.kiosk` with the kiosk's address."""

    def __init__(self, session: HttpSession, address: str):
        self.session, self.address = session, address

    def tick(self) -> None:
        self.session.request(
            "poll.kiosk", "GET", {"query": "poll.kiosk", "address": self.address}
        )


class NowRacingPoller:
    """`now-racing-poller.js` with `now-racing.js`'s request: `poll` for
    the lineup it shows, which starts at round 0, heat 0."""

    def __init__(self, session: HttpSession):
        self.session, self.roundid, self.heat = session, 0, 0

    def tick(self) -> None:
        status, text = self.session.request(
            "poll",
            "GET",
            {
                "query": "poll",
                "values": NOW_RACING_VALUES,
                "roundid": self.roundid,
                "heat": self.heat,
                "row-height": 0,
            },
        )
        current = _json(text).get("current-heat") if status == 200 else None
        if isinstance(current, dict):
            self.roundid = current.get("roundid", self.roundid)
            self.heat = current.get("heat", self.heat)


class CoordinatorPoller:
    """`coordinator-poll.js`: `poll.coordinator` for the heat it shows."""

    def __init__(self, session: HttpSession):
        self.session, self.roundid, self.heat = session, "", ""

    def tick(self) -> None:
        status, text = self.session.request(
            "poll.coordinator",
            "GET",
            {"query": "poll.coordinator", "roundid": self.roundid, "heat": self.heat},
        )
        current = _json(text).get("current-heat") if status == 200 else None
        if isinstance(current, dict):
            self.roundid = current.get("roundid", self.roundid)
            self.heat = current.get("heat", self.heat)


class ReplayPoller:
    """`replay.php`'s `poll_once_for_replay`."""

    def __init__(self, session: HttpSession):
        self.session = session

    def tick(self) -> None:
        self.session.request(
            "replay.message",
            "POST",
            {"action": "replay.message", "status": 0, "finished-replay": 0},
        )


class DashboardPoller:
    """`kiosk-dashboard.js`'s `poll_kiosk_all`."""

    def __init__(self, session: HttpSession):
        self.session = session

    def tick(self) -> None:
        self.session.request("poll.kiosk.all", "GET", {"query": "poll.kiosk.all"})


_HEAT_READY = re.compile(r"<heat-ready\b([^>]*)>")
_ATTRIBUTE = re.compile(r'([\w-]+)="([^"]*)"')


class TimerSim:
    """`derby-timer.jar` with a simulated timer, one message at a time.

    `next_message(now)` is the message to send at `now` and
    `on_response(label, body, now)` reads the server's reply."""

    def __init__(
        self,
        lanes: int,
        rng: random.Random,
        stage_seconds: float = 1.0,
        fastest: float = 2.5,
        slowest: float = 4.0,
    ):
        self.lanes, self.rng, self.stage_seconds = lanes, rng, stage_seconds
        self.fastest, self.slowest = fastest, slowest
        self.phase = 0  # 0: say HELLO, 1: say IDENTIFIED, 2: connected
        self.staged: tuple[int, int, int] | None = None  # roundid, heat, lane mask
        self.staged_at = 0.0
        self.started_at: float | None = None
        self.race_times: dict[int, str] = {}
        self.idle_since: float | None = None
        self.finished_sent = 0
        self.finished_ok = 0

    def _message(self, message: str, **params: str) -> tuple[str, dict]:
        return f"timer-message {message}", {
            "action": "timer-message",
            "message": message,
            **params,
        }

    def next_message(self, now: float) -> tuple[str, dict]:
        if self.phase == 0:
            return self._message("HELLO", interface="jar", build="load-test")
        if self.phase == 1:
            return self._message(
                "IDENTIFIED",
                interface="jar",
                lane_count=str(self.lanes),
                timer="SimulatedTimer",
                confirmed="1",
                human="Load test timer",
            )
        if self.staged is not None and self.started_at is None:
            if now >= self.staged_at + self.stage_seconds:
                self.started_at = now
                _, _, mask = self.staged
                self.race_times = {
                    lane: f"{self.rng.uniform(self.fastest, self.slowest):.4f}"
                    for lane in range(1, self.lanes + 1)
                    if mask & (1 << (lane - 1))
                }
                return self._message("STARTED")
        elif self.staged is not None and self.started_at is not None:
            slowest = max((float(t) for t in self.race_times.values()), default=0.0)
            if now >= self.started_at + slowest:
                roundid, heat, _ = self.staged
                self.finished_sent += 1
                lanes = {f"lane{lane}": time for lane, time in self.race_times.items()}
                return self._message(
                    "FINISHED", roundid=str(roundid), heat=str(heat), **lanes
                )
        return self._message("HEARTBEAT", confirmed="1")

    def _unstage(self, now: float) -> None:
        self.staged, self.started_at, self.race_times = None, None, {}
        self.idle_since = now

    def on_response(self, label: str, body: str, now: float) -> None:
        ok = "<success" in body and "<failure" not in body
        message = label.removeprefix("timer-message ")
        if message == "HELLO" and ok:
            self.phase = max(self.phase, 1)
        elif message == "IDENTIFIED" and ok:
            self.phase = 2
            self.idle_since = now
        elif message == "FINISHED":
            if ok:
                self.finished_ok += 1
            self._unstage(now)
        if "<abort" in body:
            self._unstage(now)
        match = _HEAT_READY.search(body)
        if match and self.started_at is None:
            attrs = dict(_ATTRIBUTE.findall(match.group(1)))
            try:
                self.staged = (
                    int(attrs["roundid"]),
                    int(attrs["heat"]),
                    int(attrs["lane-mask"]),
                )
            except KeyError, ValueError:
                return
            self.staged_at = now
            self.idle_since = None

    def idle_seconds(self, now: float) -> float:
        """How long the connected timer has had no heat to race."""
        if self.idle_since is None or self.staged is not None:
            return 0.0
        return now - self.idle_since


class TimerClient:
    def __init__(
        self,
        session: HttpSession,
        sim: TimerSim,
        clock: Callable[[], float],
        director: Director | None = None,
        rearm_after: float = 1.0,
    ):
        self.session, self.sim, self.clock = session, sim, clock
        self.director, self.rearm_after = director, rearm_after

    def tick(self) -> None:
        label, params = self.sim.next_message(self.clock())
        status, text = self.session.request(label, "POST", params)
        self.sim.on_response(label, text if status == 200 else "", self.clock())
        if (
            self.director is not None
            and self.sim.idle_seconds(self.clock()) > self.rearm_after
        ):
            self.director.rearm()
            self.sim.idle_since = self.clock()


# ----- setup and the race director ---------------------------------------------

GROUPS = ("Lions", "Tigers", "Wolves", "Bears")


class Director:
    """The script's coordinator: sets the event up, restarts racing when the
    schedule runs out, and counts the heats the server recorded."""

    def __init__(self, session: HttpSession, loop: bool):
        self.session, self.loop = session, loop
        self.recorded = 0  # heats run in earlier passes
        self.baseline = 0  # heats run before this pass's first FINISHED
        self.passes = 0
        self.first_roundid: int | None = None
        self.checked = True

    def _post(self, label: str, **params) -> dict:
        status, text = self.session.request(label, "POST", params)
        if status != 200 or classify(status, text) is not None:
            raise SetupError(f"{params.get('action')} failed: {status} {text[:300]}")
        return _json(text)

    def _get(self, label: str, **params) -> dict:
        status, text = self.session.request(label, "GET", params)
        if status != 200:
            raise SetupError(f"{params.get('query')} failed: {status} {text[:300]}")
        return _json(text)

    def setup(
        self, racers: int, groups: int, lanes: int, kiosk_addresses: list[str]
    ) -> None:
        names = (
            GROUPS[:groups]
            if groups <= len(GROUPS)
            else [f"Group {n}" for n in range(1, groups + 1)]
        )
        post = self._post
        post("setup database.execute", action="database.execute", script="schema")
        post(
            "setup database.execute", action="database.execute", script="update-schema"
        )
        post(
            "setup settings.write",
            action="settings.write",
            **{
                "n-lanes": lanes,
                "unused-lane-mask": 0,
                "use-master-sched-checkbox": 1,
                "use-master-sched": 1,
            },
        )
        for i in range(racers):
            group = names[i % len(names)]
            post(
                "setup racer.import",
                action="racer.import",
                firstname=f"Racer{i + 1}",
                lastname=group,
                partition=group,
                carnumber=100 * (i % len(names) + 1) + i // len(names) + 1,
            )
        post(
            "setup racer.bulk", action="racer.bulk", who="all", what="checkin", value=1
        )
        rounds = self._get("setup poll", query="poll", values="rounds").get(
            "rounds", []
        )
        for entry in rounds:
            post(
                "setup schedule.generate",
                action="schedule.generate",
                roundid=entry["roundid"],
            )
        for address in kiosk_addresses:
            self._get("setup poll.kiosk", query="poll.kiosk", address=address)
        post("setup kiosk.assign", action="kiosk.assign", all="kiosks/now-racing.kiosk")
        if not rounds:
            raise SetupError("setup made no rounds")
        self.first_roundid = rounds[0]["roundid"]
        self._start_racing("setup heat.select")

    def _start_racing(self, label: str) -> None:
        self._post(
            label,
            action="heat.select",
            roundid=self.first_roundid,
            heat=1,
            now_racing=1,
        )

    def heats_run(self) -> int:
        rounds = self._get("director poll.coordinator", query="poll.coordinator").get(
            "rounds"
        )
        return sum(r.get("heats_run", 0) for r in rounds or [] if isinstance(r, dict))

    def start(self) -> None:
        try:
            self.baseline = self.heats_run()
        except SetupError as error:
            print(f"cannot count the heats run: {error}", file=sys.stderr)
            self.checked = False

    def rearm(self) -> None:
        """After the schedule's last heat: purge the results and race it again."""
        if not self.loop or self.first_roundid is None:
            return
        try:
            state = self._get("director poll.coordinator", query="poll.coordinator")
            if (state.get("current-heat") or {}).get("now_racing"):
                return
            run = sum(r.get("heats_run", 0) for r in state.get("rounds") or [])
            self.recorded += run - self.baseline
            self._post(
                "director database.purge", action="database.purge", purge="results"
            )
            self.baseline = 0
            self._start_racing("director heat.select")
            self.passes += 1
        except SetupError as error:
            print(f"could not restart racing: {error}", file=sys.stderr)

    def finished_recorded(self) -> int | None:
        if not self.checked:
            return None
        try:
            return self.recorded + self.heats_run() - self.baseline
        except SetupError:
            return None


# ----- the run -----------------------------------------------------------------


def environment(args: argparse.Namespace, elapsed: float) -> dict:
    def first(path: str, pattern: str) -> str | None:
        try:
            match = re.search(pattern, Path(path).read_text(), re.MULTILINE)
        except OSError:
            return None
        return match.group(1).strip().strip('"') if match else None

    try:
        import django

        django_version = django.get_version()
    except ImportError:
        django_version = "not installed with the client"
    memory = first("/proc/meminfo", r"^MemTotal:\s*(\d+)")
    cpu = first("/proc/cpuinfo", r"^(?:model name|Model)\s*:\s*(.+)$")
    machine = platform.machine()
    return {
        "Date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "Machine": ", ".join(
            part
            for part in (
                socket.gethostname(),
                cpu,
                f"{os.cpu_count()} cores",
                f"{int(memory) // 1024} MB RAM" if memory else None,
                machine,
            )
            if part
        ),
        "OS": first("/etc/os-release", r"^PRETTY_NAME=(.+)$") or platform.platform(),
        "Kernel": platform.release(),
        "Python": platform.python_version(),
        "Django": django_version,
        "Server": args.url + (f" ({args.server})" if args.server else ""),
        "Run length": f"{elapsed:.0f} s of {args.duration:g} s",
        "Clients": (
            f"{args.kiosks} kiosks, 1 timer, 1 coordinator, 1 replay station,"
            " 1 kiosk dashboard"
        ),
        **({"Note": args.note} if args.note else {}),
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tools.load_test",
        description="Simulate race-day polling against a DerbyNet server (#37).",
    )
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000", help="the server's base URL"
    )
    parser.add_argument("--duration", type=float, default=60, help="seconds to run")
    parser.add_argument("--kiosks", type=int, default=10)
    parser.add_argument(
        "--setup", action="store_true", help="reset the event and race it"
    )
    parser.add_argument("--racers", type=int, default=40, help="racers --setup imports")
    parser.add_argument("--groups", type=int, default=4, help="groups --setup makes")
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--stage-seconds", type=float, default=1.0)
    parser.add_argument("--pid", type=int, help="the server process to sample")
    parser.add_argument("--json", type=Path, help="also write the report here as JSON")
    parser.add_argument("--markdown", type=Path, help="also write the markdown here")
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--max-p99-ms", type=float)
    parser.add_argument("--coordinator-password", default="doyourbest")
    parser.add_argument("--timer-password", default="")
    parser.add_argument("--seed", type=int, default=37)
    parser.add_argument(
        "--server", default="", help="how the server runs, for the report"
    )
    parser.add_argument("--note", default="", help="a line for the report")
    return parser.parse_args(argv)


def client_types(kiosks: int) -> list[ClientType]:
    return [
        ClientType("kiosk poll.kiosk", kiosks, Every(5.0), ("poll.kiosk",)),
        ClientType("kiosk now-racing poll", kiosks, After(0.5), ("poll",)),
        ClientType("timer", 1, After(0.5), ("timer-message",)),
        ClientType("coordinator", 1, Every(2.0), ("poll.coordinator",)),
        ClientType("replay", 1, Every(0.25), ("replay.message",)),
        ClientType("kiosk dashboard", 1, After(2.0), ("poll.kiosk.all",)),
    ]


def run(args: argparse.Namespace, stop: threading.Event) -> dict:
    stats = Stats()
    rng = random.Random(args.seed)
    clock = time.monotonic
    addresses = [f"load-kiosk-{n:02d}" for n in range(1, args.kiosks + 1)]

    def session(cookies: Cookies | None = None) -> HttpSession:
        return HttpSession(args.url, stats, cookies)

    director = Director(session(), loop=args.setup)
    director.session.login("RaceCoordinator", args.coordinator_password)
    if args.setup:
        director.setup(args.racers, args.groups, args.lanes, addresses)
    director.start()

    actors: list[tuple[object, Every | After, float]] = []
    for address in addresses:
        jar = Cookies()  # one browser: its two pollers share the session
        actors.append(
            (KioskPoller(session(jar), address), Every(5.0), rng.uniform(0, 5))
        )
        actors.append(
            (NowRacingPoller(session(jar)), After(0.5), 1.0 + rng.uniform(0, 0.5))
        )
    for kind, pacer in (
        (CoordinatorPoller, Every(2.0)),
        (ReplayPoller, Every(0.25)),
        (DashboardPoller, After(2.0)),
    ):
        s = session()
        s.login("RaceCoordinator", args.coordinator_password)
        actors.append((kind(s), pacer, 0.0))
    timer_session = session()
    timer_session.login("Timer", args.timer_password)
    sim = TimerSim(
        args.lanes, random.Random(args.seed + 1), stage_seconds=args.stage_seconds
    )
    actors.append((TimerClient(timer_session, sim, clock, director), After(0.5), 0.0))

    sampler = ProcSampler(args.pid, stop) if args.pid else None
    if sampler:
        sampler.start()
    start = clock()
    deadline = start + args.duration
    threads = [
        threading.Thread(
            target=run_actor,
            args=(actor, pacer, start + offset, clock, stop.wait, deadline),
            daemon=True,
            name=type(actor).__name__,
        )
        for actor, pacer, offset in actors
    ]
    for thread in threads:
        thread.start()
    while any(t.is_alive() for t in threads) and not stop.wait(0.2):
        pass
    elapsed = min(clock(), deadline) - start
    stop.set()
    for thread in threads:
        thread.join(timeout=35)

    report = summarize(stats, elapsed, client_types(args.kiosks))
    report["timer"] = {
        "finished_sent": sim.finished_sent,
        "finished_ok": sim.finished_ok,
        "finished_recorded": director.finished_recorded(),
        "required": args.setup,
        "passes": director.passes,
    }
    if sampler:
        report["server"] = sampler.summary()
    report["environment"] = environment(args, elapsed)
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stop = threading.Event()

    def on_signal(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        report = run(args, stop)
    except SetupError as error:
        print(f"setup failed: {error}", file=sys.stderr)
        return 2
    report["thresholds"] = {
        "max_p95_ms": args.max_p95_ms,
        "max_p99_ms": args.max_p99_ms,
    }
    report["failures"] = verdict(report, args.max_p95_ms, args.max_p99_ms)
    text = render_markdown(report)
    print(text)
    if args.markdown:
        args.markdown.write_text(text)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
