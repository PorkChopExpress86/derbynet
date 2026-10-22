from django.conf import settings
from django.db import connections

from spike.settings import event_db


def repoint_event_db(get_response):
    """Option (a). One site query per request; reopen `event` when the version moved.

    The version lives on the connection object, so each thread's connection
    notices on its own. Tracking it per process instead (SPIKE_FAULT=process_version)
    passes on sync workers and serves stale events on gthread workers. Every
    `event` connection in a process shares one settings dict, so a thread that
    reconnects mid-request picks up the new NAME.
    """
    from siteapp.models import SiteState

    process_seen = {}

    def middleware(request):
        slug, version = SiteState.objects.values_list("active_slug", "version").get(pk=1)
        conn = connections["event"]
        if settings.FAULT == "process_version":
            # Control: track the version per process, not per connection. Breaks under threads.
            seen = process_seen.get("v")
            process_seen["v"] = version
        else:
            seen = getattr(conn, "spike_version", None)
        stuck = settings.FAULT == "no_repoint" and seen is not None
        if seen != version and not stuck:
            conn.close()
            conn.settings_dict["NAME"] = event_db(slug)["NAME"]
            conn.spike_version = version
        request.spike_version = version
        return get_response(request)

    return middleware


def resolve_active_event(get_response):
    """Option (b). Resolve the active event once per request."""
    from bapp.models import SiteState

    def middleware(request):
        request.event = SiteState.objects.select_related("active_event").get(pk=1).active_event
        request.spike_version = request.event.slug
        return get_response(request)

    return middleware
