"""Results by racer (#49): `racer-results.php` as one card per racer per
round, over `racing.services.racer_results`.

The page is public, as kiosk data (#15, D5). `?racerid=` keeps one racer;
it is parsed as an integer, where PHP put the raw text into its SQL (S13),
and anything else shows every racer. The rounds poll every second, as
`results-by-racer-update.js` polls `poll.results`, and answer 204 while the
signature of what they show holds: a hash of the rendering, so the 204
covers every name, time and photo (#45 retro).
"""

import hashlib

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import get_script_prefix
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET

from racing.services.racer_results import results_by_racer
from ui.navigation import requires_page
from ui.responses import active_event, posted_int

guard = requires_page("racer-results")

POLL_SECONDS = 1


def _racer_id(request) -> int | None:
    return posted_int(request.GET.get("racerid", ""), positive=True)


def rounds_html(request) -> tuple[str, str]:
    """The rounds and their signature."""
    racer_id = _racer_id(request)
    found = results_by_racer(active_event(request), racer_id)
    html = render_to_string(
        "ui/results/rounds.html",
        {"results": found, "prefix": get_script_prefix(), "racer_id": racer_id},
        request,
    )
    sig = hashlib.sha256(html.encode()).hexdigest()[:20]
    # The rendered template, so it is HTML already.
    sig_input = f'<input type="hidden" id="results-sig" name="sig" value="{sig}">'
    return mark_safe(sig_input + html), sig  # noqa: S308


@require_GET
def page(request):
    html, _ = rounds_html(request)
    return render(
        request,
        "ui/results/page.html",
        {
            "rounds_html": html,
            "racer_id": _racer_id(request),
            "poll_seconds": POLL_SECONDS,
        },
    )


@require_GET
@guard
def rounds(request):
    """The 1 s poll. 204 while the rounds are what the page sent the
    signature of."""
    html, sig = rounds_html(request)
    if request.GET.get("sig") == sig:
        return HttpResponse(status=204)
    return HttpResponse(html)
