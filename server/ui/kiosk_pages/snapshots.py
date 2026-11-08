"""A kiosk page drawn as a thumbnail (#237): the page's own template, with
its parameters and the event's data, rendered on the server with no script.

The kiosk dashboard, its page picker and the scene editor put the HTML in a
scaled `<iframe srcdoc>` (`ui/kiosks/thumbnail.html`). No script runs there
(`ui/tests/test_kiosks.py::test_each_card_draws_its_kiosks_page_in_a_frame_that_runs_no_script`),
so a thumbnail sends no request but its stylesheets and images
(`ui/tests/browser/test_kiosk_thumbnails.py::test_a_thumbnail_sends_no_request_of_its_own`)
and leaves the award being presented as it is
(`test_opening_the_scene_editor_does_not_change_the_award_on_the_kiosk`),
where `scenes.php`'s live previews ran the award kiosk's script.

What a page's script would fetch is filled here from the same data (D84):
the now-racing layouts show the current heat's lineup from
`racing.services.polls.current_racers()`, which the kiosk's first poll
answers, and the award page shows the award `query=award.current` gives
(`ui.presenter.state.current()`), with its recipient once revealed. The
on-deck chart and the QR code are drawn by script only, so their
thumbnails keep the page's frame and say the chart or code shows on the
kiosk (D84). The dashboard's poll signs the board as rendered, thumbnails
included, so a thumbnail follows the data within one poll and the poll
still answers 204 while nothing changed.
"""

from django.template.loader import render_to_string

from kiosks import pages
from racing.services import polls
from ui.kiosk_pages.state import once
from ui.kiosk_pages.views import page_context
from ui.presenter import state as presenter
from ui.responses import active_event

# The thumbnail lays the page out at this size, a TV's 16:9, and scales it.
WIDTH = 1280
HEIGHT = 720

AWARDS = "kiosks/award-presentations.kiosk"


def _lineup(event) -> dict[int, dict]:
    """The current heat's racers by lane (D84)."""
    run = polls.running(event)
    return {r["lane"]: r for r in polls.current_racers(run, run.settings.name_style)}


def render(request, page: str, params: str, *, address=None, name: str = "") -> str:
    """The HTML of the kiosk page `page` (a page string; one with no
    registry entry draws identify, as a kiosk shows it, D10) with the
    parameters text `params`, for the kiosk at `address` named `name`."""
    found = pages.page_for(page)
    event = active_event(request)
    context = page_context(
        request,
        event,
        found,
        params or pages.NO_PARAMS,
        address=address,
        name=name,
        thumbnail=True,
    )
    if found.racing and "lanes" in context:
        lineup = once(request, "lineup", lambda: _lineup(event))
        context["lanes"] = [
            {**lane, "racer": lineup.get(lane["lane"])} for lane in context["lanes"]
        ]
    if found.key == AWARDS:
        context["award"] = once(request, "award", lambda: presenter.current(event))
    return render_to_string(found.template, context, request)
