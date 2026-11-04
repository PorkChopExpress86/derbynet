"""Photo management (#47): `photo-thumbs.php` for a phone, as the registry
pages `racer-photos` and `car-photos`, over the photo service of #35.

A volunteer taps instead of dragging: a racer, then a photo from a sheet of
unassigned photos, or a photo, then a racer from a searchable sheet. The crop
and capture sheets are #42's (`ui/photo_sheets/`). Every POST calls one
`media.photos` service and answers with a toast, or the sheet's next body,
and an `HX-Trigger: photos-refresh` header that lists the repository again.
A refusal is a 422 error toast. No GET writes anything.

Every photo change needs ASSIGN_RACER_IMAGE, the page's own permission, where
PHP's `photo.crop` checks none (S19). An upload needs PHOTO_UPLOAD too.
"""

import json
from urllib.parse import urlencode

from django.http import (
    Http404,
    HttpResponse,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import get_script_prefix, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import Perm, allows
from media import photos
from racing.engine.phpcompat import php_numeric, php_truthy
from ui.navigation import BY_KEY, can_open, requires_page
from ui.photos import state
from ui.photos.state import Filters
from ui.responses import active_event as _event
from ui.responses import (
    banner_back,
    closing_sheet,
    event_racer,
    into_sheet,
    name_of,
    refused,
    service_error,
    toast_html,
)

guard = requires_page("racer-photos")

SHEET = "photos"
SHEET_BODY = "photos-sheet-body"
ROTATIONS = {"left": 90, "right": 270}
CROP_FIELDS = ("left", "top", "right", "bottom", "original_width", "original_height")
# The query a link to the other repository keeps, as photo-thumbs.php's
# Racers and Cars button does.
KEPT = ("order", "racerid", "back")


def _repo(request, repo: str) -> photos.Repository:
    if repo not in state.REPOS:
        raise Http404
    return photos.repository(_event(request), repo)


def _photo_name(request) -> str:
    """The photo a control names, `?photo=`. Client input: the service checks
    it as it checks any name."""
    name = request.GET.get("photo", request.POST.get("photo", ""))
    return name if isinstance(name, str) else ""


def _refreshing(response: HttpResponse) -> HttpResponse:
    response["HX-Trigger"] = json.dumps({"photos-refresh": True})
    return response


def toasted(request, message: str, *, level="ok", undo_url=""):
    return _refreshing(HttpResponse(toast_html(request, message, level, undo_url)))


def sheet_reply(request, html: str):
    return _refreshing(HttpResponse(into_sheet(request, SHEET_BODY, html)))


def closed_reply(request, message: str):
    return _refreshing(HttpResponse(closing_sheet(request, SHEET_BODY, message)))


def _need_upload(request):
    if not allows(request, Perm.PHOTO_UPLOAD):
        return service_error(request, "Your role cannot upload photos.")
    return None


def _perms(request) -> dict:
    return {"can_upload": allows(request, Perm.PHOTO_UPLOAD)}


def _query(**values) -> str:
    kept = {k: v for k, v in values.items() if v not in ("", None)}
    return "?" + urlencode(kept) if kept else ""


# ----- the page and its list -----


def legacy_page(request):
    """`photo-thumbs.php`: a 301 to the repository's page, keeping the rest of
    the query (`order`, `racerid`, `back`). `repo=car` is the car photos;
    anything else is the racer photos, where PHP fails on an unknown name:
    `photo_repository()` returns nothing for it (`website/inc/photo-config.inc:527`
    to `:533`) and `website/photo-thumbs.php:14` goes on to use it."""
    query = request.GET.copy()
    repo = query.pop("repo", ["head"])[-1]
    target = reverse(state.REPOS["car" if repo == "car" else "head"])
    if query:
        target += "?" + query.urlencode()
    return HttpResponsePermanentRedirect(target)


def _list_context(request, listing, filters, *, highlight=None) -> dict:
    return {
        "listing": listing,
        "filters": filters,
        "repo": listing.repo,
        "highlight": highlight,
        "settings_url": reverse("settings")
        if can_open(request, BY_KEY["settings"])
        else "",
        **_perms(request),
    }


def _repo_links(request, repo: str, filters: Filters) -> list[dict]:
    """The Racers and Cars control: links that keep the order, the deep
    link and the back page."""
    kept = {k: request.GET.get(k, "") for k in KEPT}
    kept["order"] = filters.order
    return [
        {
            "label": state.REPO_LABELS[other],
            "url": reverse(state.REPOS[other]) + _query(**kept),
            "current": other == repo,
        }
        for other in state.REPOS
    ]


@require_GET
def page(request):
    repo = state.repo_of_page(request.nav_entry.key)
    filters = Filters.from_request(request.GET)
    event = _event(request)
    listing = state.build(event, repo, filters)
    try:
        highlight = int(request.GET.get("racerid", ""))
    except ValueError:
        highlight = None
    return render(
        request,
        "ui/photos/page.html",
        {
            **_list_context(request, listing, filters, highlight=highlight),
            "orders": state.orders(event),
            "repo_links": _repo_links(request, repo, filters),
            "back_url": banner_back(request, "index.php"),
        },
    )


@require_GET
@guard
def listing(request, repo):
    """The list again, after a change (`photos-refresh`) or a new search or
    order. The Racers and Cars links follow the order, out of band."""
    _repo(request, repo)
    filters = Filters.from_request(request.GET)
    found = state.build(_event(request), repo, filters)
    return render(
        request,
        "ui/photos/list.html",
        {
            **_list_context(request, found, filters),
            "repo_links": _repo_links(request, repo, filters),
            "oob": True,
        },
    )


# ----- tap to assign -----


@require_GET
@guard
def photo_choices(request, repo, racer_id):
    """Tap a racer: the unassigned photos, each a button that assigns it."""
    _repo(request, repo)
    racer = event_racer(request, racer_id)
    found = state.build(_event(request), repo, Filters())
    return render(
        request,
        "ui/photos/sheets/photos.html",
        {
            "racer": racer,
            "title": f"Photo for {state.car(racer.car_number)} {name_of(racer)}",
            "repo": repo,
            "pool": found.pool,
            "noun": state.REPO_NOUNS[repo].lower(),
        },
    )


def _racer_choices_context(request, repo, name) -> dict:
    filters = Filters.from_request(request.GET)
    found = state.build(_event(request), repo, filters, without_photo=True)
    return {
        "repo": repo,
        "photo": name,
        "query": _query(photo=name),
        "cards": found.cards,
        "filters": filters,
    }


@require_GET
@guard
def racer_choices(request, repo):
    """Tap a photo: the racers it can go to, with a search field, and the
    photo's crop sheet."""
    repository = _repo(request, repo)
    name = _photo_name(request)
    if not photos.is_valid_name(name):
        raise Http404
    return render(
        request,
        "ui/photos/sheets/racers.html",
        {
            **_racer_choices_context(request, repo, name),
            "title": f"Assign {name}",
            "thumbnail": state.pool_photo(repository, name).thumbnail,
        },
    )


@require_GET
@guard
def racer_choices_list(request, repo):
    """The racer sheet's search."""
    _repo(request, repo)
    return render(
        request,
        "ui/photos/sheets/racer_choices.html",
        _racer_choices_context(request, repo, _photo_name(request)),
    )


@require_http_methods(["POST"])
@guard
def assign(request, repo):
    """Give `racer` the `photo`. The unassign toast's Undo posts here with
    `restore=1`, which gives the photo back only while the racer has none.
    The toast names the racer the service read under its lock."""
    repository = _repo(request, repo)
    data = request.POST if request.POST else request.GET
    restore = php_truthy(data.get("restore", ""))
    try:
        done = photos.assign_photo(
            _event(request),
            repository,
            data.get("racer", ""),
            _photo_name(request),
            restore=restore,
        )
    except photos.Refused as failure:
        return refused(request, failure)
    what = "back for" if restore else "assigned to"
    message = f"{state.REPO_NOUNS[repo]} {what} {name_of(done.racer)}."
    if restore:
        return toasted(request, message)
    return closed_reply(request, message)


@require_POST
@guard
def unassign(request, repo, racer_id):
    """Take the racer's photo away, with an Undo that gives back the photo
    the service took."""
    repository = _repo(request, repo)
    try:
        done = photos.assign_photo(_event(request), repository, str(racer_id), "")
    except photos.Refused as failure:
        return refused(request, failure)
    undo = ""
    if done.previous:
        undo = reverse("photos-assign", args=[repo]) + _query(
            racer=done.racer.pk, photo=done.previous, restore=1
        )
    return toasted(
        request,
        f"{state.REPO_NOUNS[repo]} taken from {name_of(done.racer)}.",
        level="info",
        undo_url=undo,
    )


# ----- crop, rotate, delete -----


def _crop_html(request, repository: photos.Repository, name: str) -> str:
    repo = repository.name
    query = _query(photo=name)
    return render_to_string(
        "ui/photos/sheets/crop.html",
        {
            "sheet": SHEET,
            "body": SHEET_BODY,
            "repo": repo,
            "title": f"Crop {name}",
            "alt": f"{state.REPO_NOUNS[repo]} {name}",
            "work_url": get_script_prefix()
            + photos.page_url(repository, name, photos.WORK),
            "crop_url": reverse("photos-crop", args=[repo]) + query,
            "rotate_left_url": reverse("photos-rotate", args=[repo, "left"]) + query,
            "rotate_right_url": reverse("photos-rotate", args=[repo, "right"]) + query,
            "delete_url": reverse("photos-delete", args=[repo]) + query,
            "done_label": "Done",
        },
        request,
    )


@require_GET
@guard
def crop_sheet(request, repo):
    repository = _repo(request, repo)
    name = _photo_name(request)
    if not photos.is_valid_name(name) or repository.directory is None:
        raise Http404
    return HttpResponse(_crop_html(request, repository, name))


@require_POST
@guard
def crop(request, repo):
    repository = _repo(request, repo)
    values = [php_numeric(request.POST.get(field, "")) for field in CROP_FIELDS]
    if any(v is None or v != v or v in (float("inf"), float("-inf")) for v in values):
        return service_error(request, "Draw the crop box first.")
    name = _photo_name(request)
    try:
        photos.crop_photo(_event(request), repository, name, photos.CropBox(*values))
    except photos.Refused as failure:
        return refused(request, failure)
    return closed_reply(request, f"{name} cropped.")


@require_POST
@guard
def rotate(request, repo, direction):
    repository = _repo(request, repo)
    if direction not in ROTATIONS:
        raise Http404
    name = _photo_name(request)
    try:
        photos.rotate_photo(_event(request), repository, name, ROTATIONS[direction])
    except photos.Refused as failure:
        return refused(request, failure)
    return sheet_reply(request, _crop_html(request, repository, name))


@require_http_methods(["GET", "POST"])
@guard
def delete(request, repo):
    """The confirm sheet (GET) and the delete (POST): the photo leaves the
    folder, and any racer that showed it loses it."""
    repository = _repo(request, repo)
    name = _photo_name(request)
    query = _query(photo=name)
    if request.method == "GET":
        if not photos.is_valid_name(name):
            raise Http404
        return render(
            request,
            "ui/photos/sheets/delete.html",
            {
                "name": name,
                "delete_url": reverse("photos-delete", args=[repo]) + query,
                "back_url": reverse("photos-crop-sheet", args=[repo]) + query,
            },
        )
    if not photos.delete_photo(_event(request), repository, name):
        return service_error(request, f"There is no photo named {name}.")
    return closed_reply(request, f"{name} deleted.")


# ----- upload -----


@require_POST
@guard
def upload(request, repo):
    """One file of a bulk upload, which the page sends one at a time. JSON,
    for the page's own request: the stored name, or the refusal's message
    with HTTP 422."""
    if not allows(request, Perm.PHOTO_UPLOAD):
        return JsonResponse({"message": "Your role cannot upload photos."}, status=403)
    repository = _repo(request, repo)
    try:
        result = photos.upload_photo(
            _event(request),
            repository,
            request.FILES.get("photo"),
            max_file_size=request.POST.get("MAX_FILE_SIZE"),
        )
    except photos.Refused as failure:
        return JsonResponse({"message": failure.description}, status=422)
    return JsonResponse({"name": result.uploaded})


@require_GET
@guard
def capture_sheet(request, repo, racer_id):
    """Take photo: #42's capture sheet, for this racer."""
    if no := _need_upload(request):
        return no
    _repo(request, repo)
    racer = event_racer(request, racer_id)
    return render(
        request,
        "ui/photos/sheets/capture.html",
        {
            "sheet": SHEET,
            "body": SHEET_BODY,
            "repo": repo,
            "title": f"{state.REPO_NOUNS[repo]} · {state.car(racer.car_number)}"
            f" {name_of(racer)}",
            "upload_url": reverse("photos-capture-upload", args=[repo, racer.pk]),
        },
    )


@require_POST
@guard
def capture_upload(request, repo, racer_id):
    """The photo the browser downscaled, for the racer, then the crop sheet."""
    if no := _need_upload(request):
        return no
    repository = _repo(request, repo)
    racer = event_racer(request, racer_id)
    try:
        result = photos.upload_photo(
            _event(request),
            repository,
            request.FILES.get("photo"),
            autocrop_requested=php_truthy(request.POST.get("autocrop", "")),
            selector=photos.ByRacerId(str(racer.pk)),
        )
    except photos.Refused as failure:
        return refused(request, failure)
    if result.failure is not None:
        return service_error(request, result.failure[1])
    html = _crop_html(request, repository, result.uploaded)
    message = f"{state.REPO_NOUNS[repo]} saved for {name_of(racer)}."
    if result.warning:
        message += f" {result.warning}"
    notice = render_to_string("ui/checkin/notice.html", {"message": message}, request)
    return sheet_reply(request, notice + html)
