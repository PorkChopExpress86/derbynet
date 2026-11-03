"""The awards presentation console (#45): `awards-presentation.php` and
`awards-presentation.js`, over `awards.presentation`.

Tap an award to present it, hidden; Reveal shows it on the award kiosk;
Next presents the next award in the filtered list; Clear blanks the kiosk.
The award being presented polls every 1 s and answers 204 while it holds.
Each button is one service call under the `RaceState` lock, and answers
with the award being presented, out of band. Nothing here clears the
current award as a side effect: only Clear does. No GET writes anything.
"""

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from awards import presentation
from awards.editing import AwardFailure
from core.settings_form import PackStandingsRefused, set_pack_standings
from ui.navigation import requires_page
from ui.presenter import state
from ui.responses import active_event as _event
from ui.responses import refused, service_error

guard = requires_page("awards-presentation")


def _type(value) -> int | None:
    try:
        n = int(str(value).strip())
    except TypeError, ValueError:
        return None
    return n if 0 < n < 2**31 else None


def _current_html(request) -> str:
    return render_to_string(
        "ui/presenter/current.html",
        {"current": state.current(_event(request))},
        request,
    )


def _shown(request) -> HttpResponse:
    """A button's reply: the award being presented, out of band."""
    return HttpResponse(
        f'<div id="presenter-current" hx-swap-oob="innerHTML">'
        f"{_current_html(request)}</div>"
    )


@require_GET
def page(request):
    event = _event(request)
    return render(
        request,
        "ui/presenter/page.html",
        {
            "listing": presentation.presentation(event),
            "current": state.current(event),
            "kiosks": presentation.award_kiosks(event),
            "poll_seconds": state.POLL_SECONDS,
            "page_title": "Present Awards",
        },
    )


@require_GET
@guard
def current(request):
    """The 1 s poll of the award being presented. 204 while the key and the
    reveal flag the page sent still hold: one read of `RaceState`."""
    *_, sig = state.signature(_event(request))
    if request.GET.get("sig") == sig:
        return HttpResponse(status=204)
    return HttpResponse(_current_html(request))


@require_POST
@guard
def present(request):
    """Tap an award: the kiosks show it, hidden until Reveal."""
    try:
        presentation.present_award(_event(request), request.POST.get("key", ""))
    except AwardFailure as failure:
        return refused(request, failure)
    return _shown(request)


@require_POST
@guard
def next_award(request):
    """Next: the award after the one presented, in the list as the type and
    group filters show it."""
    scope = request.POST.get("scope", presentation.ALL)
    try:
        presentation.present_next(
            _event(request), _type(request.POST.get("type", "")), scope
        )
    except AwardFailure as failure:
        return refused(request, failure)
    return _shown(request)


@require_POST
@guard
def reveal(request):
    """Reveal or hide the award presented: `reveal=1` or `reveal=0`."""
    value = request.POST.get("reveal", "")
    if value not in ("0", "1"):
        return service_error(request, "Reveal or hide the award.")
    presentation.reveal(_event(request), value == "1")
    return _shown(request)


@require_POST
@guard
def clear(request):
    """Clear: no award on the award kiosk."""
    presentation.clear(_event(request))
    return _shown(request)


@require_POST
@guard
def calc_rule(request):
    """The pack standings source (`award.calc-rule`). The speed awards
    change with it, so the page loads again, as PHP's does."""
    try:
        set_pack_standings(_event(request), request.POST.get("full-field-calc", ""))
    except PackStandingsRefused as failure:
        return refused(request, failure)
    response = HttpResponse("")
    response["HX-Refresh"] = "true"
    return response
