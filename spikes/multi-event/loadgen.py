"""Load and switch driver for the multi-event spike. Standard library only.

Clients: 10 kiosks (GET every 5 s), 1 coordinator (GET every 2 s), 1 timer
(POST every 0.5 s). After a warmup, a switcher POSTs /switch every PERIOD
seconds, cycling through the events, SWITCHES times.

Every endpoint returns the event it served (`event`) and the event its data
rows belong to (`rows_event`). A response is:
  failed  when the HTTP status is not 200, the request raised, or event != rows_event;
  stale   when it was sent after a switch committed and served an event other than the
          switch target (unless it was answered from the next switch's target after that commit).

Usage: python loadgen.py BASE_URL OUT_JSONL [--switches 20] [--period 6] [--events e1,e2,e3]
Prints a one-line JSON summary on stdout.
"""
import argparse
import json
import threading
import time
import urllib.error
import urllib.request

KIOSKS, KIOSK_PERIOD = 10, 5.0
COORD_PERIOD = 2.0
TIMER_PERIOD = 0.5
TIMEOUT = 10.0


def call(url, method):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    t_send = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            status, body = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        status, body = e.code, {"error": e.read()[:200].decode(errors="replace")}
    except Exception as e:  # connection refused, reset, timeout
        status, body = 0, {"error": repr(e)}
    return {"t_send": t_send, "t_recv": time.time(), "status": status, **body}


def poller(base, kind, method, period, offset, stop, out, lock):
    start = time.time() + offset
    k = 0
    while not stop.is_set():
        next_at = start + k * period
        k += 1
        delay = next_at - time.time()
        if delay > 0 and stop.wait(delay):
            return
        rec = call(f"{base}/{kind}", method)
        rec["kind"] = kind
        with lock:
            out.append(rec)


def run(base, switches, period, events, warmup, scale):
    stop, lock, out, switch_log = threading.Event(), threading.Lock(), [], []
    kp = KIOSK_PERIOD / scale
    specs = [("kiosk/poll", "GET", kp, i * kp / KIOSKS) for i in range(KIOSKS)]
    specs += [("coord/poll", "GET", COORD_PERIOD / scale, 0.1), ("timer/heartbeat", "POST", TIMER_PERIOD / scale, 0.05)]
    threads = [threading.Thread(target=poller, args=(base, *s, stop, out, lock), daemon=True) for s in specs]
    for t in threads:
        t.start()
    time.sleep(warmup)
    for i in range(switches):
        target = events[(i + 1) % len(events)]
        rec = call(f"{base}/switch?to={target}", "POST")
        rec["target"] = target
        switch_log.append(rec)
        time.sleep(max(0.0, period - (rec["t_recv"] - rec["t_send"])))
    stop.set()
    for t in threads:
        t.join(timeout=TIMEOUT + 1)
    return out, switch_log


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def analyze(out, switch_log):
    ok = lambda r: r["status"] == 200 and r.get("event") is not None and r["event"] == r.get("rows_event")
    failed = [r for r in out if not ok(r)]
    pids = sorted({r["pid"] for r in out if "pid" in r})
    commits = [s for s in switch_log if s["status"] == 200]
    stale, in_flight_old, converge, first_new, unobserved = [], 0, [], [], 0
    for i, s in enumerate(commits):
        c = s["commit_ts"]
        end = commits[i + 1]["commit_ts"] if i + 1 < len(commits) else float("inf")
        window = sorted((r for r in out if ok(r) and c <= r["t_send"] < end), key=lambda r: r["t_send"])
        nxt = commits[i + 1] if i + 1 < len(commits) else None
        # A request sent just before the next commit can be served from the next event. That is not stale.
        newer = lambda r: nxt is not None and r["event"] == nxt["to"] and r["t_recv"] > nxt["commit_ts"]
        stale += [{**r, "switch": i, "expected": s["to"]} for r in window if r["event"] != s["to"] and not newer(r)]
        in_flight_old += sum(1 for r in out if ok(r) and r["t_send"] < c < r["t_recv"] and r["event"] == s["from"])
        firsts = {}
        for r in window:
            if r["event"] == s["to"]:
                firsts.setdefault(r["pid"], r["t_recv"] - c)
        if firsts:
            first_new.append(min(firsts.values()))
        if set(firsts) >= set(pids):
            converge.append(max(firsts.values()))
        else:
            unobserved += 1
    lat = lambda kind: [1000 * (r["t_recv"] - r["t_send"]) for r in out if r["kind"] == kind and ok(r)]
    switch_ms = [1000 * (s["t_recv"] - s["t_send"]) for s in switch_log]
    return {
        "requests": len(out),
        "failed": len(failed),
        "failed_samples": [{k: r.get(k) for k in ("kind", "status", "event", "rows_event", "error")} for r in failed[:5]],
        "switches_ok": len(commits),
        "switches_failed": len(switch_log) - len(commits),
        "stale": len(stale),
        "stale_samples": [{k: r.get(k) for k in ("kind", "pid", "event", "expected", "switch")} for r in stale[:5]],
        "in_flight_old": in_flight_old,
        "workers": len(pids),
        "converge_all_workers_s": {"median": pct(converge, 50), "max": pct(converge, 100), "n": len(converge)},
        "switches_without_all_workers_observed": unobserved,
        "first_new_response_s": {"median": pct(first_new, 50), "max": pct(first_new, 100)},
        "switch_request_ms": {"median": pct(switch_ms, 50), "max": pct(switch_ms, 100)},
        "migrate_in_switch_ms": {"median": pct([1000 * s.get("migrate_s", 0) for s in commits], 50)},
        "latency_ms": {k: {"p50": pct(lat(k), 50), "p99": pct(lat(k), 99), "n": len(lat(k))}
                       for k in ("kiosk/poll", "coord/poll", "timer/heartbeat")},
        "achieved_rps": round(len(out) / (max(r["t_recv"] for r in out) - min(r["t_send"] for r in out)), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("--switches", type=int, default=20)
    ap.add_argument("--period", type=float, default=6.0)
    ap.add_argument("--events", default="e1,e2,e3")
    ap.add_argument("--warmup", type=float, default=5.0)
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every poller's request rate")
    ap.add_argument("--reanalyze", action="store_true", help="recompute the summary from an existing OUT_JSONL")
    a = ap.parse_args()
    if a.reanalyze:
        rows = [json.loads(line) for line in open(a.out)]
        print(json.dumps(analyze([r for r in rows if r["kind"] != "switch"],
                                 [r for r in rows if r["kind"] == "switch"])))
        return
    out, switch_log = run(a.base.rstrip("/"), a.switches, a.period, a.events.split(","), a.warmup, a.scale)
    with open(a.out, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
        for s in switch_log:
            f.write(json.dumps({"kind": "switch", **s}) + "\n")
    print(json.dumps(analyze(out, switch_log)))


if __name__ == "__main__":
    main()
