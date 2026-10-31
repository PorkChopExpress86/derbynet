#!/usr/bin/env python3
"""SPIKE #71: the dropped-frame table from Chromium traces, counted as
tools/trace.js counts them. Each PipelineReporter event is one frame the
compositor presented, partially presented or dropped (what the Performance
panel's Frames track shows). "Animating" frames carry a main-thread or
compositor animation.

    python3 summarize-trace.py OUT/*.trace.json
"""

import json
import os
import sys


def summarize(path):
    with open(path) as f:
        raw = json.load(f)
    events = raw["traceEvents"] if isinstance(raw, dict) else raw
    counts = {}
    for e in events:
        if e.get("name") != "PipelineReporter" or e.get("ph") != "b":
            continue
        args = e.get("args", {})
        r = args.get("frame_reporter") or args.get("chrome_frame_reporter") or {}
        if not (r.get("has_main_animation") or r.get("has_compositor_animation")):
            continue
        state = r.get("state", "unknown").replace("STATE_", "")
        counts[state] = counts.get(state, 0) + 1
    shown = counts.get("PRESENTED_ALL", 0) + counts.get("PRESENTED_PARTIAL", 0)
    dropped = counts.get("DROPPED", 0)
    pct = 100 * dropped / (shown + dropped) if shown + dropped else 0
    return shown, counts.get("PRESENTED_PARTIAL", 0), dropped, pct


print(f"{'run':28} {'shown':>7} {'partial':>8} {'dropped':>8} {'dropped %':>10}")
for path in sys.argv[1:]:
    name = os.path.basename(path).replace(".trace.json", "")
    try:
        shown, partial, dropped, pct = summarize(path)
    except (OSError, ValueError) as error:
        print(f"{name:28} unreadable: {error}")
        continue
    print(f"{name:28} {shown:7} {partial:8} {dropped:8} {pct:9.1f}%")
