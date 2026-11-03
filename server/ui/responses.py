"""Responses for htmx requests (#39 htmx patterns)."""

from django.http import Http404, HttpResponse
from django.template.loader import render_to_string

from core.models import SiteState


def active_event(request):
    """The open event, once per request. A new install with no event has
    no race to run or check in."""
    found = getattr(request, "_ui_event", None)
    if found is None:
        state = SiteState.objects.select_related("active_event").filter(pk=1).first()
        if state is None or state.active_event is None:
            raise Http404("No event is open.")
        found = request._ui_event = state.active_event
    return found


# The integer columns' range on every database.
INT32 = 2**31


def posted_int(value, *, positive: bool = False) -> int | None:
    """A posted whole number the integer columns hold, or None for text that
    is not one, a number past the columns, or, with `positive`, one below 1."""
    try:
        n = int(str(value).strip())
    except TypeError, ValueError:
        return None
    if not -INT32 < n < INT32 or (positive and n < 1):
        return None
    return n


def car(number) -> str:
    """A car number padded to three digits (D47)."""
    try:
        return f"{int(number):03d}"
    except TypeError, ValueError:
        return str(number or "")


def racer_name(first: str, last: str) -> str:
    """A racer's name as the manager pages show it."""
    return f"{first} {last}"


def round_name(round) -> str:
    """A round as the manager pages name it: "Scouts, Round 2"."""
    return f"{round.group.name}, Round {round.number}"


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def nocase(text: str) -> str:
    """Lower-cases A to Z only, for sorting names as PHP's pages do."""
    return text.translate(_ASCII_LOWER)


def toast_html(
    request, message: str, level: str = "ok", undo_url: str = "", *, oob=False
) -> str:
    """A toast's HTML, to send beside other partials. With `oob`, it goes
    out of band into #toasts."""
    return render_to_string(
        "ui/components/toast.html",
        {"message": message, "level": level, "undo_url": undo_url, "oob": oob},
        request,
    )


def toast(
    request, message: str, *, level: str = "info", undo_url: str = "", status: int = 200
):
    """A toast for the page's toast region. With status 422, the base
    template's htmx config swaps it into the region whatever the request's
    target. A 2xx toast goes where the request swaps, so send it out of band
    (`toast.html` with `oob=True`) next to the partials it changed."""
    body = render_to_string(
        "ui/components/toast.html",
        {"message": message, "level": level, "undo_url": undo_url},
        request,
    )
    return HttpResponse(body, status=status)


def service_error(request, message: str):
    """A service refused the change: an error toast with HTTP 422."""
    return toast(request, message, level="error", status=422)


def refused(request, failure) -> HttpResponse:
    """A service's failure, as an error toast with HTTP 422."""
    return service_error(request, getattr(failure, "description", str(failure)))


# ----- a page's one sheet (#42, #43) -----
#
# A page keeps one sheet whose body, `#<body_id>`, a control loads. A
# control inside the sheet sends its request there, so its reply is the
# sheet's next body; a control on the page sends its request to #toasts, so
# the sheet's body goes out of band.


def in_sheet(request, body_id: str) -> bool:
    """The request came from a control inside the sheet."""
    return request.headers.get("HX-Target") == body_id


def into_sheet(request, body_id: str, html: str) -> str:
    """`html` as the sheet's next body: the reply itself for a control in
    the sheet, out of band for a control on the page."""
    if in_sheet(request, body_id):
        return html
    return f'<div id="{body_id}" hx-swap-oob="innerHTML">{html}</div>'


def closing_sheet(request, body_id: str, message: str) -> str:
    """Close the sheet, and say what happened in a toast."""
    closed = render_to_string("ui/components/sheet_closed.html", {}, request)
    toast = toast_html(request, message, oob=in_sheet(request, body_id))
    return into_sheet(request, body_id, closed) + toast
