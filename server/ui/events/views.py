"""The events page (#212): every event with its date, state and racer
count; a new event, copied in part from another; switching the active
event; archiving; Reopen for editing; and the export download. The rules
are `core.events`'s (#9, Past events and Copy from), and the zip is
`transfer.export`'s.

PHP has no such page (D14, D81). `setup.php` creates and opens event
folders only (`action.setup.nodata.inc`), and the set-up page keeps that
step (#48).
"""

import tempfile
from pathlib import Path

from django.conf import settings as django_settings
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from core import events
from core.models import Event
from transfer.export import export_event, file_name
from ui.navigation import requires_page
from ui.responses import posted_int, service_error, toast_html

guard = requires_page("events")


def _context(**extra) -> dict:
    listed = events.listing()
    return {
        "listed": listed,
        "parts": events.Part.choices,
        "max_name": events.MAX_NAME_LENGTH,
        # The hosted lock hides creating, switching, archiving and reopening;
        # export stays (D81; #9, Hosted lock).
        "locked": django_settings.LOCK_SETTINGS,
        **extra,
    }


@require_GET
@guard
def page(request):
    return render(request, "ui/events/page.html", _context())


def _changed(request, change, message):
    """Run one lifecycle service and answer the page's events section with a
    toast of `message(result)`, or the service's refusal as an error toast."""
    try:
        result = change()
    except events.EventRefused as refused:
        return service_error(request, refused.description)
    response = render(request, "ui/events/section.html", _context())
    response.write(toast_html(request, message(result), level="ok", oob=True))
    return response


def _created(event) -> str:
    if event.copied_from is not None:
        return f"Created {event.name}, copied from {event.copied_from.name}."
    return f"Created {event.name}."


def _switched(switched) -> str:
    message = f"{switched.active.name} is the active event."
    if switched.replaced is not None:
        message += f" {switched.replaced.name} is now read-only."
    if switched.active.read_only:
        message += " It is read-only until you reopen it for editing."
    return message


@require_POST
@guard
def create(request):
    source = request.POST.get("copy_from", "")
    copy_from = posted_int(source, positive=True) if source else None
    if source and copy_from is None:
        return service_error(request, "Choose an event to copy from.")
    return _changed(
        request,
        lambda: events.create_event(
            request.POST.get("name", ""),
            copy_from=copy_from,
            parts=[p for p in request.POST.getlist("part") if p in events.Part.values],
        ),
        _created,
    )


@require_POST
@guard
def switch(request, pk: int):
    return _changed(request, lambda: events.switch_to(pk), _switched)


@require_POST
@guard
def archive(request, pk: int):
    return _changed(
        request,
        lambda: events.archive(pk),
        lambda event: f"Archived {event.name}. It is read-only.",
    )


@require_POST
@guard
def reopen(request, pk: int):
    return _changed(
        request,
        lambda: events.reopen(pk),
        lambda event: f"{event.name} is open for editing.",
    )


@require_GET
@guard
def export(request, pk: int):
    """The event's zip, as `manage.py export_event` writes it."""
    event = Event.objects.filter(pk=pk).first()
    if event is None:
        raise Http404("No such event.")
    with tempfile.TemporaryDirectory(prefix="derbynet-download-") as work:
        target = export_event(event, Path(work) / file_name(event))
        # Open, then let the folder go: the open file stays readable until
        # FileResponse closes it, and nothing is left on disk.
        stream = open(target, "rb")  # noqa: SIM115
    return FileResponse(
        stream,
        as_attachment=True,
        filename=file_name(event),
        content_type="application/zip",
    )
