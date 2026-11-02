from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from accounts.permissions import first_run
from ui.responses import service_error, toast


@require_GET
def home(request):
    """The home menu, as `index.php`: the role's entries grouped by section,
    in two columns on a desktop for a role with SET_UP. A new install with no
    event goes to set-up first."""
    if first_run():
        return redirect("setup")
    return render(request, "ui/home.html")


@require_GET
def placeholder(request):
    """A registry page whose ticket has not landed yet."""
    return render(request, "ui/placeholder.html")


@require_GET
def quickref(request):
    """The quick reference the help link opens. The docs build writes it
    (`docs/build.xml`, target `quickref`)."""
    try:
        pdf = open(settings.QUICKREF_PDF, "rb")  # FileResponse closes it.
    except FileNotFoundError:
        raise Http404("The quick reference has not been built.") from None
    return FileResponse(pdf, content_type="application/pdf")


def _components_enabled():
    if not (settings.DEBUG or settings.ENABLE_TEST_ENDPOINTS):
        raise Http404


@require_GET
def components(request):
    """Every #39 component, for developers and the browser tests. Served only
    with DERBYNET_DEBUG or DERBYNET_ENABLE_TEST_ENDPOINTS."""
    _components_enabled()
    context = {
        "racers": [
            {"id": 1, "name": "Ava Verify", "car": "101", "passed": True},
            {"id": 2, "name": "Ben Verify", "car": "102", "passed": False},
            {"id": 3, "name": "Cal Verify", "car": "201", "passed": False},
        ],
        "orders": [("name", "Name"), ("car", "Car number"), ("den", "Den")],
        "poll_seconds": 2,
        "page_title": "Components",
    }
    return render(request, "ui/components_gallery.html", context)


@require_http_methods(["POST"])
def components_demo(request, outcome):
    """The gallery's htmx endpoints: `ok` answers 204, `toast` a toast with
    Undo, `refuse` a service error (422)."""
    _components_enabled()
    match outcome:
        case "ok":
            return HttpResponse(status=204)
        case "toast":
            undo = request.path.replace("toast", "ok")
            return toast(request, "Ben Verify passed.", undo_url=undo)
        case "refuse":
            return service_error(request, "The server refused the change.")
    raise Http404
