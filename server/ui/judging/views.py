"""Judging (#45): `judging.php` and `judging.js`, over the award services of
#33. Judges assign an award by tapping, with no drag: a racer, then one of
the awards on the racer's sheet, or an award, then one of the racers on the
award's sheet.

The award list and the racer grid poll every 30 s and answer 204 while their
signature holds (#39), so several judges stay in sync. Every POST answers
with a toast and an `HX-Trigger: judging-refresh` header, which makes the
board fetch itself again at once; a control inside the sheet gets a body
that closes the sheet. Each view parses the posted text and makes one
service call, which checks the rules under the `RaceState` lock. No GET
writes anything.
"""

import json
from urllib.parse import urlencode

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from awards import editing
from awards.editing import AwardFailure
from ui.judging import state
from ui.judging.state import Filters
from ui.navigation import requires_page
from ui.responses import active_event as _event
from ui.responses import in_sheet, posted_int, refused, service_error, toast_html

guard = requires_page("judging")

SHEET_BODY = "judging-sheet-body"


def _reply(request, message: str, *, level="ok", undo_url="") -> HttpResponse:
    """A toast, and a body that closes the sheet for a control in it; the
    board fetches itself again."""
    html = toast_html(
        request, message, level, undo_url, oob=in_sheet(request, SHEET_BODY)
    )
    if in_sheet(request, SHEET_BODY):
        html = render_to_string("ui/components/sheet_closed.html", {}, request) + html
    response = HttpResponse(html)
    response["HX-Trigger"] = json.dumps({"judging-refresh": True})
    return response


# ----- the page and its poll -----


def _context(request, board) -> dict:
    return {
        "board": board,
        "orders": state.ORDERS,
        "poll_seconds": state.POLL_SECONDS,
    }


@require_GET
def page(request):
    """`?order=` is `judging.php`'s, so its links keep working."""
    board = state.build(_event(request), Filters.from_request(request.GET))
    return render(
        request,
        "ui/judging/page.html",
        {**_context(request, board), "page_title": "Judging"},
    )


@require_GET
@guard
def board(request):
    """The 30 s poll and the filter bar's fetch. 204 while the signature the
    page sent still matches."""
    found = state.build(_event(request), Filters.from_request(request.GET))
    if request.GET.get("sig") == found.signature:
        return HttpResponse(status=204)
    return render(request, "ui/judging/board.html", _context(request, found))


# ----- the sheets -----


@require_GET
@guard
def racer(request, racer_id):
    """A racer's sheet: each judged award, and why the racer cannot win the
    ones they cannot; and the ad hoc award field."""
    sheet = state.racer_sheet(_event(request), racer_id)
    if sheet is None:
        raise Http404
    return render(request, "ui/judging/sheets/racer.html", {"sheet": sheet})


@require_GET
@guard
def award(request, award_id):
    """An award's sheet: its winner, and the racers who can win it."""
    order = Filters.from_request(request.GET).order
    sheet = state.award_sheet(_event(request), award_id, order)
    if sheet is None:
        raise Http404
    return render(request, "ui/judging/sheets/award.html", {"sheet": sheet})


# ----- assign, remove, Undo, ad hoc -----


@require_POST
@guard
def assign(request, award_id):
    """Give the award to the posted racer (`award.winner`). The service
    refuses an award with another winner and a racer who cannot win it."""
    racer_id = posted_int(request.POST.get("racer", ""), positive=True)
    if racer_id is None:
        return service_error(request, "Pick a racer.")
    try:
        found = editing.assign_award(_event(request), award_id, racer_id)
    except AwardFailure as failure:
        return refused(request, failure)
    return _reply(
        request,
        f"{found.name} goes to {found.racer.first_name} {found.racer.last_name}.",
    )


@require_POST
@guard
def remove(request, award_id):
    """Take the award from its winner (`award.winner racerid=0`), with
    Undo."""
    try:
        found, racer = editing.remove_winner(_event(request), award_id)
    except AwardFailure as failure:
        return refused(request, failure)
    if racer is None:
        return _reply(request, f"{found.name} has no winner.", level="info")
    query = urlencode({"racer": racer.pk})
    return _reply(
        request,
        f"{found.name} taken from {racer.first_name} {racer.last_name}.",
        level="info",
        undo_url=f"{reverse('judging-restore', args=[found.pk])}?{query}",
    )


@require_POST
@guard
def restore(request, award_id):
    """Undo a removal: the award goes back to the racer, checked as a new
    assignment is, so a second Undo changes nothing."""
    racer_id = posted_int(request.GET.get("racer", ""), positive=True)
    if racer_id is None:
        return service_error(request, "That award cannot go back. Assign it again.")
    try:
        found = editing.assign_award(_event(request), award_id, racer_id)
    except AwardFailure as failure:
        return refused(request, failure)
    return _reply(
        request,
        f"{found.name} is back with {found.racer.first_name} {found.racer.last_name}.",
    )


@require_POST
@guard
def ad_hoc(request, racer_id):
    """The racer's ad hoc award (`award.adhoc`): a name gives it, an empty
    name takes it away."""
    name = request.POST.get("awardname", "")
    try:
        racer = editing.set_ad_hoc_award_by_id(_event(request), racer_id, name)
    except AwardFailure as failure:
        return refused(request, failure)
    who = f"{racer.first_name} {racer.last_name}"
    if name.strip():
        return _reply(request, f"{who} gets {name.strip()}.")
    return _reply(request, f"{who} has no ad hoc award now.", level="info")
