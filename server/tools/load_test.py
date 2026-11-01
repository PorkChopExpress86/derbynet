"""Race-day load on a DerbyNet server (#37).

    uv run python -m tools.load_test --url http://127.0.0.1:8000 --setup --duration 300

It plays a race day's clients, 10 kiosks, one timer, the coordinator page,
the replay station and the kiosk dashboard, each at its real rate with its
real requests and login, and prints a markdown report. The section
"Race-day load test (#37)" of `server/README.md` describes each client, the
setup and the restarts, the report, the thresholds, the exit status, and the
limiting-resource heuristic. It is the one place they are described; keep it
in step with this module.

The pieces, top to bottom: pacing (`Interval`, `Every`, `After`,
`run_actor`), response kinds and statistics, the report (`summarize`,
`verdict`, `likely_limits`, `render_markdown`), the `/proc` sampler, the HTTP
session, the clients, the timer (`TimerSim`), the director that sets the
event up and restarts it, and the run itself (`client_types`, `run`, `main`).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import enum
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlencode, urlsplit

# ----- pacing ------------------------------------------------------------------


@dataclass(frozen=True)
class Interval:
    """A bare `setInterval(period)`, as `kiosk-poller.js` uses: a request
    goes every period even while the last one is still in flight."""

    period: float

    @property
    def rate(self) -> float:
        return 1.0 / self.period

    def next(self, scheduled: float, done: float) -> float:
        return scheduled + self.period

    def __str__(self) -> str:
        return f"every {self.period:g} s, overlapping"


@dataclass(frozen=True)
class Every:
    """`setInterval(period)` guarded against a second request in flight, as
    `coordinator-poll.js` and `replay.php` are: the next request goes at the
    first tick of the grid after the response."""

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


Pacer = Interval | Every | After


def _tick(actor) -> None:
    try:
        actor.tick()
    except Exception as error:  # one bad reply must not end a client
        print(f"{type(actor).__name__}: {error!r}", file=sys.stderr)


def run_actor(
    actor,
    pacer: Pacer,
    first_at: float,
    clock: Callable[[], float],
    wait: Callable[[float], bool],
    deadline: float,
    spawn: Callable[[Callable[[], None]], object] | None = None,
    lag: Callable[[float], None] | None = None,
) -> None:
    """Call `actor.tick()` at `first_at`, then as `pacer` says, until
    `deadline` or until `wait` reports a stop.

    With an `Interval`, each tick goes to `spawn`, which runs it on another
    thread, so the grid never waits for a response. An actor with a
    `due_at()` that returns a time fires then if that is sooner than the
    pacer's next tick, as `derby-timer.jar` sends a queued message at once.
    `lag` gets how late each tick started. A tick that raises is logged and
    the actor goes on, as a page's next poll would."""
    due_at = getattr(actor, "due_at", None)
    scheduled = first_at
    while scheduled < deadline:
        if wait(max(0.0, scheduled - clock())):
            return
        if lag is not None:
            lag(max(0.0, clock() - scheduled))
        if isinstance(pacer, Interval) and spawn is not None:
            spawn(lambda: _tick(actor))
            scheduled = pacer.next(scheduled, clock())
            continue
        _tick(actor)
        done = clock()
        scheduled = pacer.next(scheduled, done)
        due = due_at() if due_at is not None else None
        if due is not None:
            scheduled = min(scheduled, max(done, due))


# ----- responses and statistics ------------------------------------------------


class ErrorKind(enum.StrEnum):
    """What went wrong with a response."""

    TRANSPORT = "transport"  # no response: refused, reset, timed out
    LOCKED = "locked"  # "database is locked" anywhere in the body
    SERVER = "5xx"
    HTTP = "http"  # another 4xx
    FAILURE = "failure"  # a `failure` outcome, JSON or XML


_FAILURE_JSON = re.compile(r'"summary"\s*:\s*"failure"')


def classify(status: int | None, body: str) -> ErrorKind | None:
    """The kind of error a response is, or None for a good one."""
    if status is None:
        return ErrorKind.TRANSPORT
    if "database is locked" in body.lower():
        return ErrorKind.LOCKED
    if status >= 500:
        return ErrorKind.SERVER
    if status >= 400:
        return ErrorKind.HTTP
    if _FAILURE_JSON.search(body) or "<failure" in body:
        return ErrorKind.FAILURE
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
    """Every request's latency and outcome, by endpoint label, and how late
    each client's ticks started, by client. Labels that start with `setup `
    or `director ` are the script's own requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._endpoints: dict[str, _Endpoint] = {}
        self._lags: dict[str, list[float]] = {}

    def record(
        self,
        endpoint: str,
        latency: float,
        kind: ErrorKind | None,
        status: int | None = None,
    ) -> None:
        with self._lock:
            row = self._endpoints.setdefault(endpoint, _Endpoint())
            row.latencies.append(latency)
            if kind is not None:
                row.errors += 1
            if kind is ErrorKind.SERVER or (status is not None and status >= 500):
                row.server_errors += 1
            if kind is ErrorKind.LOCKED:
                row.locked += 1
            if kind is ErrorKind.TRANSPORT:
                row.transport_errors += 1

    def record_lag(self, client: str, seconds: float) -> None:
        with self._lock:
            self._lags.setdefault(client, []).append(seconds)

    def snapshot(self) -> tuple[dict[str, _Endpoint], dict[str, list[float]]]:
        with self._lock:
            endpoints = {
                name: dataclasses.replace(row, latencies=list(row.latencies))
                for name, row in self._endpoints.items()
            }
            return endpoints, {name: list(v) for name, v in self._lags.items()}


@dataclass(frozen=True)
class ClientType:
    """A kind of client: how many run, how they pace, the endpoint label
    their requests go under (it matches itself and `<label> <more>`), the
    role they log in as, the browser whose session they share (instance i
    of each type with the same browser shares one session), and the range
    their first request falls in, in seconds after the start."""

    name: str
    instances: int
    pacer: Pacer
    endpoint: str
    role: str | None = None
    browser: str | None = None
    start: tuple[float, float] = (0.0, 0.0)

    def owns(self, endpoint: str) -> bool:
        return endpoint == self.endpoint or endpoint.startswith(self.endpoint + " ")


def _is_script_request(endpoint: str) -> bool:
    return endpoint.startswith(("setup ", "director "))


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def summarize(stats: Stats, elapsed: float, clients: Iterable[ClientType]) -> dict:
    endpoints = {}
    totals = dict.fromkeys(
        ("requests", "errors", "server_errors", "locked", "transport_errors"), 0
    )
    snapshot, lags = stats.snapshot()
    # The clients' endpoints first, in the order they were first seen.
    for name, row in sorted(snapshot.items(), key=lambda i: _is_script_request(i[0])):
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
        client_lags = lags.get(client.name, [])
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
                "lag_p95_ms": _ms(percentile(client_lags, 95)),
                "lag_max_ms": _ms(max(client_lags, default=None)),
            }
        )
    return {
        "elapsed_s": round(elapsed, 1),
        "endpoints": endpoints,
        "totals": totals,
        "rates": rates,
    }


THRESHOLD_ENDPOINTS = (
    "poll",
    "poll.kiosk",
    "poll.coordinator",
    "poll.kiosk.all",
    "replay.message",
    "timer-message HEARTBEAT",
    "timer-message FINISHED",
)
"""The endpoints `--max-p95-ms` and `--max-p99-ms` judge: the race-day polls
of #37's acceptance criterion and the timer's heartbeat and FINISHED."""

MIN_SAMPLES = 20
"""Fewer requests than this make no percentile worth judging."""


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
        if name not in THRESHOLD_ENDPOINTS or row["requests"] < MIN_SAMPLES:
            continue
        for key, limit in (("p95_ms", max_p95_ms), ("p99_ms", max_p99_ms)):
            if limit is not None and row[key] is not None and row[key] > limit:
                failures.append(f"{name} {key[:3]} {row[key]:.1f} ms > {limit:g} ms")
    return failures


NOTHING_STOOD_OUT = (
    "no sampled resource stood out (CPU, memory, SQLite's write lock, the load"
    " script's own lag); read the server log and the per-endpoint latency"
)


def likely_limits(report: dict) -> list[str]:
    """The resources that probably limited a failed run, from the samples.
    The rules, documented in `server/README.md`, are deliberately simple."""
    limits = []
    server = report.get("server") or {}
    locked = report["totals"]["locked"]
    if locked:
        limits.append(
            f"SQLite's write lock: {locked} `database is locked` errors; writers"
            " waited longer than the busy timeout"
        )
    host_mean = server.get("host_cpu_mean_pct")
    host_max = server.get("host_cpu_max_pct")
    if (host_mean is not None and host_mean >= 75) or (
        host_max is not None and host_max >= 95
    ):
        limits.append(
            f"CPU: the host's cores were busy, mean {host_mean}%, highest 1 s"
            f" {host_max}%; the server took {server.get('cpu_mean_pct')}% of one"
            " core on average"
        )
    start, end = server.get("rss_start_mb"), server.get("rss_end_mb")
    if start is not None and end is not None and end - start >= max(50.0, start / 4):
        limits.append(f"memory: the server's RSS grew from {start} to {end} MB")
    for rate in report.get("rates", []):
        lag = rate.get("lag_p95_ms")
        achieved = rate.get("achieved_pct")
        if (lag is not None and lag > 100) or (achieved is not None and achieved < 90):
            limits.append(
                f"the load script: {rate['client']} started its requests p95"
                f" {lag} ms late and reached {achieved}% of its expected rate; it"
                " may be short of CPU itself, so the server may be better than"
                " these numbers"
            )
    if limits:
        return limits
    if not server:
        return [
            "unknown: run with --pid to sample the server's CPU and memory; "
            + NOTHING_STOOD_OUT
        ]
    return [NOTHING_STOOD_OUT]


def _cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


SERVER_LABELS = {
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


def render_markdown(report: dict) -> str:
    lines = ["# DerbyNet load report", ""]
    environment = report.get("environment") or {}
    if environment:
        lines += ["| | |", "|---|---|"]
        lines += [f"| {key} | {value} |" for key, value in environment.items()]
        lines.append("")
    failures = report.get("failures", [])
    lines += ["## Verdict", ""]
    if failures:
        lines += ["**FAIL**", ""]
        lines += [f"- {failure}" for failure in failures]
        lines += ["", "Likely limit:", ""]
        lines += [f"- {limit}" for limit in report.get("limits", [])]
    else:
        lines.append("**PASS**: no errors, no 5xx, no `database is locked`,")
        lines.append("every FINISHED recorded, and every threshold met.")
    totals = report["totals"]
    thresholds = report.get("thresholds") or {}
    lines += [
        "",
        f"{totals['requests']} requests in {report['elapsed_s']} s:"
        f" {totals['errors']} errors, {totals['server_errors']} 5xx,"
        f" {totals['locked']} `database is locked`,"
        f" {totals['transport_errors']} failed connections.",
    ]
    if any(value is not None for value in thresholds.values()):
        lines += [
            "",
            f"Thresholds: p95 {_cell(thresholds.get('max_p95_ms'))} ms and p99"
            f" {_cell(thresholds.get('max_p99_ms'))} ms, on"
            f" {', '.join(THRESHOLD_ENDPOINTS)}, each with at least"
            f" {MIN_SAMPLES} requests.",
        ]
    lines += [
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
    columns = ("requests", "errors", "server_errors", "p50_ms", "p95_ms", "p99_ms")
    for name, row in report["endpoints"].items():
        cells = [name] + [_cell(row[key]) for key in (*columns, "max_ms")]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Rates",
        "",
        "Requests per second for all instances together. A client paced"
        " *after* each response can reach only `expected`, its delay plus the"
        " mean latency apart. Lag is how late the load script started a"
        " request after it was due.",
        "",
        "| Client | Instances | Pacing | Target/s | Expected/s | Achieved/s"
        " | % of expected | Lag p95 ms |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for rate in report["rates"]:
        lines.append(
            f"| {rate['client']} | {rate['instances']} | {rate['pacing']} |"
            f" {rate['target_per_s']:.2f} | {rate['expected_per_s']:.2f} |"
            f" {rate['achieved_per_s']:.2f} | {_cell(rate['achieved_pct'])} |"
            f" {_cell(rate.get('lag_p95_ms'))} |"
        )
    timer = report.get("timer")
    if timer:
        recorded = timer.get("finished_recorded")
        lines += [
            "",
            "## Timer",
            "",
            f"- FINISHED sent: {timer.get('finished_sent')}",
            f"- FINISHED accepted: {timer.get('finished_ok')}",
            "- Heats recorded by the server: "
            + ("not checked" if recorded is None else str(recorded)),
        ]
        if "passes" in timer:
            lines.append(
                f"- Restarts of the schedule: {timer['passes']}. Each purges the"
                " results and races the schedule again, load a race day doesn't"
                " have, so the timer races heats for the whole run."
            )
    server = report.get("server")
    if server:
        lines += ["", "## Server process", "", "| | |", "|---|---:|"]
        lines += [
            f"| {SERVER_LABELS.get(key, key)} | {_cell(value)} |"
            for key, value in server.items()
        ]
    return "\n".join(lines) + "\n"


# ----- the server's CPU and memory, from /proc ---------------------------------


@dataclass(frozen=True)
class ProcUsage:
    """The CPU seconds used and resident bytes held by a process tree, and
    how many processes it has, at one moment."""

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
    if seconds <= 0:
        return 0.0
    return 100 * (after.cpu_seconds - before.cpu_seconds) / seconds


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
                busy = self.host[-1][0] - self.host[0][0]
                out["host_cpu_mean_pct"] = round(100 * busy / span, 1)
                out["host_cpu_max_pct"] = round(max(host_steps), 1)
        return out


# ----- HTTP --------------------------------------------------------------------


class SetupError(Exception):
    """A login or a setup step failed, so the run cannot measure anything."""


class HttpSession:
    """One browser's session with `action.php`: a cookie jar and a pool of
    keep-alive connections, so requests that overlap each get a connection
    of their own, as a browser opens one. Each request is recorded in
    `stats`. It is safe to use from several threads."""

    def __init__(self, base_url: str, stats: Stats, timeout: float = 30.0):
        parts = urlsplit(base_url)
        if parts.scheme != "http":
            raise SetupError(f"only http:// URLs are supported, not {base_url}")
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 80
        self.path = parts.path.rstrip("/") + "/action.php"
        self.stats, self.timeout = stats, timeout
        self._lock = threading.Lock()
        self._idle: list[http.client.HTTPConnection] = []
        self._cookies: dict[str, str] = {}

    def _take(self) -> tuple[http.client.HTTPConnection, bool]:
        with self._lock:
            if self._idle:
                return self._idle.pop(), True
        return http.client.HTTPConnection(
            self.host, self.port, timeout=self.timeout
        ), False

    def _give(self, connection: http.client.HTTPConnection) -> None:
        with self._lock:
            self._idle.append(connection)

    def _cookie_header(self) -> str:
        with self._lock:
            return "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    def _keep_cookies(self, set_cookies: Iterable[str]) -> None:
        with self._lock:
            for line in set_cookies:
                name, _, rest = line.partition("=")
                value = rest.split(";", 1)[0]
                expired = "max-age=0" in line.lower().replace(" ", "")
                if value and not expired:
                    self._cookies[name.strip()] = value
                else:
                    self._cookies.pop(name.strip(), None)

    def _send(self, method: str, params: dict) -> tuple[int, str]:
        headers = {"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"}
        cookie = self._cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        url, body = self.path, None
        if method == "GET":
            url += "?" + urlencode(params)
        else:
            body = urlencode(params)
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        for attempt in (1, 2):
            connection, reused = self._take()
            try:
                connection.request(method, url, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
            except (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                BrokenPipeError,
            ):
                # The server closed an idle keep-alive connection; a browser
                # retries such a request on a new one.
                connection.close()
                if reused and attempt == 1:
                    continue
                raise
            except BaseException:
                connection.close()
                raise
            if response.will_close:
                connection.close()
            else:
                self._give(connection)
            self._keep_cookies(response.headers.get_all("Set-Cookie") or [])
            return response.status, data.decode("utf-8", "replace")
        raise AssertionError("unreachable")

    def request(self, label: str, method: str, params: dict) -> tuple[int | None, str]:
        started = time.perf_counter()
        try:
            status, text = self._send(method, params)
        except (OSError, http.client.HTTPException) as error:
            status, text = None, repr(error)
        latency = time.perf_counter() - started
        self.stats.record(label, latency, classify(status, text), status)
        return status, text


def login(session, role: str, password: str) -> None:
    """`role.login` on `session`, as each page and `derby-timer.jar` does."""
    status, text = session.request(
        "setup role.login",
        "POST",
        {"action": "role.login", "name": role, "password": password},
    )
    if status != 200 or classify(status, text) is not None:
        raise SetupError(f"could not log in as {role}: {status} {text[:200]}")


def _json(text: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def current_heat(status: int | None, text: str) -> tuple[int, int] | None:
    """The `roundid` and `heat` of a poll reply's `current-heat`, or None."""
    current = _json(text).get("current-heat") if status == 200 else None
    if not isinstance(current, dict) or "roundid" not in current:
        return None
    return current["roundid"], current.get("heat", 0)


# ----- the clients -------------------------------------------------------------

NOW_RACING_VALUES = (
    "current-heat,heat-results,precision,racers,timer-trouble,current-reschedule"
)

TV_HEIGHT = 1080
"""A 1080p TV, the screen the now-racing kiosk usually fills."""
TV_PHOTO_TOP = 100
"""Pixels above the first photo cell: the banner and the table header."""
TV_PHOTO_BORDER = 7
"""The photo cell's bottom border (`css/now-racing.css`)."""


def default_row_height(lanes: int) -> int:
    """`now-racing.js`'s `g_row_height` on a 1080p TV: the height below the
    first photo cell split over the lanes, less the cell's bottom border.
    The banner height and border are approximations; `--row-height` sets
    another value."""
    return (TV_HEIGHT - TV_PHOTO_TOP) // lanes - TV_PHOTO_BORDER


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

    def __init__(self, session: HttpSession, row_height: int):
        self.session, self.row_height = session, row_height
        self.roundid, self.heat = 0, 0

    def tick(self) -> None:
        params = {
            "query": "poll",
            "values": NOW_RACING_VALUES,
            "roundid": self.roundid,
            "heat": self.heat,
            "row-height": self.row_height,
        }
        heat = current_heat(*self.session.request("poll", "GET", params))
        if heat is not None:
            self.roundid, self.heat = heat


class CoordinatorPoller:
    """`coordinator-poll.js`: `poll.coordinator` for the heat it shows,
    which starts at round 0, heat 0."""

    def __init__(self, session: HttpSession):
        self.session, self.roundid, self.heat = session, 0, 0

    def tick(self) -> None:
        params = {
            "query": "poll.coordinator",
            "roundid": self.roundid,
            "heat": self.heat,
        }
        heat = current_heat(*self.session.request("poll.coordinator", "GET", params))
        if heat is not None:
            self.roundid, self.heat = heat


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


# ----- the timer ---------------------------------------------------------------

_HEAT_READY = re.compile(r"<heat-ready\b([^>]*)>")
_ATTRIBUTE = re.compile(r'([\w-]+)="([^"]*)"')


class Phase(enum.Enum):
    """Where the timer is in `derby-timer.jar`'s handshake."""

    HELLO = enum.auto()  # say HELLO
    IDENTIFY = enum.auto()  # say IDENTIFIED
    CONNECTED = enum.auto()


class Staged(NamedTuple):
    """The heat a `<heat-ready>` gave the timer."""

    roundid: int
    heat: int
    lane_mask: int


TIMER_FLAGS = {
    # Settable flags of timer/src/org/jeffpiazza/derby/Flag.java, as
    # Message.Flags writes them: name, type, value, description.
    "t": ("bool", "false", "Trace non-heartbeat messages sent"),
    "th": ("bool", "false", "Trace heartbeat messages sent"),
    "r": ("bool", "false", "Trace responses to traced messages"),
    "ignore-place": ("bool", "false", "Discard any place indications from timer"),
    "pace": ("int", "0", "Seconds between simulated heats"),
    "reset-after-start": (
        "int",
        "10",
        "Reset timer <nsec> seconds after heat start, default 10.  Set 0 for never.",
    ),
    "delay-reset-after-race": ("long", "10", "Delay reset after race"),
}
TIMER_DEVICES = {
    "FastTrackDevice": "FastTrack K, P or Q series",
    "ChampDevice": "Champ Timer (BestTrack)",
    "SmartLineDevice": "Micro Wizard SmartLine",
}


class TimerSim:
    """`derby-timer.jar` with a simulated timer, one message at a time.

    `next_message(now)` is the message to send at `now`,
    `on_response(label, body, now)` reads the server's reply, and `due_at()`
    says when the next message other than a heartbeat is due."""

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
        self.phase = Phase.HELLO
        self.queue: list[str] = []  # messages queued by the server, e.g. FLAGS
        self.staged: Staged | None = None
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

    def _flags(self) -> tuple[str, dict]:
        params = {}
        for name, (kind, value, description) in TIMER_FLAGS.items():
            params[f"flag-{name}"] = f"{kind}:{value}"
            params[f"desc-{name}"] = description
        params["ports"] = ""
        params.update({f"device-{name}": text for name, text in TIMER_DEVICES.items()})
        return self._message("FLAGS", **params)

    def _finish_time(self) -> float:
        assert self.started_at is not None
        return self.started_at + max(
            (float(t) for t in self.race_times.values()), default=0.0
        )

    def due_at(self) -> float | None:
        """When the next message other than a heartbeat is due: 0 for one
        that goes at once, None when only heartbeats are due."""
        if self.queue or self.phase is not Phase.CONNECTED:
            return 0.0
        if self.staged is None:
            return None
        if self.started_at is None:
            return self.staged_at + self.stage_seconds
        return self._finish_time()

    def next_message(self, now: float) -> tuple[str, dict]:
        if self.queue:
            name = self.queue.pop(0)
            if name == "FLAGS":
                return self._flags()
        if self.phase is Phase.HELLO:
            return self._message("HELLO", interface="jar", build="load-test")
        if self.phase is Phase.IDENTIFY:
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
                self.race_times = {
                    lane: f"{self.rng.uniform(self.fastest, self.slowest):.4f}"
                    for lane in range(1, self.lanes + 1)
                    if self.staged.lane_mask & (1 << (lane - 1))
                }
                return self._message("STARTED")
        elif self.staged is not None and now >= self._finish_time():
            self.finished_sent += 1
            lanes = {f"lane{lane}": t for lane, t in self.race_times.items()}
            return self._message(
                "FINISHED",
                roundid=str(self.staged.roundid),
                heat=str(self.staged.heat),
                **lanes,
            )
        return self._message("HEARTBEAT", confirmed="1")

    def _unstage(self, now: float) -> None:
        self.staged, self.started_at, self.race_times = None, None, {}
        self.restart_idle(now)

    def restart_idle(self, now: float) -> None:
        """Start counting the time without a heat afresh from `now`."""
        self.idle_since = now

    def on_response(self, label: str, body: str, now: float) -> None:
        ok = "<success" in body and "<failure" not in body
        message = label.removeprefix("timer-message ")
        if message == "HELLO" and ok and self.phase is Phase.HELLO:
            self.phase = Phase.IDENTIFY
        elif message == "IDENTIFIED" and ok:
            self.phase = Phase.CONNECTED
            self.restart_idle(now)
        elif message == "FINISHED":
            if ok:
                self.finished_ok += 1
            self._unstage(now)
        if "<query" in body and "FLAGS" not in self.queue:
            self.queue.append("FLAGS")
        if "<abort" in body:
            self._unstage(now)
        match = _HEAT_READY.search(body)
        if match and self.started_at is None:
            attrs = dict(_ATTRIBUTE.findall(match.group(1)))
            try:
                self.staged = Staged(
                    int(attrs["roundid"]), int(attrs["heat"]), int(attrs["lane-mask"])
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
    """The timer's actor: sends `TimerSim`'s messages, and asks the director
    to restart racing once the timer has waited `rearm_after` seconds
    without a heat."""

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

    def due_at(self) -> float | None:
        return self.sim.due_at()

    def tick(self) -> None:
        label, params = self.sim.next_message(self.clock())
        status, text = self.session.request(label, "POST", params)
        self.sim.on_response(label, text if status == 200 else "", self.clock())
        idle = self.sim.idle_seconds(self.clock())
        if self.director is not None and idle > self.rearm_after:
            self.director.rearm()
            self.sim.restart_idle(self.clock())


# ----- setup and the race director ---------------------------------------------

GROUPS = ("Lions", "Tigers", "Wolves", "Bears")


def _heats_run(poll_coordinator: dict) -> int:
    rounds = poll_coordinator.get("rounds") or []
    return sum(r.get("heats_run", 0) for r in rounds if isinstance(r, dict))


class Director:
    """The script's coordinator: sets the event up, restarts racing when the
    schedule runs out, and counts the heats the server recorded."""

    def __init__(self, session: HttpSession, loop: bool):
        self.session, self.loop = session, loop
        self.recorded = 0  # heats run in earlier passes
        self.baseline = 0  # heats run before this pass's first FINISHED
        self.passes = 0
        self.first_roundid: int | None = None
        self.heats_known = True

    def login(self, role: str, password: str) -> None:
        login(self.session, role, password)

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
        group_names = (
            GROUPS[:groups]
            if groups <= len(GROUPS)
            else [f"Group {n}" for n in range(1, groups + 1)]
        )
        self._post("setup database.execute", action="database.execute", script="schema")
        self._post(
            "setup database.execute", action="database.execute", script="update-schema"
        )
        self._post(
            "setup settings.write",
            action="settings.write",
            **{
                "n-lanes": lanes,
                "unused-lane-mask": 0,
                "use-master-sched-checkbox": 1,
                "use-master-sched": 1,
                # The default, as demo.sh sets it: now-racing shows head shots.
                "photos-on-now-racing": "head",
            },
        )
        for i in range(racers):
            group = group_names[i % len(group_names)]
            self._post(
                "setup racer.import",
                action="racer.import",
                firstname=f"Racer{i + 1}",
                lastname=group,
                partition=group,
                carnumber=100 * (i % len(group_names) + 1) + i // len(group_names) + 1,
            )
        self._post(
            "setup racer.bulk", action="racer.bulk", who="all", what="checkin", value=1
        )
        rounds = self._get("setup poll", query="poll", values="rounds").get("rounds")
        if not rounds:
            raise SetupError("setup made no rounds")
        for entry in rounds:
            self._post(
                "setup schedule.generate",
                action="schedule.generate",
                roundid=entry["roundid"],
            )
        for address in kiosk_addresses:
            self._get("setup poll.kiosk", query="poll.kiosk", address=address)
        self._post(
            "setup kiosk.assign", action="kiosk.assign", all="kiosks/now-racing.kiosk"
        )
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

    def _coordinator_poll(self) -> dict:
        return self._get("director poll.coordinator", query="poll.coordinator")

    def start(self) -> None:
        try:
            self.baseline = _heats_run(self._coordinator_poll())
        except SetupError as error:
            print(f"cannot count the heats run: {error}", file=sys.stderr)
            self.heats_known = False

    def rearm(self) -> None:
        """After the schedule's last heat: purge the results and race it
        again. Only an event the script set up is purged."""
        if not self.loop or self.first_roundid is None:
            return
        try:
            state = self._coordinator_poll()
            if (state.get("current-heat") or {}).get("now_racing"):
                return
            self.recorded += _heats_run(state) - self.baseline
            self._post(
                "director database.purge", action="database.purge", purge="results"
            )
            self.baseline = 0
            self._start_racing("director heat.select")
            self.passes += 1
        except SetupError as error:
            print(f"could not restart racing: {error}", file=sys.stderr)

    def finished_recorded(self) -> int | None:
        if not self.heats_known:
            return None
        try:
            return self.recorded + _heats_run(self._coordinator_poll()) - self.baseline
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
    machine_parts = (
        socket.gethostname(),
        cpu,
        f"{os.cpu_count()} cores",
        f"{int(memory) // 1024} MB RAM" if memory else None,
        platform.machine(),
    )
    return {
        "Date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "Machine": ", ".join(part for part in machine_parts if part),
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
        description="Simulate race-day polling against a DerbyNet server (#37)."
        ' server/README.md, "Race-day load test", describes it.',
    )
    add = parser.add_argument
    add("--url", default="http://127.0.0.1:8000", help="the server's base URL")
    add("--duration", type=float, default=60, help="seconds to run")
    add("--kiosks", type=int, default=10)
    add("--setup", action="store_true", help="reset the event and race it")
    add("--racers", type=int, default=40, help="racers --setup imports")
    add("--groups", type=int, default=4, help="groups --setup makes")
    add("--lanes", type=int, default=4)
    add("--row-height", type=int, help="now-racing's row-height; default 1080p TV")
    add("--stage-seconds", type=float, default=1.0)
    add("--pid", type=int, help="the server process to sample")
    add("--json", type=Path, help="also write the report here as JSON")
    add("--markdown", type=Path, help="also write the markdown here")
    add("--max-p95-ms", type=float)
    add("--max-p99-ms", type=float)
    add("--coordinator-password", default="doyourbest")
    add("--timer-password", default="")
    add("--seed", type=int, default=37)
    add("--server", default="", help="how the server runs, for the report")
    add("--note", default="", help="a line for the report")
    return parser.parse_args(argv)


def client_types(kiosks: int) -> list[ClientType]:
    """Every client of the run, each rate defined here once."""
    return [
        ClientType(
            "kiosk poll.kiosk",
            kiosks,
            Interval(5.0),
            "poll.kiosk",
            browser="kiosk",
            start=(0.0, 5.0),
        ),
        ClientType(
            "kiosk now-racing poll",
            kiosks,
            After(0.5),
            "poll",
            browser="kiosk",
            start=(1.0, 1.5),
        ),
        ClientType("timer", 1, After(0.5), "timer-message", role="Timer"),
        ClientType(
            "coordinator", 1, Every(2.0), "poll.coordinator", role="RaceCoordinator"
        ),
        ClientType("replay", 1, Every(0.25), "replay.message", role="RaceCoordinator"),
        ClientType(
            "kiosk dashboard", 1, After(2.0), "poll.kiosk.all", role="RaceCoordinator"
        ),
    ]


def run(args: argparse.Namespace, stop: threading.Event) -> dict:
    stats = Stats()
    rng = random.Random(args.seed)
    clock = time.monotonic
    passwords = {
        "RaceCoordinator": args.coordinator_password,
        "Timer": args.timer_password,
    }
    addresses = [f"load-kiosk-{n:02d}" for n in range(1, args.kiosks + 1)]
    row_height = args.row_height or default_row_height(args.lanes)

    director = Director(HttpSession(args.url, stats), loop=args.setup)
    director.login("RaceCoordinator", args.coordinator_password)
    if args.setup:
        director.setup(args.racers, args.groups, args.lanes, addresses)
    director.start()

    sim = TimerSim(
        args.lanes, random.Random(args.seed + 1), stage_seconds=args.stage_seconds
    )
    builders: dict[str, Callable[[HttpSession, int], object]] = {
        "poll.kiosk": lambda session, i: KioskPoller(session, addresses[i]),
        "poll": lambda session, i: NowRacingPoller(session, row_height),
        "timer-message": lambda session, i: TimerClient(session, sim, clock, director),
        "poll.coordinator": lambda session, i: CoordinatorPoller(session),
        "replay.message": lambda session, i: ReplayPoller(session),
        "poll.kiosk.all": lambda session, i: DashboardPoller(session),
    }
    browsers: dict[tuple[str, int], HttpSession] = {}
    actors = []
    clients = client_types(args.kiosks)
    for client in clients:
        for i in range(client.instances):
            if client.browser is not None and (client.browser, i) in browsers:
                session = browsers[(client.browser, i)]
            else:
                session = HttpSession(args.url, stats)
                if client.role is not None:
                    login(session, client.role, passwords[client.role])
                if client.browser is not None:
                    browsers[(client.browser, i)] = session
            actor = builders[client.endpoint](session, i)
            actors.append((client, actor, rng.uniform(*client.start)))

    sampler = ProcSampler(args.pid, stop) if args.pid else None
    if sampler:
        sampler.start()
    # Threads for the overlapping requests of Interval clients.
    pool = ThreadPoolExecutor(
        max_workers=4 * args.kiosks + 4, thread_name_prefix="tick"
    )
    start = clock()
    deadline = start + args.duration
    threads = [
        threading.Thread(
            target=run_actor,
            args=(actor, client.pacer, start + offset, clock, stop.wait, deadline),
            kwargs={
                "spawn": pool.submit,
                "lag": lambda seconds, name=client.name: stats.record_lag(
                    name, seconds
                ),
            },
            daemon=True,
            name=type(actor).__name__,
        )
        for client, actor, offset in actors
    ]
    for thread in threads:
        thread.start()
    while any(t.is_alive() for t in threads) and not stop.wait(0.2):
        pass
    elapsed = min(clock(), deadline) - start
    stop.set()
    for thread in threads:
        thread.join(timeout=35)
    pool.shutdown(wait=True, cancel_futures=True)

    report = summarize(stats, elapsed, clients)
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
    if report["failures"]:
        report["limits"] = likely_limits(report)
    text = render_markdown(report)
    print(text)
    if args.markdown:
        args.markdown.write_text(text)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
