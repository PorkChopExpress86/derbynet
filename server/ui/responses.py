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


def racer_name(first: str, last: str) -> str:
    """A racer's name as the manager pages show it."""
    return f"{first} {last}"


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
