"""The scene editor (#46): `scenes.php` and `scenes.js`, over the scene
services of #34.

Pick a scene, and every kiosk name the scenes use shows as a card with a
thumbnail of the page that scene gives it, a page picker, and a Configure
sheet. New scene, Delete scene and Add kiosk name are sheets. A thumbnail is
a picture of the page type, never the page itself: `scenes.php`'s live
previews loaded the award kiosk, which cleared the award being presented.
So opening the editor changes nothing, and no GET writes anything.

Each change is one service call under the `RaceState` lock, and answers with
the scene's cards again, out of band. A new or deleted scene loads the page
again, at the scene to show next.
"""

from urllib.parse import urlencode

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from kiosks import parameters, services
from kiosks.models import SceneKiosk
from kiosks.services import KioskFailure
from ui.kiosks import state
from ui.kiosks.views import posted_parameters, qr_default
from ui.navigation import requires_page
from ui.responses import active_event as _event
from ui.responses import banner_back, closing_sheet, posted_int, refused

guard = requires_page("scenes")
SHEET_BODY = "scenes-sheet-body"


def _data(request):
    return request.POST if request.method == "POST" else request.GET


def _scene_id(request) -> int | None:
    return posted_int(_data(request).get("scene", ""), positive=True)


def _page_url(scene_id: int | None, back: str) -> str:
    query = {k: v for k, v in (("scene", scene_id), ("back", back)) if v}
    url = reverse("scenes")
    return f"{url}?{urlencode(query)}" if query else url


def _entries_html(request, scene_id) -> str:
    found = state.editor(_event(request), scene_id)
    return render_to_string("ui/scenes/entries.html", {"editor": found}, request)


def changed(request, scene_id: int, message: str) -> HttpResponse:
    """A change's reply: the sheet closes, a toast says what changed, and
    the scene's cards come again, out of band."""
    reply = closing_sheet(request, SHEET_BODY, message)
    entries = _entries_html(request, scene_id)
    return HttpResponse(
        reply + f'<div id="scene-entries" hx-swap-oob="innerHTML">{entries}</div>'
    )


def _reload_at(scene_id: int | None, back: str) -> HttpResponse:
    response = HttpResponse("")
    response["HX-Redirect"] = _page_url(scene_id, back)
    return response


def _sheet(request, template: str, context: dict) -> HttpResponse:
    return render(
        request,
        f"ui/scenes/sheets/{template}",
        {"sheet_body": SHEET_BODY, "pages": state.pages(), **context},
    )


@require_GET
def page(request):
    event = _event(request)
    editor = state.editor(event, _scene_id(request))
    back = request.GET.get("back", "")
    return render(
        request,
        "ui/scenes/page.html",
        {
            "editor": editor,
            "back": back,
            "back_url": banner_back(request, "kiosk-dashboard.php"),
            "scene_links": [
                (pk, name, _page_url(pk, back)) for pk, name in editor.scenes
            ],
            "page_title": "Scenes",
        },
    )


@require_http_methods(["GET", "POST"])
@guard
def new(request):
    """New scene (`scene.add`). The page loads again at the new scene."""
    back = _data(request).get("back", "")
    if request.method == "GET":
        return _sheet(request, "new.html", {"back": back, "name": ""})
    name = request.POST.get("name", "")
    try:
        scene = services.create_scene(_event(request), name)
    except KioskFailure as failure:
        return _sheet(
            request,
            "new.html",
            {"back": back, "name": name, "error": failure.description},
        )
    return _reload_at(scene.pk, back)


@require_http_methods(["GET", "POST"])
@guard
def delete(request):
    """Delete scene (`scene.delete`), confirmed in a sheet in place of a
    dialog. The page loads again at the first scene."""
    scene_id = _scene_id(request)
    back = _data(request).get("back", "")
    if request.method == "GET":
        editor = state.editor(_event(request), scene_id)
        if scene_id is None or editor.scene_id != scene_id:
            raise Http404
        return _sheet(request, "delete.html", {"editor": editor, "back": back})
    if scene_id is None or not services.remove_scene(_event(request), scene_id):
        return refused(request, KioskFailure(services.SCENE_GONE))
    return _reload_at(None, back)


@require_http_methods(["GET", "POST"])
@guard
def add_name(request):
    """Add kiosk name: a name no scene uses yet, and the page this scene
    gives it. Kiosks given that name show the page while the scene is on."""
    scene_id = _scene_id(request)
    if request.method == "GET":
        if scene_id is None:
            raise Http404
        return _sheet(request, "name.html", {"scene_id": scene_id, "values": {}})
    try:
        if scene_id is None:
            raise KioskFailure(services.SCENE_GONE)
        services.set_scene_page(
            _event(request),
            scene_id,
            request.POST.get("name", ""),
            request.POST.get("page", ""),
            new_name=True,
        )
    except KioskFailure as failure:
        return _sheet(
            request,
            "name.html",
            {
                "scene_id": scene_id,
                "values": request.POST,
                "error": failure.description,
            },
        )
    return changed(request, scene_id, "Kiosk name added.")


def _entry(request, scene_id) -> SceneKiosk | None:
    return SceneKiosk.objects.filter(
        event=_event(request), scene_id=scene_id, kiosk_name=_data(request).get("name")
    ).first()


@require_http_methods(["GET", "POST"])
@guard
def page_sheet(request):
    """The page a kiosk name shows in the scene, or none, which takes the
    name out of this scene (`scene.setkiosk`)."""
    scene_id = _scene_id(request)
    name = _data(request).get("name", "")
    if request.method == "GET":
        if scene_id is None or not name:
            raise Http404
        row = _entry(request, scene_id)
        return _sheet(
            request,
            "page.html",
            {"scene_id": scene_id, "name": name, "shown": row.page if row else ""},
        )
    try:
        if scene_id is None:
            raise KioskFailure(services.SCENE_GONE)
        services.set_scene_page(
            _event(request), scene_id, name, request.POST.get("page", "")
        )
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, scene_id, f"{name} page changed.")


@require_http_methods(["GET", "POST"])
@guard
def configure(request):
    """The Configure sheet of the page a kiosk name shows in the scene, with
    its stored parameters."""
    scene_id = _scene_id(request)
    if request.method == "GET":
        row = _entry(request, scene_id) if scene_id else None
        if row is None or row.page not in parameters.CONFIGURABLE:
            raise Http404
        return render(
            request,
            "ui/kiosks/sheets/configure.html",
            {
                "label": f"{row.kiosk_name} in {row.scene.name}",
                "form": state.configure(
                    _event(request), row.page, row.params, qr_default(request)
                ),
                "hidden": {
                    "scene": scene_id,
                    "name": row.kiosk_name,
                    "page": row.page,
                },
                "post_url": request.path,
                "sheet_body": SHEET_BODY,
                "sheet_js": "sceneSheet",
                "sync": "this:drop",
            },
        )
    name = request.POST.get("name", "")
    try:
        if scene_id is None:
            raise KioskFailure(services.SCENE_GONE)
        services.configure_scene_kiosk(
            _event(request),
            scene_id,
            name,
            request.POST.get("page", ""),
            posted_parameters(request.POST),
        )
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, scene_id, f"{name} configured.")
