"""Responses for htmx requests (#39 htmx patterns)."""

from django.http import HttpResponse
from django.template.loader import render_to_string


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
