"""The kiosk display pages (#52): `kiosk.php`, `fullscreen.php`, and the
standalone `ondeck.php` and `slideshow.php`.

- `kiosk.php` with no `address` and no `page` answers 302 to
  `?address=<this kiosk's address>`, as PHP does. With `address`, it
  registers the kiosk, records the contact, consumes a pending reload
  (`kiosks.services.kiosk_setting`), and renders the page the kiosk is
  given. With `page`, it previews that page with `parameters` and
  registers nothing. A page string with no registry entry, such as
  `../settings.php` or a `local/kiosks/` page, renders the identify page
  (D10); PHP walked `realpath` to keep the file inside `kiosks/`.
- PHP ORs `PRESENT_AWARDS` into every visitor's session here, so the award
  kiosk could clear the current award on load. Django grants nothing, and
  the award kiosk clears nothing (S28).
- Every kiosk view allows same-origin framing, so a page shows inside
  `replay.php` and `fullscreen.php`; the rest of the server refuses framing.
"""

import random

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

from kiosks import pages, services
from kiosks.madlib import madlib
from ui.kiosk_pages import state
from ui.responses import active_event

ADDRESS_KEY = "kiosk_address"


def address_for_current_kiosk(request) -> str:
    """`address_for_current_kiosk()`: the `address` query value, else the
    one this session was given, else the client's address and port, kept in
    the session. WSGI has no client port, so a number from 100 to 200
    stands in, as PHP's does when `REMOTE_PORT` is unset."""
    address = request.GET.get("address", "") or request.session.get(ADDRESS_KEY, "")
    if not address:
        port = request.META.get("REMOTE_PORT") or random.randint(100, 200)  # noqa: S311
        address = f"{request.META.get('REMOTE_ADDR', '')}+{port}"
        request.session[ADDRESS_KEY] = address
    return address


def _render(request, page: pages.KioskPage, params_text: str, **config):
    event = active_event(request)
    found = state.context(request, event, page, params_text, config.get("address"))
    kiosk = {
        "address": None,
        "loaded": page.key,
        "name": "",
        "standalone": False,
        **config,
    }
    return render(
        request,
        page.template,
        {
            "page": page,
            "kiosk": kiosk,
            # What kiosk.js reads: the poll.kiosk address (none in preview
            # and standalone), the page string to compare, the parameters.
            "kiosk_config": {
                "address": kiosk["address"],
                "loaded": kiosk["loaded"],
                "parameters": found["params"],
                "action": reverse("action-php"),
                "home": reverse("home"),
            },
            **found,
        },
    )


@require_GET
@xframe_options_sameorigin
def kiosk(request):
    if "address" not in request.GET and "page" not in request.GET:
        query = urlencode({"address": address_for_current_kiosk(request)})
        return HttpResponseRedirect(f"{request.path}?{query}")
    if "page" in request.GET:
        page = pages.page_for(request.GET["page"])
        return _render(request, page, request.GET.get("parameters", "{}"))
    address = address_for_current_kiosk(request)
    setting = services.kiosk_setting(active_event(request), address, timezone.now())
    return _render(
        request,
        pages.page_for(setting["page"]),
        setting["parameters"],
        address=address,
        # KioskPoller compares the page string poll.kiosk answers with this
        # one, so it is the stored string, even for a page shown as identify.
        loaded=setting["page"],
        name=setting.get("name", ""),
        madlib=madlib(address),
    )


def _standalone(request, key: str):
    return _render(request, pages.PAGES[key], "{}", standalone=True)


@require_GET
@xframe_options_sameorigin
def ondeck(request):
    """`ondeck.php` outside a kiosk: the on-deck page with a way home and no
    kiosk poll."""
    return _standalone(request, "kiosks/ondeck.kiosk")


@require_GET
@xframe_options_sameorigin
def slideshow(request):
    """`slideshow.php` outside a kiosk."""
    return _standalone(request, "kiosks/slideshow.kiosk")


@require_GET
@xframe_options_sameorigin
def fullscreen(request):
    """`fullscreen.php`: a URL, `kiosk.php` beside this page by default, in a
    full-window frame, and the browser asked to go full screen."""
    return render(request, "kiosk/fullscreen.html", {"kiosk_url": reverse("kiosk-php")})


@require_GET
@xframe_options_sameorigin
def standings_list(request):
    """The standings kiosk's list for a catalog key, fetched when its `key`
    parameter changes. Public, as the kiosk is (S29, D5)."""
    key = request.GET.get("key", "")
    found = state.standings_state.build(active_event(request), key[:40])
    return render(request, "kiosk/standings_list.html", {"standings": found})


@require_GET
@xframe_options_sameorigin
def check_in_list(request):
    """The please-check-in kiosk's list, fetched every 30 s where PHP
    reloaded the page. `parameters` is the kiosk's parameter text."""
    page = pages.PAGES["kiosks/please-check-in.kiosk"]
    found = state.context(
        request, active_event(request), page, request.GET.get("parameters", ""), None
    )
    return render(request, "kiosk/check_in_list.html", found)
