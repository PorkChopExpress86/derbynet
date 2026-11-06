"""The events page (#212): every event with its date, state and racer
count; a new event, copied in part from another; switching the active
event; archiving; Reopen for editing; and the export download. The rules
are `core.events`'s (#9, Past events and Copy from), and the zip is
`transfer.export`'s.

PHP has no such page. `setup.php` creates and opens event folders only
(`action.setup.nodata.inc`), and the set-up page keeps that step (#48).
"""

import tempfile
from pathlib import Path

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
        **extra,
    }


@require_GET
@guard
def page(request):
    return render(request, "ui/events/page.html", _context())


def _section(request, message: str):
    """The page's events section after a change, with its toast."""
    response = render(request, "ui/events/section.html", _context())
    response.write(toast_html(request, message, level="ok", oob=True))
    return response


@require_POST
@guard
def create(request):
    source = request.POST.get("copy_from", "")
    copy_from = posted_int(source, positive=True) if source else None
    if source and copy_from is None:
        return service_error(request, "Choose an event to copy from.")
    try:
        event = events.create_event(
            request.POST.get("name", ""),
            copy_from=copy_from,
            parts=[p for p in request.POST.getlist("part") if p in events.Part.values],
        )
    except events.EventRefused as refused:
        return service_error(request, refused.description)
    if event.copied_from is not None:
        message = f"Created {event.name}, copied from {event.copied_from.name}."
    else:
        message = f"Created {event.name}."
    return _section(request, message)


def _event_id(raw) -> int:
    found = posted_int(raw, positive=True)
    if found is None:
        raise Http404("No such event.")
    return found


@require_POST
@guard
def switch(request, pk: int):
    try:
        switched = events.switch_to(_event_id(pk))
    except events.EventRefused as refused:
        return service_error(request, refused.description)
    message = f"{switched.active.name} is the active event."
    if switched.replaced is not None:
        message += f" {switched.replaced.name} is now read-only."
    if switched.active.read_only:
        message += " It is read-only until you reopen it for editing."
    return _section(request, message)


@require_POST
@guard
def archive(request, pk: int):
    try:
        event = events.archive(_event_id(pk))
    except events.EventRefused as refused:
        return service_error(request, refused.description)
    return _section(request, f"Archived {event.name}. It is read-only.")


@require_POST
@guard
def reopen(request, pk: int):
    try:
        event = events.reopen(_event_id(pk))
    except events.EventRefused as refused:
        return service_error(request, refused.description)
    return _section(request, f"{event.name} is open for editing.")


@require_GET
@guard
def export(request, pk: int):
    """The event's zip, as `manage.py export_event` writes it."""
    event = Event.objects.filter(pk=_event_id(pk)).first()
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
