"""The about and diagnostics page (#48): `about.php` without `phpinfo()`
(S30). Anyone sees the version, the build date, the time zone, the schema
state and the timer. The database location, the setup health checks, the
`database.check` results and the downloads need SET_UP, as `about.php` shows
the database configuration only with SET_UP and S26 keeps database.check
from anonymous visitors. The local URLs, user agent and JavaScript
version of `about.php` are left out (D80)."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.permissions import Perm, allows
from core import diagnostics
from core.invariants import check_invariants
from ui.navigation import requires_page
from ui.responses import active_event_or_none
from ui.version import build_version, timer_version

guard = requires_page("about")


@require_GET
@guard
def page(request):
    event = active_event_or_none(request)
    set_up = allows(request, Perm.SET_UP)
    context = {
        "version": build_version(),
        "build_date": timer_version()["date"],
        "tz": diagnostics.time_zone(),
        "pending": diagnostics.pending_migrations(),
        "event": event,
        "timer": diagnostics.timer(event) if event else None,
        "set_up": set_up,
    }
    if set_up:
        context |= {
            "database": diagnostics.database_location(),
            "checks": diagnostics.health_checks(event),
            "violations": check_invariants(event) if event else {},
        }
    return render(request, "ui/about/page.html", context)
