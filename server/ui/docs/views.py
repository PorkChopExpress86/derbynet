"""The guides under `/docs/` (#61): the index, a guide, and a guide's image.

Anyone may read them, logged in or not and before the first event exists
(D92, refining D5 as D70 did): they hold no race data, and a phone needs the
install and certificate guides before anyone logs in. They read only what
`manage.py build_docs` wrote into `settings.DOCS_ROOT` (`ui.docs.build`), so
a page that has not been built is a 404 that says so.
"""

import json
import mimetypes

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ui.docs.build import GUIDES, IMAGES, INDEX
from ui.navigation import requires_page

NOT_BUILT = "The guides have not been built. Run `manage.py build_docs`."


def _read(name: str) -> dict:
    try:
        return json.loads((settings.DOCS_ROOT / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Http404(NOT_BUILT) from None


@require_GET
@requires_page("docs")
def index(request):
    return render(request, "ui/docs/index.html", {"guides": _read(INDEX)["guides"]})


@require_GET
@requires_page("docs-page")
def page(request, slug):
    if slug not in GUIDES:
        raise Http404("No such guide.")
    guide = _read(f"{slug}.json")
    return render(
        request,
        "ui/docs/page.html",
        {"page_title": guide["title"], "html": guide["html"], "slug": slug},
    )


@require_GET
@requires_page("docs")
def image(request, name):
    # Only a name the build listed, so a URL never names another file.
    if name not in _read(INDEX)["images"]:
        raise Http404("No such image.")
    path = settings.DOCS_ROOT / IMAGES / name
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    response = FileResponse(open(path, "rb"), content_type=content_type)
    response["Cache-Control"] = "max-age=3600"
    return response
