"""SPIKE #71 ONLY. Views for both prototypes.

Candidate A (htmx + Alpine.js) gets server-rendered fragments. Candidate B
(Svelte) is a static bundle under /spike/b/ that fetches JSON: the legacy
`action.php?query=poll` for now racing (the same contract the PHP page uses)
and /spike/api/* for standings and leaders.

Every kiosk view is framed by replay.php, so it answers with X-Frame-Options
SAMEORIGIN. The skeleton's default (DENY, from XFrameOptionsMiddleware) would
blank the page inside the replay iframe.
"""

import json
import time
from pathlib import Path

from django.http import (
    Http404,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.static import serve

from kiosks import polls as kiosk_polls
from spike import data

SVELTE_DIST = Path(__file__).resolve().parent.parent / "svelte" / "dist"


def _event():
    event = data.active_event()
    if event is None:
        raise Http404("No event is open")
    return event


def index(request):
    return render(request, "spike/index.html")


def replay_harness(request):
    return render(
        request, "spike/replay_harness.html", {"src": request.GET.get("src", "")}
    )


# ----- shared JSON --------------------------------------------------------------


def api_standings(request):
    return JsonResponse(data.standings(_event(), request.GET.get("key")))


def api_leaders(request):
    return JsonResponse(data.leaders(_event()))


def events(request):
    """Push spike (#56): a server-sent event stream that names a change as
    soon as the change signature moves. Each kiosk then fetches what it
    shows, as it would on a poll. Polled every 50 ms here; the real design
    would be notified by the write instead."""
    event = _event()

    def stream():
        last = None
        deadline = time.monotonic() + 600
        yield "retry: 1000\n\n"
        while time.monotonic() < deadline:
            sig = data.change_signature(event)
            if sig != last:
                last = sig
                yield f"event: change\ndata: {sig}\n\n"
            time.sleep(0.05)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    return response


# ----- candidate A: Django templates + htmx + Alpine.js ------------------------


@xframe_options_sameorigin
def a_now_racing(request):
    return render(
        request,
        "spike/a/now_racing.html",
        {"push": request.GET.get("push") == "1"},
    )


def a_now_racing_board(request):
    """The board fragment. The page sends the heat it shows and how many
    results it has; an unchanged board answers 204, so htmx swaps nothing
    and no animation replays."""
    roundid = request.GET.get("roundid") or None
    heat = request.GET.get("heat") or None
    try:
        shown_results = int(request.GET.get("nresults") or 0)
    except ValueError:
        shown_results = 0
    board = data.now_racing_board(_event(), roundid, heat, shown_results)

    def text(value):
        return "" if value is None else str(value)

    new_heat = (text(board["roundid"]), text(board["heat"])) != (
        text(roundid),
        text(heat),
    )
    unchanged = not new_heat and text(board["nresults"]) == request.GET.get(
        "nresults", ""
    )
    # While the page lingers on results it keeps its lineup.
    if unchanged or (new_heat and heat and request.GET.get("hold")):
        return HttpResponse(status=204)
    response = render(
        request,
        "spike/a/_board.html",
        {"board": board, "new_heat": new_heat},
    )
    if new_heat:
        # A new lineup slides in through the View Transitions API, or with
        # ?vt=0 after htmx's 450 ms swap delay lets the old one slide out.
        vt = request.GET.get("vt") != "0"
        response["HX-Reswap"] = (
            "innerHTML transition:true" if vt else "innerHTML swap:450ms"
        )
    return response


def a_kiosk_poll(request):
    """`poll.kiosk` for an htmx page: no body, and the kiosk's parameters as
    an `HX-Trigger` event the page's Alpine component listens for."""
    setting = kiosk_polls.poll_kiosk(_event(), request.GET.get("address"))
    params = json.loads((setting or {}).get("parameters") or "{}")
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"kiosk-params": params})
    return response


@xframe_options_sameorigin
def a_standings(request):
    return render(
        request,
        "spike/a/standings.html",
        {
            "address": request.GET.get("address", "spike-a"),
            "push": request.GET.get("push") == "1",
        },
    )


def a_standings_table(request):
    return render(
        request,
        "spike/a/_standings_table.html",
        data.standings(_event(), request.GET.get("key")),
    )


@xframe_options_sameorigin
def a_leaders(request):
    return render(request, "spike/a/leaders.html", {"vt": request.GET.get("vt") != "0"})


def a_leaders_panel(request):
    panels = data.leaders(_event())["panels"]
    if not panels:
        return HttpResponse("<p>No standings yet.</p>")
    i = int(request.GET.get("i", 0)) % len(panels)
    return render(
        request,
        "spike/a/_leaders_panel.html",
        {
            "panel": panels[i],
            "next": (i + 1) % len(panels),
            "vt": request.GET.get("vt") != "0",
        },
    )


# ----- candidate B: the Svelte bundle, served as static files --------------------


@xframe_options_sameorigin
def b_static(request, path):
    return serve(request, path or "index.html", document_root=SVELTE_DIST)
