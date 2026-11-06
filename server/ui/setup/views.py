"""The set-up page (#48): `setup.php` as a first-run wizard, the setup
health checks, the data section (purges, preferences, snapshots), and the
"Make fake roster" sheet that replaces `fakeroster.php`. The wizard's steps
and health checks replace `setup.php`'s status list (D80).

The event step creates or opens an event through `core.setup_event`, as
`setup.nodata` does; on a first run, with no role logged in, it then logs in
as the post-setup role, as `setup.nodata` does. The list, copy, archive and
export screens of #9 are #212. Every data
change goes through `transfer.backups`, which writes a snapshot first (D78).
"""

from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import log_in, role
from core import database_scripts, setup_event
from roster.services import preferences
from roster.services.racers import FakeRosterRefused, make_fake_roster
from transfer import backups, snapshot
from ui.navigation import requires_page
from ui.responses import (
    active_event,
    active_event_or_none,
    posted_int,
    service_error,
    toast_html,
)
from ui.setup import state

guard = requires_page("setup")
fake_guard = requires_page("fakeroster")


def _page(request, *, error_step="", error="", status=200):
    event = active_event_or_none(request)
    return render(
        request,
        "ui/setup/page.html",
        {
            "wizard": state.build(request, event),
            "error_step": error_step,
            "error": error,
        },
        status=status,
    )


@require_GET
@guard
def page(request):
    return _page(request)


def _to_setup():
    return HttpResponseRedirect(reverse("setup"))


def _after_event(request, event):
    """A first run grants set-up for want of an event, and the new event
    ends that. Log in, without a password, as the post-setup role, as
    `setup.nodata` does; a role already logged in stays."""
    request._ui_event = event
    if role(request) is None and (found := setup_event.post_setup_role()):
        log_in(request.session, found)


@require_POST
@guard
def event_new(request):
    try:
        event = setup_event.start_event(request.POST.get("name", "").strip())
    except setup_event.SetupFailure as failure:
        return _page(request, error_step="event", error=failure.description, status=422)
    _after_event(request, event)
    return _to_setup()


@require_POST
@guard
def event_open(request):
    try:
        event = setup_event.open_existing(request.POST.get("name", ""))
    except setup_event.SetupFailure as failure:
        return _page(request, error_step="event", error=failure.description, status=422)
    _after_event(request, event)
    return _to_setup()


@require_POST
@guard
def migrate(request):
    """`setup.php`'s initialize and update-schema buttons, as migrations."""
    database_scripts.migrate()
    return _to_setup()


@require_POST
@guard
def login_post_setup(request):
    """The wizard's last step for a session with set-up and no role."""
    found = setup_event.post_setup_role()
    if found is not None and role(request) is None:
        log_in(request.session, found)
    return _to_setup()


# ----- the data section ------------------------------------------------------------


def _snapshot_url(path) -> str:
    return reverse("setup-snapshot", args=[path.name])


def _data_html(request, event, *, oob=True) -> str:
    html = render_to_string(
        "ui/setup/data.html", {"wizard": state.build(request, event)}, request
    )
    return f'<div id="setup-data" hx-swap-oob="innerHTML">{html}</div>' if oob else html


@require_http_methods(["GET", "POST"])
@guard
def purge(request):
    """GET: a purge's confirmation for the page's sheet, with the count it
    deletes. POST: the purge, or Re-Initialize, after an automatic snapshot
    (D78)."""
    event = active_event(request)
    if request.method == "GET":
        wizard = state.build(request, event)
        what = request.GET.get("what", "")
        found = next((p for p in wizard.purges if p.what == what), None)
        if found is None and what != backups.REINITIALIZE:
            raise Http404("No such purge.")
        return render(
            request,
            "ui/setup/purge_confirm.html",
            {"purge": found, "wizard": wizard, "what": what},
        )
    what = request.POST.get("what", "")
    try:
        taken = backups.purge(event, what)
    except backups.BackupFailed as failure:
        return service_error(request, failure.description)
    done = "Re-initialized the event" if what == backups.REINITIALIZE else "Deleted"
    message = f"{done}. A snapshot from before is saved as {taken.name}."
    toast = toast_html(request, message, oob=True)
    link = render_to_string(
        "ui/setup/snapshot_link.html", {"url": _snapshot_url(taken), "name": taken.name}
    )
    return HttpResponse(_data_html(request, event) + toast + link)


@require_GET
@guard
def snapshot_file(request, name: str):
    """An automatic snapshot, by its file name. A snapshot import replaces
    the open event, so the one taken before it is another event's."""
    found = backups.find_snapshot(name)
    if found is None:
        raise Http404("No such snapshot.")
    return FileResponse(
        open(found, "rb"),
        as_attachment=True,
        filename=found.name,
        content_type="text/xml",
    )


@require_POST
@guard
def preferences_upload(request):
    """`preferences.upload`, answered as the lines applied and the error."""
    event = active_event(request)
    upload = request.FILES.get("prefs")
    if upload is None:
        return service_error(request, "Pick a preferences file.")
    content = upload.read().decode("utf-8", errors="replace")
    report = preferences.apply_report(event, content)
    return render(request, "ui/setup/preferences_report.html", {"report": report})


@require_POST
@guard
def snapshot_import(request):
    """`snapshot.put` through the validated import (#36), after a snapshot
    of the event it replaces (D78)."""
    event = active_event(request)
    upload = request.FILES.get("snapshot")
    if upload is None:
        return service_error(request, "Pick a snapshot file.")
    try:
        imported = backups.import_snapshot(event, upload.read())
    except snapshot.PutFailure as failure:
        return service_error(
            request, f"The snapshot was not imported: {failure.description}"
        )
    except backups.BackupFailed as failure:
        return service_error(request, failure.description)
    return render(
        request,
        "ui/setup/import_report.html",
        {"imported": imported, "url": _snapshot_url(imported.snapshot)},
    )


# ----- the fake roster sheet -------------------------------------------------------


@require_http_methods(["GET", "POST"])
@fake_guard
def fake_roster(request):
    """`fakeroster.php`: a page of its own for the 301, and the sheet the
    set-up page opens. A POST makes the racers through `racer.fake`'s
    service, then goes to check-in, as `fakeroster.php:32` does."""
    wizard_fake = state.FAKE
    if request.method == "GET":
        template = (
            "ui/setup/fake_roster_form.html"
            if request.headers.get("HX-Request") == "true"
            else "ui/setup/fake_roster.html"
        )
        return render(request, template, {"fake": wizard_fake})
    groups = posted_int(request.POST.get("ngroups", ""))
    per_group = posted_int(request.POST.get("racers_per_group", ""))
    if groups is None or per_group is None:
        return service_error(request, "Type a whole number in each field.")
    try:
        make_fake_roster(
            active_event(request),
            groups,
            per_group,
            request.POST.get("check_in") == "1",
        )
    except FakeRosterRefused as refused:
        return service_error(request, str(refused))
    target = reverse("checkin")
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse()
        response["HX-Redirect"] = target
        return response
    return HttpResponseRedirect(target)
