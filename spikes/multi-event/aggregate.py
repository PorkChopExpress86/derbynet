"""Summarize run_matrix.sh output. Usage: python aggregate.py RESULTS_DIR"""
import glob
import json
import os
import statistics
import sys
from collections import defaultdict


def span(xs, fmt="{:.0f}"):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "n/a"
    return f"{fmt.format(statistics.median(xs))} ({fmt.format(min(xs))}-{fmt.format(max(xs))})"


runs = defaultdict(list)
for path in sorted(glob.glob(os.path.join(sys.argv[1], "*.summary.json"))):
    cfg = os.path.basename(path).rsplit("-r", 1)[0]
    if os.path.getsize(path):
        runs[cfg].append(json.load(open(path)))

cols = ["config", "runs", "requests", "failed", "switches ok/total", "stale", "in-flight old",
        "all workers on new event, s", "switches with a worker unobserved", "switch request, ms",
        "kiosk p50/p99, ms", "timer p50/p99, ms"]
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
for cfg, rs in sorted(runs.items()):
    print("| " + " | ".join([
        cfg,
        str(len(rs)),
        str(sum(r["requests"] for r in rs)),
        str(sum(r["failed"] for r in rs)),
        f'{sum(r["switches_ok"] for r in rs)}/{sum(r["switches_ok"] + r["switches_failed"] for r in rs)}',
        str(sum(r["stale"] for r in rs)),
        str(sum(r["in_flight_old"] for r in rs)),
        "median " + span([r["converge_all_workers_s"]["median"] for r in rs], "{:.2f}")
        + ", max " + span([r["converge_all_workers_s"]["max"] for r in rs], "{:.2f}"),
        str(sum(r["switches_without_all_workers_observed"] for r in rs)),
        span([r["switch_request_ms"]["median"] for r in rs]),
        span([r["latency_ms"]["kiosk/poll"]["p50"] for r in rs], "{:.1f}") + " / "
        + span([r["latency_ms"]["kiosk/poll"]["p99"] for r in rs]),
        span([r["latency_ms"]["timer/heartbeat"]["p50"] for r in rs], "{:.1f}") + " / "
        + span([r["latency_ms"]["timer/heartbeat"]["p99"] for r in rs]),
    ]) + " |")
print("\nCells with a range show the median across runs, then (min-max) across runs.")
