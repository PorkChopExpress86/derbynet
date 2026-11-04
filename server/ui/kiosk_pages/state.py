"""What each kiosk page renders (#52), from the registry entry, the page's
typed parameters and the event.

The pages that run a content poll read it in the browser from the endpoint
their PHP page polls (`now-racing` reads `query=poll`); the rest render here
and, where PHP reloaded, refresh a fragment from this server.
"""

from core.models import RaceSettings
from kiosks import displays, pages
from kiosks.madlib import madlib
from ui.standings import state as standings_state


def _identify(request, event, settings, params, address):
    shown = address or "page-param"
    return {"madlib": madlib(shown), "shown_address": shown}


def _scale(request, event, settings, params, address):
    return {"track": displays.scaled_track(settings)}


def _check_in(request, event, settings, params, address):
    return {"waiting": displays.to_check_in(event, params["classids"])}


def _standings(request, event, settings, params, address):
    return {"standings": standings_state.build(event, params["key"])}


def _lanes(request, event, settings, params, address):
    colors = displays.lane_colors(settings)
    return {
        "lanes": [
            {"lane": n, "color": color} for n, color in enumerate(colors, start=1)
        ],
        "use_points": settings.use_points,
        "linger_ms": settings.now_racing_linger_ms,
    }


def _columnar(reversed_order: bool):
    def build(request, event, settings, params, address):
        found = _lanes(request, event, settings, params, address)
        if reversed_order:
            found["lanes"] = found["lanes"][::-1]
        return found

    return build


def _ondeck(request, event, settings, params, address):
    found = _lanes(request, event, settings, params, address)
    found["show_car_photos"] = settings.show_cars_on_deck
    return found


def _results(request, event, settings, params, address):
    from ui.results.views import rounds_html

    html, _ = rounds_html(request)
    return {"rounds_html": html}


BUILDERS = {
    "identify": _identify,
    "scale-MPH": _scale,
    "please-check-in": _check_in,
    "standings": _standings,
    "now-racing": _lanes,
    "now-racing-columnar": _columnar(False),
    "now-racing-columnar-reversed": _columnar(True),
    "ondeck": _ondeck,
    "results-by-racer": _results,
}


def context(request, event, page: pages.KioskPage, params_text: str, address):
    settings = RaceSettings.objects.get(event=event)
    params = displays.read_parameters(page, params_text)
    build = BUILDERS.get(page.name)
    found = build(request, event, settings, params, address) if build else {}
    return {"params": params, "settings": settings, **found}
