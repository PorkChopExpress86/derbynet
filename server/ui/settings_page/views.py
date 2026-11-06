"""The settings page (#48): `settings.php` as collapsible sections whose
fields each save on change, over `core.settings_page` and the weigh-in
service of #70.

A field posts its key and value and gets itself back, with "Saved" or the
refusal beside it. A lane count change also sends the lanes in use and the
lane colors out of band, since both follow the count. No GET writes. A
number outside its field's range is refused, where PHP stores any (D80).
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from core import diagnostics, settings_page
from core.settings_page import Kind, SaveRefused
from roster.models import Partition
from roster.services.weigh_in import WeighInFailure, set_partition_weigh_in
from ui.navigation import requires_page
from ui.playlist import state as playlist_state
from ui.responses import active_event as _event
from ui.settings_page import state

guard = requires_page("settings")

LANE_KEYS = ("n-lanes",)


@require_GET
@guard
def page(request):
    return render(
        request,
        "ui/settings_page/page.html",
        {"page": state.build(_event(request)), **state.CHOICES},
    )


def _lanes_html(request, event) -> str:
    """The lanes in use and the lane colors, out of band."""
    return render_to_string(
        "ui/settings_page/lanes_oob.html",
        {
            "scheduling": playlist_state.scheduling(event),
            "part": "lanes",
            "lane_colors": state.lane_colors(event),
            **state.CHOICES,
        },
        request,
    )


@require_POST
@guard
def save(request):
    """One field: `key` and its `value`."""
    event = _event(request)
    key = request.POST.get("key", "")
    error = ""
    try:
        settings_page.save_field(event, key, request.POST.get("value", ""))
    except SaveRefused as refused:
        error = refused.description
    setting = settings_page.BY_KEY.get(key)
    if setting is None:
        return HttpResponse(status=400)
    view = state.field_view(event, setting)
    view.saved, view.error = not error, error
    if error and setting.kind is Kind.DIRECTORY:
        # The refused path stays in the field, with its own badge.
        view.value = request.POST.get("value", "")
        view.status = diagnostics.folder_status(view.value)
    html = render_to_string("ui/settings_page/field.html", {"fv": view}, request)
    if key in LANE_KEYS and not error:
        html += _lanes_html(request, event)
    return HttpResponse(html, status=200)


@require_POST
@guard
def lane_colors(request):
    event = _event(request)
    error = ""
    try:
        settings_page.save_lane_colors(event, request.POST.getlist("color"))
    except SaveRefused as refused:
        error = refused.description
    return render(
        request,
        "ui/settings_page/lane_colors.html",
        {
            "lane_colors": state.lane_colors(event),
            "saved": not error,
            "error": error,
            **state.CHOICES,
        },
    )


@require_POST
@guard
def weigh_in(request, partition_id: int):
    """A partition's weigh-in. A new mode clears the partition's weigh-ins;
    the row says so before the Save (`set_partition_weigh_in`)."""
    partition = get_object_or_404(Partition, pk=partition_id, event=_event(request))
    error = ""
    try:
        partition = set_partition_weigh_in(
            partition,
            request.POST.get("mode", ""),
            request.POST.get("unit", ""),
            request.POST.get("max", "").strip(),
        )
    except WeighInFailure as failure:
        error = failure.description
    row = state.weigh_in_row(partition)
    row.saved, row.error = not error, error
    return render(
        request, "ui/settings_page/weigh_in_row.html", {"row": row, **state.CHOICES}
    )
