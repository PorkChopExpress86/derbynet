"""The rounds playlist page (#44): `playlist.php` and the scheduling parts of
`settings.php`, over the playlist services of #25 and #34, the settings
service of #63 and the lane poll of #108.

The page loads once and never polls. Every change to the queue answers
with the queue again, out of band, and a toast; a form in the page's one
sheet gets a body that closes the sheet, or the form again with the
refusal in it. Every rule about stored state is the service's, checked
under the `RaceState` lock: a view parses the posted text and makes one
service call. No GET writes anything.
"""

from urllib.parse import urlencode

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import Perm, allows
from accounts.views import page_requires
from core.settings_form import apply_form
from racing.services import playlist, rounds
from racing.services.playlist import (
    MAX_RUNS_PER_LANE,
    PlaylistFailure,
    RemovedStep,
    StepChoice,
)
from ui.navigation import requires_page
from ui.playlist import state
from ui.responses import active_event as _event
from ui.responses import (
    closing_sheet,
    in_sheet,
    refused,
    service_error,
    toast_html,
)

guard = requires_page("playlist")

SHEET_BODY = "playlist-sheet-body"
SETTINGS_WRITE = {"action": "settings.write"}
# The scheduling method radio to the two settings it writes, as
# `on_schedule_method_change()` in `website/js/settings.js` sets them.
METHOD_SETTINGS = {
    "normal": {"max-runs-per-car": "0", "rotation-schedule": "0"},
    "abbreviated": {"max-runs-per-car": "1", "rotation-schedule": "0"},
    "rotation": {"max-runs-per-car": "0", "rotation-schedule": "1"},
}
# `playlist.php`'s back button, by the PHP page a link names in `back`.
BACK_PAGES = {"setup.php": "setup", "coordinator.php": "dashboard"}
MAX_LANES = 31


# ----- parsing ---------------------------------------------------------------------


def _int(value) -> int | None:
    try:
        n = int(str(value).strip())
    except TypeError, ValueError:
        return None
    return n if -(2**31) < n < 2**31 else None


def _choice(post, *, cut: bool) -> StepChoice:
    """The entry sheet's fields. With `cut`, the sheet showed the top N,
    which a final must have (D43): a blank or a number that is not one is
    the refusal. Raises ValueError with what to tell the manager."""
    runs = _int(post.get("runs", ""))
    if runs is None or not 1 <= runs <= MAX_RUNS_PER_LANE:
        raise ValueError(f"Runs per lane is 1 to {MAX_RUNS_PER_LANE}.")
    then = _int(post.get("then", "0"))
    if then is None:
        raise ValueError("Pick what happens after the round.")
    top = None
    if cut:
        top = _int(post.get("top", ""))
        if top is None or top < 1:
            raise ValueError("Say how many racers go to this final.")
    return StepChoice(
        top=top,
        bucketed=post.get("bucketed") == "1",
        runs=runs,
        then=then,
        waits=post.get("waits") == "1",
    )


def back_url(request) -> str:
    """`?back=` names the page the back button returns to: setup.php and
    coordinator.php by their new pages, or a path on this server. Anything
    else returns to the race dashboard, as `playlist.php` does."""
    back = request.GET.get("back", "")
    if back in BACK_PAGES:
        return reverse(BACK_PAGES[back])
    if back.startswith("/") and url_has_allowed_host_and_scheme(
        back, allowed_hosts={request.get_host()}
    ):
        return back
    return reverse("dashboard")


# ----- replies ---------------------------------------------------------------------


def _queue(request, found=None) -> str:
    found = found or state.build(_event(request))
    return render_to_string(
        "ui/playlist/queue.html",
        {"playlist": found, "can_race": allows(request, Perm.CONTROL_RACE)},
        request,
    )


def changed(request, message: str, *, level="ok", undo_url="") -> HttpResponse:
    """A change's reply: a body that closes the sheet for a form in it, or a
    toast; and the queue again, out of band."""
    if in_sheet(request, SHEET_BODY):
        html = closing_sheet(request, SHEET_BODY, message)
    else:
        html = toast_html(request, message, level, undo_url)
    queue = f'<div id="playlist-queue" hx-swap-oob="innerHTML">{_queue(request)}</div>'
    return HttpResponse(html + queue)


# ----- the page --------------------------------------------------------------------


@require_GET
def page(request):
    return render(
        request,
        "ui/playlist/page.html",
        {
            "playlist": state.build(_event(request)),
            "can_race": allows(request, Perm.CONTROL_RACE),
            "back_url": back_url(request),
            "page_title": "Rounds Playlist",
        },
    )


@require_POST
@guard
def racing_scene(request):
    """The racing scene select (`settings.write racing_scene`): the scene
    kiosks show while racing. -1 is none. The settings service checks the
    scene is the event's."""
    scene = _int(request.POST.get("racing_scene", ""))
    if scene is None:
        return service_error(request, "Pick a scene, or none.")
    result = apply_form(_event(request), {**SETTINGS_WRITE, "racing_scene": str(scene)})
    if result.failure:
        return service_error(request, "That scene is gone. Reload the page.")
    return changed(request, "Racing scene saved.")


# ----- the entry sheet -------------------------------------------------------------


def _sheet(request, context: dict) -> HttpResponse:
    found = context.pop("playlist", None) or state.build(_event(request))
    return render(
        request,
        "ui/playlist/sheets/entry.html",
        {"playlist": found, **context},
    )


@require_http_methods(["GET", "POST"])
@guard
def add(request):
    """The entry sheet for a round from the list that can be added
    (`playlist.add`): its cut for a final, runs per lane, what happens
    after it, and whether it waits for the operator (D12)."""
    data = request.POST if request.method == "POST" else request.GET
    group_id = _int(data.get("group", ""))
    number = _int(data.get("round", ""))
    if group_id is None or number is None:
        return service_error(request, "Pick a round to add.")
    found = state.build(_event(request))
    row = next(
        (
            r
            for r in found.available
            if (r.group_id, r.round_number) == (group_id, number)
        ),
        None,
    )
    if request.method == "GET":
        if row is None:
            raise Http404
        return _sheet(
            request,
            {
                "playlist": found,
                "row": row,
                "values": {
                    "runs": 1,
                    "then": state.STOP,
                    "waits": found.default_waits,
                    "top": "",
                    "bucketed": False,
                },
                "post_url": reverse("playlist-add"),
            },
        )
    cut = request.POST.get("cut") == "1"
    try:
        choice = _choice(request.POST, cut=cut)
        step = playlist.queue_step(_event(request), group_id, number, choice)
    except (PlaylistFailure, ValueError) as failure:
        if row is None:
            return refused(request, failure)
        return _sheet(
            request,
            {
                "playlist": found,
                "row": row,
                "values": _posted(request.POST),
                "error": getattr(failure, "description", str(failure)),
                "post_url": reverse("playlist-add"),
            },
        )
    return changed(request, f"Added {playlist.queue_entry(step)['roundname']}.")


def _posted(post) -> dict:
    return {
        "runs": _int(post.get("runs", "")) or 1,
        "then": _int(post.get("then", "")) or 0,
        "waits": post.get("waits") == "1",
        "top": post.get("top", ""),
        "bucketed": post.get("bucketed") == "1",
    }


@require_http_methods(["GET", "POST"])
@guard
def step(request, entry_id):
    """The entry sheet of a queued step (`playlist.edit`): every field the
    add sheet has. The cut shows only while the round has no roster, as in
    `playlist.php`."""
    found = state.build(_event(request))
    row = next((e for e in found.entries if e.id == entry_id), None)
    if request.method == "GET":
        if row is None:
            raise Http404
        return _sheet(
            request,
            {
                "playlist": found,
                "row": row,
                "entry": row,
                "values": {
                    "runs": row.runs,
                    "then": row.then,
                    "waits": row.waits,
                    "top": row.bucket_limit or "",
                    "bucketed": row.bucketed,
                },
                "post_url": reverse("playlist-step", args=[entry_id]),
            },
        )
    try:
        choice = _choice(request.POST, cut=request.POST.get("cut") == "1")
        playlist.change_step(_event(request), entry_id, choice)
    except (PlaylistFailure, ValueError) as failure:
        if row is None:
            return refused(request, failure)
        return _sheet(
            request,
            {
                "playlist": found,
                "row": row,
                "entry": row,
                "values": _posted(request.POST),
                "error": getattr(failure, "description", str(failure)),
                "post_url": reverse("playlist-step", args=[entry_id]),
            },
        )
    return changed(request, f"Saved {row.name if row else 'the step'}.")


@require_POST
@guard
def waits(request, entry_id):
    """The "Wait for operator" switch on a step's card (D12)."""
    try:
        found = playlist.set_step_waits(
            _event(request), entry_id, request.POST.get("waits") == "1"
        )
    except PlaylistFailure as failure:
        return refused(request, failure)
    return changed(
        request,
        "The playlist waits for you before this round."
        if found.waits_for_operator
        else "This round starts on its own.",
    )


# ----- remove, Undo, reorder -------------------------------------------------------

RESTORE_FIELDS = (
    "group",
    "round",
    "position",
    "top",
    "bucketed",
    "runs",
    "scene",
    "continue",
    "waits",
)


@require_POST
@guard
def remove(request, entry_id):
    """Remove a step (`playlist.delete`), with Undo."""
    try:
        gone = playlist.remove_step(_event(request), entry_id)
    except PlaylistFailure as failure:
        return refused(request, failure)
    query = urlencode(
        {
            "group": gone.group_id,
            "round": gone.round_number,
            "position": gone.position,
            "top": gone.bucket_limit,
            "bucketed": int(gone.bucketed),
            "runs": gone.n_times_per_lane,
            "scene": gone.scene_id or 0,
            "continue": int(gone.continue_racing),
            "waits": int(gone.waits),
        }
    )
    return changed(
        request,
        "Step removed.",
        level="info",
        undo_url=f"{reverse('playlist-restore')}?{query}",
    )


@require_POST
@guard
def restore(request):
    """Undo a removal: the step comes back where it was."""
    data = request.GET
    values = {name: _int(data.get(name, "")) for name in RESTORE_FIELDS}
    if any(v is None for v in values.values()):
        return service_error(request, "That step cannot come back. Add it again.")
    try:
        playlist.restore_step(
            _event(request),
            RemovedStep(
                group_id=values["group"],
                round_number=values["round"],
                position=values["position"],
                bucket_limit=values["top"],
                bucketed=bool(values["bucketed"]),
                n_times_per_lane=values["runs"],
                scene_id=values["scene"] or None,
                continue_racing=bool(values["continue"]),
                waits=bool(values["waits"]),
            ),
        )
    except PlaylistFailure as failure:
        return refused(request, failure)
    return changed(request, "Step put back.")


@require_POST
@guard
def order(request):
    """The queue in a new order, from a drag or a row's Move up and Move
    down (`playlist.order`)."""
    ids = [_int(raw) for raw in request.POST.get("order", "").split(",")]
    if not ids or None in ids:
        return service_error(request, "That order names no steps.")
    try:
        playlist.reorder_steps(_event(request), ids)
    except PlaylistFailure as failure:
        return refused(request, failure)
    return changed(request, "Order saved.")


# ----- racing ----------------------------------------------------------------------


def _races(view):
    """Starting racing needs CONTROL_RACE too (S18), as on the dashboard."""
    return page_requires(Perm.CONTROL_RACE)(view)


@require_POST
@guard
@_races
def start(request):
    """Start Playlist: the service the dashboard's button calls (#41)."""
    round = playlist.start_playlist(_event(request), rounds.playlist_finalists())
    if round is None:
        return service_error(request, "Every round in the playlist has been raced.")
    return changed(request, "Playlist started.")


@require_POST
@guard
@_races
def start_next(request):
    """Start the round the intermission waits on (D12), as the dashboard's
    Start next round does."""
    try:
        playlist.start_waiting_step(_event(request))
    except PlaylistFailure as failure:
        return refused(request, failure)
    return changed(request, "Racing started.")


# ----- the scheduling partial ------------------------------------------------------


def _scheduling(request, *, error="") -> HttpResponse:
    return render(
        request,
        "ui/playlist/scheduling.html",
        {"scheduling": state.scheduling(_event(request)), "error": error},
    )


@require_GET
@guard
def scheduling(request):
    """The scheduling method and the lanes in use, a partial this page and
    the dashboard's Schedule sheet open, and the settings page embeds (#48)."""
    return _scheduling(request)


@require_POST
@guard
def scheduling_method(request):
    """Normal, abbreviated or rotation, as `max-runs-per-car` and
    `rotation-schedule` (`settings.php`)."""
    chosen = METHOD_SETTINGS.get(request.POST.get("method", ""))
    if chosen is None:
        return _scheduling(request, error="Pick a scheduling method.")
    result = apply_form(_event(request), {**SETTINGS_WRITE, **chosen})
    return _scheduling(request, error=result.failure[1] if result.failure else "")


@require_POST
@guard
def scheduling_lanes(request):
    """The lanes left out of new schedules, as `unused-lane-mask`: lanes 1
    to `lanes`, the lane count the partial showed, that are not checked as
    in use. While unraced heats exist the settings service refuses the
    change (`already-scheduled`), and the partial shows the refusal beside
    the lanes as they were."""
    count = _int(request.POST.get("lanes", ""))
    used = {_int(raw) for raw in request.POST.getlist("used")}
    if count is None or not 0 <= count <= MAX_LANES or None in used:
        return _scheduling(request, error="Pick lanes the track has.")
    mask = sum(1 << (n - 1) for n in range(1, count + 1) if n not in used)
    result = apply_form(
        _event(request), {**SETTINGS_WRITE, "unused-lane-mask": str(mask)}
    )
    return _scheduling(request, error=result.failure[1] if result.failure else "")
