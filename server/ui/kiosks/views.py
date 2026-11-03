"""The kiosk dashboard (#46): `kiosk-dashboard.php` and
`kiosk-dashboard.js`, over the kiosk services of #34.

A sticky scene switcher changes every named kiosk in one tap, and a card per
kiosk names it, picks its page, configures the page, reloads it or makes it
identify itself. The board polls every 2 s and answers 204 while what it
would render is what the page holds; it waits while a sheet is open
(`derbynet.pollable`). Each change is one service call under the
`RaceState` lock, and answers with the board again, out of band.

The page needs PRESENT_AWARDS, its page gate, but every change needs
CONTROL_RACE, as `kiosk.assign`, `kiosk.reload` and `scene.apply` do: a role
without it sees the board with no controls (C37). No GET writes anything.
"""

import hashlib

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import get_script_prefix
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import Perm, allows
from accounts.views import page_requires
from kiosks import parameters, services
from kiosks.madlib import madlib
from kiosks.services import KioskFailure
from ui.kiosks import state
from ui.navigation import requires_page
from ui.responses import active_event as _event
from ui.responses import (
    closing_sheet,
    in_sheet,
    posted_int,
    refused,
    service_error,
    toast_html,
)

guard = requires_page("kiosk-dashboard")
SHEET_BODY = "kiosks-sheet-body"


# A change needs CONTROL_RACE, as the legacy endpoints it stands for.
_controls = page_requires(Perm.CONTROL_RACE)


def can_control(request) -> bool:
    return allows(request, Perm.CONTROL_RACE)


def qr_default(request) -> str:
    """The QR code page's default text, `g_url + "/vote.php"`."""
    return request.build_absolute_uri(get_script_prefix() + "vote.php")


def posted_parameters(post) -> parameters.Posted:
    """A Configure form's fields, parsed only: the service checks them."""
    return parameters.Posted(
        title=post.get("title", ""),
        subdir=post.get("subdir", ""),
        classids=tuple(post.getlist("classids")),
        key=post.get("key", ""),
        confetti=post.get("confetti") == "1",
        content=post.get("content", ""),
    )


# ----- the board -------------------------------------------------------------------


def board_html(request) -> tuple[str, str]:
    """The board and its signature: a hash of the board as rendered, so the
    poll's 204 covers everything it shows (#45 retro)."""
    found = state.board(_event(request), timezone.now())
    html = render_to_string(
        "ui/kiosks/board.html",
        {"board": found, "can_control": can_control(request)},
        request,
    )
    sig = hashlib.sha256(html.encode()).hexdigest()[:20]
    return f'<input type="hidden" id="kiosks-sig" name="sig" value="{sig}">{html}', sig


def changed(request, message: str) -> HttpResponse:
    """A change's reply: a body that closes the sheet for a form in it, or a
    toast; and the board again, out of band."""
    if in_sheet(request, SHEET_BODY):
        reply = closing_sheet(request, SHEET_BODY, message)
    else:
        reply = toast_html(request, message)
    html, _ = board_html(request)
    return HttpResponse(
        reply + f'<div id="kiosk-board" hx-swap-oob="innerHTML">{html}</div>'
    )


@require_GET
def page(request):
    html, _ = board_html(request)
    return render(
        request,
        "ui/kiosks/page.html",
        {
            "board_html": html,
            "server_now": int(timezone.now().timestamp()),
            "poll_seconds": state.POLL_SECONDS,
            "quiet_seconds": state.QUIET_SECONDS,
            "can_control": can_control(request),
            "can_edit_scenes": allows(request, Perm.SET_UP),
            "kiosk_url": get_script_prefix() + "kiosk.php",
            "page_title": "Kiosk Dashboard",
        },
    )


@require_GET
@guard
def board(request):
    """The 2 s poll. 204 while the board is what the page sent the
    signature of."""
    html, sig = board_html(request)
    if request.GET.get("sig") == sig:
        return HttpResponse(status=204)
    return HttpResponse(html)


# ----- the scene switcher ----------------------------------------------------------


@require_POST
@guard
@_controls
def scene(request):
    """Tap a scene: every named kiosk shows that scene's page for its name."""
    scene_id = posted_int(request.POST.get("scene", ""))
    if scene_id is None:
        return service_error(request, "Pick a scene.")
    try:
        shown = services.show_scene(_event(request), scene_id)
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, f"Kiosks show the {shown.name} scene.")


# ----- a kiosk's sheets and buttons ------------------------------------------------


def _address(request) -> str:
    data = request.POST if request.method == "POST" else request.GET
    return data.get("address", "")


def _listed(request):
    found = state.kiosk(_event(request), _address(request), timezone.now())
    if found is None:
        raise Http404
    return found


def _label(kiosk) -> str:
    return kiosk.name or madlib(kiosk.address)


@require_http_methods(["GET", "POST"])
@guard
@_controls
def page_sheet(request):
    """The page picker: tap a page and the kiosk shows it, with no
    parameters, as `kiosk.assign` with `page` does."""
    if request.method == "GET":
        kiosk = _listed(request)
        return render(
            request,
            "ui/kiosks/sheets/page.html",
            {
                "kiosk": kiosk,
                "label": _label(kiosk),
                "pages": state.pages(),
                "shown": state.Shown.of(kiosk.page),
            },
        )
    try:
        services.set_kiosk_page(
            _event(request), _address(request), request.POST.get("page", "")
        )
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, "Kiosk page changed.")


@require_http_methods(["GET", "POST"])
@guard
@_controls
def name_sheet(request):
    """Name a kiosk, with chips for the names the scenes use. A named kiosk
    takes the current scene's page for its name."""
    if request.method == "GET":
        kiosk = _listed(request)
        return render(
            request,
            "ui/kiosks/sheets/name.html",
            {
                "kiosk": kiosk,
                "label": _label(kiosk),
                "names": services.scene_kiosk_names(_event(request)),
            },
        )
    try:
        services.name_kiosk(
            _event(request), _address(request), request.POST.get("name", "")
        )
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, "Kiosk named.")


@require_http_methods(["GET", "POST"])
@guard
@_controls
def configure(request):
    """The Configure sheet of the kiosk's page, showing its stored
    parameters. `page` names the page the sheet was for."""
    if request.method == "GET":
        kiosk = _listed(request)
        if kiosk.page not in parameters.CONFIGURABLE:
            raise Http404
        return render(
            request,
            "ui/kiosks/sheets/configure.html",
            {
                "label": _label(kiosk),
                "form": state.configure(
                    _event(request), kiosk.page, kiosk.params, qr_default(request)
                ),
                "hidden": {"address": kiosk.address, "page": kiosk.page},
                "post_url": request.path,
                "sheet_body": SHEET_BODY,
                "sheet_js": "kioskSheet",
                "sync": "#kiosk-board:queue all",
            },
        )
    try:
        services.configure_kiosk(
            _event(request),
            _address(request),
            request.POST.get("page", ""),
            posted_parameters(request.POST),
        )
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, "Kiosk configured.")


@require_POST
@guard
@_controls
def reload(request):
    """Reload: the kiosk reloads its page at its next poll."""
    try:
        services.reload_kiosk(_event(request), _address(request))
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, "The kiosk reloads at its next check-in.")


@require_POST
@guard
@_controls
def identify(request):
    """Identify: the kiosk shows the identify page, its name and address in
    large type, so the crew can tell which TV it is."""
    try:
        services.identify_kiosk(_event(request), _address(request))
    except KioskFailure as failure:
        return refused(request, failure)
    return changed(request, "The kiosk shows who it is.")
