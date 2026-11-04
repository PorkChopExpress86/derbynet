"""The awards editor (#45): `awards-editor.php` and `awards-editor.js`, over
the award services of #33 and the partition lists of #26 (D35, D38).

The page loads once and never polls, as PHP's does not
(`website/js/awards-editor.js` sets no timer). Every change
answers with the award list again, out of band, and a toast; the sheet's
form gets a body that closes the sheet, or the form again with the refusal
in it. Each view parses the posted text and makes one service call, which
checks every rule under the `RaceState` lock. No GET writes anything.
"""

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from awards import editing
from awards.editing import AwardFailure
from awards.models import Award
from awards.services import award_partition_choice
from ui.awards import state
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

guard = requires_page("awards-editor")

SHEET_BODY = "awards-sheet-body"


# ----- parsing -----


def _scope(posted) -> tuple[int | None, int | None]:
    """A posted scope, `group,subgroup` with 0 for none. Raises ValueError
    for text that is not one."""
    parts = str(posted or "0,0").split(",")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise ValueError("Pick who the award is for.")
    group, subgroup = (int(p) for p in parts)
    if group >= 2**31 or subgroup >= 2**31:
        raise ValueError("Pick who the award is for.")
    return group or None, subgroup or None


def _partitions(post) -> list[int] | None:
    """The posted partition list: `scope=all`, or no fieldset, is all
    partitions (None); `scope=some` is the checked ones, which the service
    checks under its lock."""
    if post.get("scope", "all") != "some":
        return None
    picked = [posted_int(raw, positive=True) for raw in post.getlist("partition")]
    if None in picked:
        raise ValueError("Pick only partitions this award draws from.")
    return list(dict.fromkeys(picked))


# ----- replies -----


def _list(request, editor=None) -> str:
    editor = editor or state.build(_event(request))
    return render_to_string("ui/awards/list.html", {"editor": editor}, request)


def changed(request, message: str) -> HttpResponse:
    if in_sheet(request, SHEET_BODY):
        html = closing_sheet(request, SHEET_BODY, message)
    else:
        html = toast_html(request, message, "ok")
    oob = f'<div id="awards-list" hx-swap-oob="innerHTML">{_list(request)}</div>'
    return HttpResponse(html + oob)


# ----- the page -----


@require_GET
def page(request):
    return render(
        request,
        "ui/awards/page.html",
        {"editor": state.build(_event(request)), "page_title": "Awards Editor"},
    )


# ----- the award sheet -----


def _choice(request, scope: tuple, award: Award | None):
    return award_partition_choice(_event(request), *scope, award)


def _sheet(request, *, award, values, error="", scope=(None, None)):
    editor = state.build(_event(request))
    return render(
        request,
        "ui/awards/sheets/award.html",
        {
            "editor": editor,
            "award": award,
            "values": values,
            "error": error,
            "choice": _choice(request, scope, award),
            "post_url": reverse("awards-editor-award", args=[award.pk])
            if award
            else reverse("awards-editor-new"),
        },
    )


def _posted_values(post) -> dict:
    return {
        "name": post.get("name", ""),
        "awardtypeid": posted_int(post.get("awardtypeid", ""), positive=True),
        "scope": post.get("class_and_rank", "0,0"),
    }


def _save(request, award: Award | None):
    try:
        scope = _scope(request.POST.get("class_and_rank"))
        saved = editing.save_award(
            _event(request),
            award.pk if award else None,
            name=request.POST.get("name", ""),
            award_type_id=posted_int(
                request.POST.get("awardtypeid", ""), positive=True
            ),
            group_id=scope[0],
            subgroup_id=scope[1],
            partition_ids=_partitions(request.POST),
        )
    except (AwardFailure, ValueError) as failure:
        message = getattr(failure, "description", str(failure))
        try:
            scope = _scope(request.POST.get("class_and_rank"))
        except ValueError:
            scope = (None, None)
        return _sheet(
            request,
            award=award,
            values=_posted_values(request.POST),
            error=message,
            scope=scope,
        )
    return changed(request, f"Saved {saved.name}.")


def _award(request, award_id) -> Award:
    found = Award.objects.filter(event=_event(request), pk=award_id).first()
    if found is None:
        raise Http404
    return found


@require_http_methods(["GET", "POST"])
@guard
def award_new(request):
    """The sheet for a new award (`award.edit awardid=new`): pack-wide, the
    first type, all partitions."""
    if request.method == "POST":
        return _save(request, None)
    return _sheet(
        request, award=None, values={"name": "", "awardtypeid": None, "scope": "0,0"}
    )


@require_http_methods(["GET", "POST"])
@guard
def award_edit(request, award_id):
    """The sheet of an award (`award.edit`): name, type, group or subgroup,
    and the partitions it can go to."""
    award = _award(request, award_id)
    if request.method == "POST":
        return _save(request, award)
    scope = (award.group_id, award.subgroup_id)
    return _sheet(
        request,
        award=award,
        values={
            "name": award.name,
            "awardtypeid": award.award_type_id,
            "scope": state.scope_value(*scope),
        },
        scope=scope,
    )


@require_GET
@guard
def partitions(request):
    """The sheet's partition list for the group or subgroup just picked:
    the partitions an award there draws from (D38)."""
    try:
        scope = _scope(request.GET.get("class_and_rank"))
    except ValueError:
        scope = (None, None)
    award = Award.objects.filter(
        event=_event(request),
        pk=posted_int(request.GET.get("award", ""), positive=True),
    ).first()
    editor = state.build(_event(request))
    return render(
        request,
        "ui/awards/partitions.html",
        {"editor": editor, "choice": _choice(request, scope, award)},
    )


# ----- delete and reorder -----


@require_http_methods(["GET", "POST"])
@guard
def award_delete(request, award_id):
    """GET: the confirm sheet, in place of PHP's confirm(). POST: the delete
    (`award.delete`)."""
    if request.method == "GET":
        award = _award(request, award_id)
        return render(
            request,
            "ui/awards/sheets/delete.html",
            {
                "name": award.name,
                "delete_url": reverse("awards-editor-delete", args=[award.pk]),
                "back_url": reverse("awards-editor-award", args=[award.pk]),
            },
        )
    try:
        name = editing.delete_award_by_id(_event(request), award_id)
    except AwardFailure as failure:
        return refused(request, failure)
    return changed(request, f"Deleted {name}.")


@require_POST
@guard
def order(request):
    """The list in a new order, from a drag or a row's Move up and Move down
    (`award.order`)."""
    ids = [
        posted_int(raw, positive=True)
        for raw in request.POST.get("order", "").split(",")
    ]
    if not ids or None in ids:
        return service_error(request, "That order names no awards.")
    try:
        editing.reorder_awards(_event(request), ids)
    except AwardFailure as failure:
        return refused(request, failure)
    return changed(request, "Order saved.")
