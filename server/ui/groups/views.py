"""The racing groups editor (#43): one page for `racing-groups.php` and the
check-in partitions dialog (`partitions-modal.js`), over the group services
of #25 and #30, the award partition lists of #26 (D35, D38), the settings
service of #63 and the car number ranges of #123 (D48).

The page loads the structure once and never polls. Every mutation answers
with the re-rendered editor out of band, so the manager sees the result of
the change, and with a toast. A control on the page sends its reply to the
toast region. A form in the page's one sheet gets the sheet's next body, or
a body that closes the sheet. A refusal is a 422 error toast, or, for a
form, the form again with the refusal in it. No GET writes anything.
"""

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from awards.services import partition_choice, set_partition_list_by_id
from core.settings_form import (
    PackStandingsRefused,
    apply_group_form,
    set_pack_standings,
)
from roster.models import CarRange, Group, Partition, Subgroup
from roster.services import car_numbers, groups
from roster.services.car_numbers import RangeFailure
from roster.services.groups import GroupFailure, Rule
from ui.groups import state
from ui.navigation import requires_page
from ui.responses import active_event as _event
from ui.responses import (
    closing_sheet,
    in_sheet,
    into_sheet,
    refused,
    service_error,
    toast_html,
)

guard = requires_page("racing-groups")

SHEET_BODY = "groups-sheet-body"
DEFAULT_TROPHIES = "-1"
TROPHIES_MESSAGE = "Pick a number of trophies."
# The settings fields the editor writes, as `settings.write` names them.
TEXT_SETTINGS = ("supergroup-label", "partition-label")
COUNT_SETTINGS = ("n-pack-trophies", "n-den-trophies", "n-rank-trophies")
USE_SUBGROUPS = "do-use-subgroups"
LARGEST_NUMBER = 999_999_999


# ----- replies -----


def _editor(request, tree=None) -> str:
    tree = tree or state.build(_event(request))
    return render_to_string("ui/groups/editor.html", {"tree": tree}, request)


def _editor_oob(request) -> str:
    return f'<div id="groups-editor" hx-swap-oob="innerHTML">{_editor(request)}</div>'


def changed(request, message: str, *, body: str | None = None) -> HttpResponse:
    """The reply to a change: the sheet's next body, or for a form in the
    sheet with no next body, a body that closes it; a toast; and the
    re-rendered editor."""
    if body is None and in_sheet(request, SHEET_BODY):
        html = closing_sheet(request, SHEET_BODY, message)
    else:
        html = into_sheet(request, SHEET_BODY, body) if body is not None else ""
        if message:
            html += toast_html(request, message, oob=in_sheet(request, SHEET_BODY))
    return HttpResponse(html + _editor_oob(request))


def _sheet(request, template: str, context: dict) -> str:
    tree = context.get("tree") or state.build(_event(request))
    return render_to_string(
        f"ui/groups/sheets/{template}", {"tree": tree, **context}, request
    )


# ----- parsing -----


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except TypeError, ValueError:
        return None


def _trophies(posted: str | None):
    """A posted trophy count of a group, subgroup or aggregate: "-1" or
    blank is the setting's default, None here; 0 to `MAX_UNIT_TROPHIES` is
    that count; anything else is not a choice."""
    if posted is None or posted.strip() in ("", DEFAULT_TROPHIES):
        return None
    n = _int(posted)
    if n is None or n not in state.TROPHY_CHOICES:
        raise ValueError(TROPHIES_MESSAGE)
    return n


def _ids(posted: str) -> list[int]:
    found = []
    for raw in posted.split(","):
        n = _int(raw)
        if n is not None and n not in found:
            found.append(n)
    return found


def _award_partitions(post) -> list[int]:
    """The posted partition list of an award unit, as partition ids:
    `scope=all` is all partitions, the empty list; `scope=some` is the
    checked partitions, at least one. The service checks them under its
    lock. Raises ValueError with what to tell the manager."""
    if post.get("scope", "all") != "some":
        return []
    picked = [_int(raw) for raw in post.getlist("partition")]
    if not picked:
        raise ValueError("Check at least one, or pick all of them.")
    if None in picked:
        raise ValueError(NOT_DRAWN)
    return list(dict.fromkeys(picked))


NOT_DRAWN = "Pick only partitions this award draws from."


def _group(request, group_id) -> Group:
    return get_object_or_404(Group, pk=group_id, event=_event(request))


def _subgroup(request, subgroup_id) -> Subgroup:
    return get_object_or_404(Subgroup, pk=subgroup_id, event=_event(request))


def _partition(request, partition_id) -> Partition:
    return get_object_or_404(Partition, pk=partition_id, event=_event(request))


# ----- the page -----


def _page(request, open_url: str = ""):
    tree = state.build(_event(request))
    return render(
        request,
        "ui/groups/page.html",
        {
            "tree": tree,
            "open_url": open_url,
            "page_title": "Racing Groups",
        },
    )


@require_GET
def page(request):
    return _page(request)


# ----- the formation rule (D31) -----


@require_http_methods(["GET", "POST"])
@guard
def rule(request):
    """GET: the confirm sheet for a change of rule, naming what it deletes,
    or a body that closes the sheet for the rule already in force. POST: the
    change, which the service refuses once a heat has a result."""
    event = _event(request)
    posted = request.POST.get("rule") if request.method == "POST" else None
    chosen = posted if posted is not None else request.GET.get("rule", "")
    if chosen not in Rule:
        return service_error(request, "Pick by partition, one group, or custom.")
    if request.method == "GET":
        effects = groups.rule_change_effects(event, chosen)
        tree = state.build(event)
        if tree.rule == chosen:
            return render(request, "ui/components/sheet_closed.html")
        return render(
            request,
            "ui/groups/sheets/rule.html",
            {
                "tree": tree,
                "chosen": chosen,
                "chosen_label": dict(state.rule_choices(tree.labels))[chosen],
                "effects": effects,
            },
        )
    try:
        groups.form_groups_by_rule(event, chosen)
    except GroupFailure as failure:
        return refused(request, failure)
    labels = state.group_labels(event)
    return changed(
        request, f"Groups formed: {dict(state.rule_choices(labels))[chosen]}."
    )


# ----- labels, trophy counts, the pack's choices -----


@require_POST
@guard
def setting(request):
    """One field of the labels and trophy counts, saved as it changes,
    through the settings service the settings page uses too (#63)."""
    event = _event(request)
    post = {"action": "settings.write"}
    for key in TEXT_SETTINGS:
        if key in request.POST:
            post[key] = request.POST[key]
    for key in COUNT_SETTINGS:
        if key in request.POST:
            n = _int(request.POST[key])
            if n is None or not 0 <= n <= state.MAX_DEFAULT_TROPHIES:
                return service_error(
                    request,
                    f"A trophy count is a number, 0 to {state.MAX_DEFAULT_TROPHIES}.",
                )
            post[key] = str(n)
    if USE_SUBGROUPS in request.POST:
        post[f"{USE_SUBGROUPS}-checkbox"] = "1"
        if request.POST[USE_SUBGROUPS] == "1":
            post[USE_SUBGROUPS] = "1"
    if len(post) == 1:
        return service_error(request, "Nothing to save.")
    result = apply_group_form(event, post)
    if result.failure:
        return service_error(request, result.failure[1])
    return changed(request, "Saved.")


@require_POST
@guard
def calc_rule(request):
    """The pack aggregate choice (`award.calc-rule`): calculate normally,
    don't calculate, or use an aggregate's standings. The service checks
    the choice, and that pack trophies need the standings, under its lock."""
    try:
        set_pack_standings(_event(request), request.POST.get("full-field-calc", ""))
    except PackStandingsRefused as failure:
        return service_error(request, str(failure))
    return changed(request, "Saved.")


@require_POST
@guard
def pack_awards(request):
    """The pack's speed-award partition list (D35, D38)."""
    try:
        picked = _award_partitions(request.POST)
    except ValueError as error:
        return service_error(request, str(error))
    try:
        set_partition_list_by_id(_event(request), picked)
    except ValueError:
        return service_error(request, NOT_DRAWN)
    return changed(request, "Saved.")


# ----- ordering -----


def _reordered(request, reorder):
    try:
        reorder(_event(request), _ids(request.POST.get("order", "")))
    except GroupFailure as failure:
        return refused(request, failure)
    return changed(request, "Order saved.")


@require_POST
@guard
def order_groups(request):
    """Groups, or aggregates, in a new order, from a drag or the row menu
    (`class.order`). The other kind keeps its order after them."""
    return _reordered(request, groups.reorder_groups)


@require_POST
@guard
def order_subgroups(request):
    """One group's subgroups in a new order (`rank.order`)."""
    return _reordered(request, groups.reorder_subgroups)


@require_POST
@guard
def order_partitions(request):
    """Partitions in a new order (`partition.order`)."""
    return _reordered(request, groups.reorder_partitions)


# ----- groups and aggregates -----


def _group_sheet(request, group: Group, *, values=None, error="") -> str:
    event = _event(request)
    tree = state.build(event)
    row = state.group_row(tree, group.pk)
    if row is None:
        raise Http404
    values = values or {"name": row.name, "ntrophies": str(row.ntrophies)}
    return _sheet(
        request,
        "group.html",
        {
            "tree": tree,
            "row": row,
            "aggregate": isinstance(row, state.AggregateRow),
            "choice": partition_choice(event, group=group),
            "values": values,
            "error": error,
            "deletable": state.group_deletable(tree, row),
            "trophy_choices": state.TROPHY_CHOICES,
        },
    )


def _sheet_values(post) -> dict:
    return {"name": post.get("name", ""), "ntrophies": post.get("ntrophies", "-1")}


@require_http_methods(["GET", "POST"])
@guard
def group_edit(request, group_id):
    """The group sheet: name, trophy count and speed-award partition list
    (`class.edit`, D35). An aggregate uses it too."""
    group = _group(request, group_id)
    if request.method == "GET":
        return HttpResponse(_group_sheet(request, group))
    values = _sheet_values(request.POST)
    try:
        n_trophies = _trophies(values["ntrophies"])
    except ValueError:
        return HttpResponse(
            _group_sheet(request, group, values=values, error=TROPHIES_MESSAGE)
        )
    try:
        picked = _award_partitions(request.POST)
        groups.edit_group(group, values["name"], n_trophies, picked)
    except (GroupFailure, ValueError) as failure:
        return HttpResponse(
            _group_sheet(
                request,
                group,
                values=values,
                error=getattr(failure, "description", str(failure)),
            )
        )
    return changed(request, f"Saved {values['name'].strip()}.")


@require_http_methods(["GET", "POST"])
@guard
def group_delete(request, group_id):
    group = _group(request, group_id)
    if request.method == "GET":
        return render(
            request,
            "ui/groups/sheets/delete.html",
            {
                "name": group.name,
                "noun": state.group_labels(_event(request)).group.lower(),
                "delete_url": reverse("groups-group-delete", args=[group.pk]),
                "back_url": reverse("groups-group", args=[group.pk]),
            },
        )
    try:
        groups.delete_group(group)
    except GroupFailure as failure:
        return refused(request, failure)
    return changed(request, f"Deleted {group.name}.")


@require_http_methods(["GET", "POST"])
@guard
def group_new(request):
    """A new group, under the custom rule (`class.add`), with its subgroup
    and partition of the same name."""
    event = _event(request)
    tree = state.build(event)
    if request.method == "GET":
        return render(
            request,
            "ui/groups/sheets/new_group.html",
            {"tree": tree, "values": {}, "trophy_choices": state.TROPHY_CHOICES},
        )
    values = _sheet_values(request.POST)
    try:
        n_trophies = _trophies(values["ntrophies"])
        groups.add_group(event, values["name"], n_trophies=n_trophies)
    except (GroupFailure, ValueError) as failure:
        return render(
            request,
            "ui/groups/sheets/new_group.html",
            {
                "tree": tree,
                "values": values,
                "trophy_choices": state.TROPHY_CHOICES,
                "error": getattr(failure, "description", TROPHIES_MESSAGE),
            },
        )
    return changed(request, f"Added {values['name'].strip()}.")


@require_http_methods(["GET", "POST"])
@guard
def aggregate_new(request):
    """A new aggregate (`class.add` with constituents): a name, a trophy
    count, and two or more groups, or with aggregate-by on, two or more
    subgroups."""
    event = _event(request)
    tree = state.build(event)
    context = {"tree": tree, "trophy_choices": state.TROPHY_CHOICES}
    if request.method == "GET":
        return render(
            request,
            "ui/groups/sheets/aggregate.html",
            {
                **context,
                "values": {"by": "group", "picked": [], "scope": "all"},
            },
        )
    by = "subgroup" if request.POST.get("by") == "subgroup" else "group"
    picked = _ids(",".join(request.POST.getlist(by)))
    values = {
        **_sheet_values(request.POST),
        "by": by,
        "picked": picked,
        "scope": request.POST.get("scope", "all"),
        "partitions": _ids(",".join(request.POST.getlist("partition"))),
    }
    try:
        n_trophies = _trophies(values["ntrophies"])
        award_ids = _award_partitions(request.POST)
        groups.add_aggregate(
            event,
            values["name"],
            group_ids=picked if by == "group" else (),
            subgroup_ids=picked if by == "subgroup" else (),
            n_trophies=n_trophies,
            award_partition_ids=award_ids,
        )
    except GroupFailure as failure:
        error = failure.description
    except ValueError as failure:
        error = str(failure)
    else:
        return changed(request, f"Added {values['name'].strip()}.")
    return render(
        request,
        "ui/groups/sheets/aggregate.html",
        {**context, "values": values, "error": error},
    )


# ----- subgroups -----


def _subgroup_sheet(request, subgroup: Subgroup, *, values=None, error="") -> str:
    event = _event(request)
    tree = state.build(event)
    found = state.subgroup_row(tree, subgroup.pk)
    if found is None:
        raise Http404
    group, row = found
    values = values or {"name": row.name, "ntrophies": str(row.ntrophies)}
    return _sheet(
        request,
        "subgroup.html",
        {
            "tree": tree,
            "group": group,
            "row": row,
            "choice": partition_choice(event, subgroup=subgroup),
            "values": values,
            "error": error,
            "deletable": state.subgroup_deletable(group, row),
            "trophy_choices": state.TROPHY_CHOICES,
        },
    )


@require_http_methods(["GET", "POST"])
@guard
def subgroup_edit(request, subgroup_id):
    """The subgroup sheet (`rank.edit`, D35). Under by partition the group
    and partition take the new name too (D49), which the re-rendered editor
    shows."""
    subgroup = _subgroup(request, subgroup_id)
    if request.method == "GET":
        return HttpResponse(_subgroup_sheet(request, subgroup))
    values = _sheet_values(request.POST)
    try:
        n_trophies = _trophies(values["ntrophies"])
    except ValueError:
        return HttpResponse(
            _subgroup_sheet(request, subgroup, values=values, error=TROPHIES_MESSAGE)
        )
    try:
        picked = _award_partitions(request.POST)
        groups.edit_subgroup(subgroup, values["name"], n_trophies, picked)
    except (GroupFailure, ValueError) as failure:
        return HttpResponse(
            _subgroup_sheet(
                request,
                subgroup,
                values=values,
                error=getattr(failure, "description", str(failure)),
            )
        )
    return changed(request, f"Saved {values['name'].strip()}.")


@require_http_methods(["GET", "POST"])
@guard
def subgroup_delete(request, subgroup_id):
    subgroup = _subgroup(request, subgroup_id)
    if request.method == "GET":
        return render(
            request,
            "ui/groups/sheets/delete.html",
            {
                "name": subgroup.name,
                "noun": state.group_labels(_event(request)).subgroup.lower(),
                "delete_url": reverse("groups-subgroup-delete", args=[subgroup.pk]),
                "back_url": reverse("groups-subgroup", args=[subgroup.pk]),
            },
        )
    try:
        groups.delete_subgroup(subgroup)
    except GroupFailure as failure:
        return refused(request, failure)
    return changed(request, f"Deleted {subgroup.name}.")


NEW_GROUP = "new"


@require_http_methods(["GET", "POST"])
@guard
def subgroup_move(request, subgroup_id):
    """ "Move to...": the other groups and "New group" (`rank.move`), so a
    subgroup changes group with a tap, never a drag."""
    subgroup = _subgroup(request, subgroup_id)
    event = _event(request)
    if request.method == "GET":
        tree = state.build(event)
        return render(
            request,
            "ui/groups/sheets/move.html",
            {
                "tree": tree,
                "subgroup": subgroup,
                "targets": [g for g in tree.groups if g.id != subgroup.group_id],
                "new_group": NEW_GROUP,
            },
        )
    target = request.POST.get("group", "")
    group_id = None if target == NEW_GROUP else (_int(target) or 0)
    try:
        subgroup = groups.move_subgroup_to(event, subgroup.pk, group_id)
    except GroupFailure as failure:
        return refused(request, failure)
    return changed(request, f"Moved {subgroup.name} to {subgroup.group.name}.")


# ----- partitions -----


def _partitions_body(
    request, *, values=None, error="", error_code="", notice=""
) -> str:
    return _sheet(
        request,
        "partitions.html",
        {
            "values": values or {},
            "error": error,
            "error_code": error_code,
            "notice": notice,
        },
    )


@require_GET
@guard
def partitions(request):
    """The partitions sheet, with its own URL so check-in's "Edit
    partitions" opens it (#42): add, rename, delete, reorder, and each
    partition's car number ranges (D48). A plain GET opens the editor with
    the sheet open."""
    if request.headers.get("HX-Request") != "true":
        return _page(request, open_url=reverse("groups-partitions"))
    return HttpResponse(_partitions_body(request))


@require_POST
@guard
def partition_add(request):
    """`partition.add`. A name another partition holds with case ignored
    is `name-taken` (D31), shown in the sheet."""
    name = request.POST.get("name", "")
    try:
        partition = groups.add_partition(_event(request), name)
    except GroupFailure as failure:
        return HttpResponse(
            _partitions_body(
                request,
                values={"name": name},
                error=failure.description,
                error_code=failure.code,
            )
        )
    return changed(
        request,
        f"Added {partition.name}.",
        body=_partitions_body(request, notice=f"Added {partition.name}."),
    )


def _partition_body(request, partition: Partition, *, values=None, error="") -> str:
    tree = state.build(_event(request))
    row = state.partition_row(tree, partition.pk)
    if row is None:
        raise Http404
    return _sheet(
        request,
        "partition.html",
        {
            "tree": tree,
            "row": row,
            "values": values or {"name": row.name},
            "error": error,
        },
    )


@require_http_methods(["GET", "POST"])
@guard
def partition_edit(request, partition_id):
    """Rename a partition (`partition.edit`): only names change (D31). Its
    car number ranges are on the same sheet."""
    partition = _partition(request, partition_id)
    if request.method == "GET":
        return HttpResponse(_partition_body(request, partition))
    name = request.POST.get("name", "")
    try:
        groups.rename_partition(partition, name)
    except GroupFailure as failure:
        return HttpResponse(
            _partition_body(
                request, partition, values={"name": name}, error=failure.description
            )
        )
    return changed(
        request,
        f"Renamed to {name.strip()}.",
        body=_partitions_body(request, notice=f"Renamed to {name.strip()}."),
    )


@require_http_methods(["GET", "POST"])
@guard
def partition_delete(request, partition_id):
    partition = _partition(request, partition_id)
    if request.method == "GET":
        return render(
            request,
            "ui/groups/sheets/delete.html",
            {
                "name": partition.name,
                "noun": state.group_labels(_event(request)).partition.lower(),
                "delete_url": reverse("groups-partition-delete", args=[partition.pk]),
                "back_url": reverse("groups-partition", args=[partition.pk]),
            },
        )
    try:
        groups.delete_partition(partition)
    except GroupFailure as failure:
        return refused(request, failure)
    return changed(
        request,
        f"Deleted {partition.name}.",
        body=_partitions_body(request, notice=f"Deleted {partition.name}."),
    )


def _number(posted: str) -> int | None:
    n = _int(posted)
    return n if n is not None and 0 <= n <= LARGEST_NUMBER else None


@require_POST
@guard
def range_add(request, partition_id):
    """Add a car number range to the partition (D48, #123)."""
    partition = _partition(request, partition_id)
    first = _number(request.POST.get("range_first", ""))
    last = _number(request.POST.get("range_last", ""))
    if first is None or last is None:
        return HttpResponse(
            _partition_body(
                request, partition, error="A range needs a first and a last number."
            )
        )
    try:
        car_numbers.add_range(partition, first, last)
    except RangeFailure as failure:
        return HttpResponse(
            _partition_body(request, partition, error=failure.description)
        )
    return changed(
        request,
        f"{partition.name} numbers {first} to {last} added.",
        body=_partition_body(request, partition),
    )


@require_POST
@guard
def range_remove(request, range_id):
    car_range = get_object_or_404(CarRange, pk=range_id, event=_event(request))
    partition = car_range.partition
    car_numbers.remove_range(car_range)
    return changed(
        request,
        f"{partition.name} numbers {car_range.first} to {car_range.last} removed.",
        body=_partition_body(request, partition),
    )
