"""A kiosk page drawn as a thumbnail (#237): the page's own template, with
its parameters and the event's data, rendered on the server with no script.

The kiosk dashboard, its page picker and the scene editor put the HTML in a
scaled `<iframe srcdoc>` (`ui/kiosks/thumbnail.html`). No script runs there,
so a thumbnail sends no poll, registers no kiosk and clears no award, which
`scenes.php`'s live previews did. It shows the page as the server renders
it, before the page's script fills in its content poll: a now-racing page
shows its lanes and no lineup. The dashboard's poll signs the board as
rendered, thumbnails included, so a thumbnail follows the data within one
poll and the poll still answers 204 while nothing changed.
"""

from django.template.loader import render_to_string

from kiosks import pages
from ui.kiosk_pages.views import page_context
from ui.responses import active_event

# The thumbnail lays the page out at this size, a TV's 16:9, and scales it.
WIDTH = 1280
HEIGHT = 720


def render(request, page: str, params: str, *, address=None, name: str = "") -> str:
    """The HTML of the kiosk page `page` (a page string; one with no
    registry entry draws identify, as a kiosk shows it, D10) with the
    parameters text `params`, for the kiosk at `address` named `name`."""
    found = pages.page_for(page)
    context = page_context(
        request,
        active_event(request),
        found,
        params or pages.NO_PARAMS,
        address=address,
        name=name,
        thumbnail=True,
    )
    return render_to_string(found.template, context, request)
