"""The standings page (#49): `standings.php` as a ranked list, public (D5).

`?view=` picks a presentation of the standings catalog by its key, such as
`st-ff` or `st-r12`; any other value shows the first. The select fetches the
list for its choice and pushes the URL, so a link keeps the presentation.
"""

from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from ui.navigation import requires_page
from ui.responses import active_event
from ui.standings import state

guard = requires_page("standings")


def _view(request) -> str:
    value = request.GET.get("view", "")
    return value[:40] if isinstance(value, str) else ""


@require_GET
def page(request):
    return render(
        request,
        "ui/standings/page.html",
        {"standings": state.build(active_event(request), _view(request))},
    )


@require_GET
@guard
def listing(request):
    """The list for the select's choice. The browser's address becomes the
    page's URL for it, so reloading or sharing keeps the choice."""
    found = state.build(active_event(request), _view(request))
    response = render(request, "ui/standings/list.html", {"standings": found})
    query = urlencode({"view": found.selected}) if found.selected else ""
    response["HX-Push-Url"] = reverse("standings") + (f"?{query}" if query else "")
    return response
