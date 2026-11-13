"""Race check-in (#42): one phone-first page for `checkin.php` and
`mcheckin.php`, over the roster, racing and media services.

The card list polls every 10 s and answers 204 while its signature holds, so
several stations stay in sync. Every POST answers with a toast for the toast
region and an `HX-Trigger: checkin-refresh` header, which makes the list
fetch itself again at once. Controls inside the page's one sheet answer with
the sheet's next body instead: the add sheet goes on to the photo and the
pass, a pass during a running round asks the D36 question, and an upload goes
on to the crop sheet. A refusal is a 422 error toast. No page or sheet GET
writes anything.
"""

import json
import re

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import get_script_prefix, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import Perm, allows, granted, role_allows
from accounts.views import MCHECKIN, grant_mcheckin_token, make_mcheckin_token
from awards.editing import toggle_xbs_award
from media import photos
from racing.engine.phpcompat import php_numeric, php_truthy
from racing.models import RosterEntry
from racing.services import finals
from racing.services.finals import FinalsFailure
from racing.services.reschedule import RescheduleFailure
from roster.models import Partition, Racer
from roster.services import car_numbers, checkin, groups, racers, weigh_in
from roster.services.car_numbers import RangeFailure
from roster.services.checkin import CheckInFailure
from roster.services.racers import Bulk, Edit, Failure, RacerFailure
from roster.services.weigh_in import WeighInFailure
from ui.checkin import state
from ui.checkin.state import Filters
from ui.navigation import requires_page
from ui.responses import active_event as _event
from ui.responses import (
    closing_sheet,
    in_sheet,
    into_sheet,
    name_of,
    refused,
    service_error,
    toast_html,
)

guard = requires_page("checkin")

REPOS = {"head": "Racer photo", "car": "Car photo"}
WHOLE_NUMBER = re.compile(r"[0-9]{1,9}")
ROTATIONS = {"left": 90, "right": 270}


def _racer(request, racer_id) -> Racer:
    return get_object_or_404(
        Racer.objects.select_related("group", "partition"),
        pk=racer_id,
        event=_event(request),
    )


def _toast(request, message: str, level: str = "ok", undo_url: str = "") -> str:
    return toast_html(request, message, level, undo_url)


def _reply(request, body: str, *, refresh=True) -> HttpResponse:
    response = HttpResponse(body)
    if refresh:
        response["HX-Trigger"] = json.dumps({"checkin-refresh": True})
    return response


def toasted(request, message: str, *, level="ok", undo_url="", refresh=True):
    """A reply for a control on the page: a toast for #toasts."""
    return _reply(request, _toast(request, message, level, undo_url), refresh=refresh)


SHEET_BODY = "checkin-sheet-body"


def _in_sheet(request) -> bool:
    return in_sheet(request, SHEET_BODY)


def sheet_reply(request, sheet_html: str, message: str = "", *, refresh=True):
    """The sheet's next body, with `message` at its top, where no toast
    covers the sheet's buttons. A control in the sheet swaps it in; a
    control on the page, whose reply goes to #toasts, gets it out of band,
    as the D36 question after a card's Passed switch."""
    if message:
        sheet_html = (
            render_to_string("ui/checkin/notice.html", {"message": message}, request)
            + sheet_html
        )
    return _reply(request, into_sheet(request, SHEET_BODY, sheet_html), refresh=refresh)


def closed_reply(request, message: str):
    """Close the sheet, and say what happened in a toast."""
    return _reply(request, closing_sheet(request, SHEET_BODY, message))


def _need(request, perm: Perm, message: str):
    if not allows(request, perm):
        return service_error(request, message)
    return None


def _perms(request) -> dict:
    return {
        "can_add": allows(request, Perm.REGISTER_NEW_RACER),
        "can_edit": allows(request, Perm.EDIT_RACER),
        "can_revert": allows(request, Perm.REVERT_CHECK_IN),
        "can_photo": allows(request, Perm.PHOTO_UPLOAD),
        "can_crop": allows(request, Perm.ASSIGN_RACER_IMAGE),
        "can_set_up": allows(request, Perm.SET_UP),
        "can_qr": all(allows(request, bit) for bit in MCHECKIN),
        # A logged-in crew role's own bits, not the check-in link's grant
        # (#206, D5): see voting_code().
        "can_voting_code": role_allows(request, Perm.CHECK_IN_RACERS),
        "can_edit_partitions": allows(request, Perm.SET_UP),
    }


# ----- the page and its poll -----


def accept_token(request):
    """The QR code's link, `/checkin/?tok=...` (#16): a valid token grants
    check-in, un-pass and photo upload for this session, and the page opens
    without the token in its URL. Runs before the page's guard."""
    if request.method == "GET" and "tok" in request.GET:
        if grant_mcheckin_token(request):
            return redirect("checkin")
    return None


def _list_context(request, listing, filters) -> dict:
    return {
        "listing": listing,
        "filters": filters,
        **_perms(request),
    }


@require_GET
def page(request):
    filters = Filters.from_request(request.GET)
    listing = state.build(_event(request), filters)
    return render(
        request,
        "ui/checkin/page.html",
        {
            **_list_context(request, listing, filters),
            "orders": state.ORDERS,
            "statuses": state.STATUSES,
            "states": state.STATES,
            "weighs": state.WEIGHS,
            "poll_seconds": state.POLL_SECONDS,
            "page_title": "Check-In",
        },
    )


@require_GET
@guard
def listing(request):
    """The 10 s poll and the filter bar's fetch. 204 while the signature the
    page sent still matches."""
    filters = Filters.from_request(request.GET)
    found = state.build(_event(request), filters)
    if request.GET.get("sig") == found.signature:
        return HttpResponse(status=204)
    return render(
        request,
        "ui/checkin/list.html",
        {**_list_context(request, found, filters), "poll_seconds": state.POLL_SECONDS},
    )


# ----- pass, un-pass, the D36 question, the D32 request ------------------------------


def _question(request, racer: Racer):
    """The D36 question's sheet body, when a round of the racer's group is
    running without the car and nobody answered No yet. Read after the pass,
    and the answer's service checks it again under the lock."""
    round = checkin.round_in_progress_for(racer)
    if round is None:
        return None
    if RosterEntry.objects.filter(
        round=round, racer=racer, left_out_at__isnull=False
    ).exists():
        return None
    return render_to_string(
        "ui/checkin/sheets/question.html",
        {
            "racer": racer,
            "car": state.car(racer.car_number),
            "round_name": f"{round.group.name}, Round {round.number}",
        },
        request,
    )


def _sheet_racer(request, racer: Racer, notice: str = "", warnings=()) -> str:
    return render_to_string(
        "ui/checkin/sheets/racer.html",
        _racer_sheet_context(request, racer, notice=notice, warnings=warnings),
        request,
    )


@require_POST
@guard
def pass_racer(request, racer_id):
    """The Passed switch, posting `passed=1` or `passed=0`, and the Undo of
    an un-pass, which posts to `?passed=1`. A pass is the end of check-in
    (D33). An un-pass needs REVERT_CHECK_IN and offers Undo instead of
    PHP's `confirm()`."""
    racer = _racer(request, racer_id)
    value = request.POST.get("passed", request.GET.get("passed", "1"))
    passed = php_truthy(value)
    if not passed and (
        no := _need(
            request,
            Perm.REVERT_CHECK_IN,
            "Only the race coordinator can undo a check-in.",
        )
    ):
        return no
    try:
        racers.pass_racer(racer, passed)
    except RacerFailure as failure:
        return refused(request, failure)
    from_sheet = _in_sheet(request)
    if not passed:
        undo = reverse("checkin-pass", args=[racer.pk]) + "?passed=1&undo=1"
        message = f"{name_of(racer)} is not passed."
        if from_sheet:
            return sheet_reply(request, _sheet_racer(request, racer), message)
        return toasted(request, message, level="info", undo_url=undo)
    message = f"{state.car(racer.car_number)} {name_of(racer)} passed."
    # Undo restores the pass and asks nothing; the card still offers the
    # question when it was never answered.
    undo = request.GET.get("undo") == "1"
    question = None if undo else _question(request, racer)
    if question is not None:
        return sheet_reply(request, question, message)
    if from_sheet:
        return sheet_reply(request, _sheet_racer(request, racer), message)
    return toasted(request, message)


def _posted_pass(value) -> bool | None:
    """Pass ("1"), Fail ("0"), or nothing chosen."""
    if value in (None, ""):
        return None
    return php_truthy(value)


def _posted_weight(value) -> str | None:
    value = (value or "").strip()
    return value or None


@require_POST
@guard
def weigh(request, racer_id):
    """The weigh-in step (#70, D8, D54): Pass or Fail in a `pass_fail`
    partition, the weight in the partition's unit in a `recorded` one. A
    weight over the maximum saves, with its warning (D8). PHP has no
    weigh-in."""
    racer = _racer(request, racer_id)
    try:
        weighed = weigh_in.record_weigh_in(
            racer,
            passed=_posted_pass(request.POST.get("passed")),
            weight=_posted_weight(request.POST.get("weight")),
        )
    except WeighInFailure as failure:
        return refused(request, failure)
    who = f"{state.car(racer.car_number)} {name_of(racer)}"
    if weighed.weight:
        message = f"{who} weighs {weighed.weight}."
    else:
        message = f"{who}: weigh-in {'passed' if weighed.passed else 'failed'}."
    warnings = [weighed.warning] if weighed.warning else []
    if _in_sheet(request):
        return sheet_reply(
            request, _sheet_racer(request, racer, warnings=warnings), message
        )
    if weighed.warning:
        return toasted(request, weighed.warning, level="info")
    return toasted(request, message, level="ok" if weighed.passed else "info")


@require_GET
@guard
def weigh_fields(request):
    """The add sheet's weigh-in fields for the class picked (#70)."""
    partition = _partition(request, request.GET.get("partitionid"))
    return render(
        request,
        "ui/checkin/weigh_fields.html",
        {"weigh_setting": weigh_in.setting(partition), "values": {}},
    )


@require_GET
@guard
def question_sheet(request, racer_id):
    """The D36 question again, from a card whose racer passed while the
    group's round was running and nobody answered."""
    racer = _racer(request, racer_id)
    question = _question(request, racer)
    if question is None:
        return render(request, "ui/components/sheet_closed.html")
    return HttpResponse(question)


@require_POST
@guard
def answer(request, racer_id, choice):
    """Yes or No to "<round> is in progress. Add this car to the current
    round?" (D36). The services hold the rule: the race crew or the race
    coordinator answers, and after a No only the race coordinator adds."""
    racer = _racer(request, racer_id)
    try:
        if choice == "yes":
            added = checkin.add_to_round_in_progress(racer, granted(request))
            round = added.round
            message = (
                f"{state.car(racer.car_number)} {name_of(racer)} added to"
                f" {round.group.name}, Round {round.number}."
            )
        elif choice == "no":
            round = checkin.leave_out_of_round_in_progress(racer, granted(request))
            message = (
                f"{name_of(racer)} stays out of {round.group.name}, Round"
                f" {round.number}. The race coordinator can still add the car."
            )
        else:
            raise Http404
    except (CheckInFailure, RescheduleFailure) as failure:
        return refused(request, failure)
    return toasted(request, message)


@require_POST
@guard
def final_request(request, racer_id):
    """ "Request place in final" (D32): only the request. The race
    coordinator allows or declines it on the dashboard."""
    racer = _racer(request, racer_id)
    try:
        finals.request_final_place(racer)
    except FinalsFailure as failure:
        return refused(request, failure)
    return toasted(
        request,
        f"{name_of(racer)} asked for a place in the final."
        " The race coordinator decides.",
    )


@require_POST
@guard
def xbs(request, racer_id):
    racer = _racer(request, racer_id)
    on = php_truthy(request.POST.get("xbs", ""))
    toggle_xbs_award(_event(request), racer, on)
    return toasted(
        request, f"{name_of(racer)}: Exclusively By Scout {'on' if on else 'off'}."
    )


# ----- the racer sheet -----


def _number_text(posted: str) -> str | None:
    """A posted car number as the plain number (D47), "" for blank, or None
    for anything but a whole number."""
    posted = posted.strip()
    if posted == "":
        return ""
    if not WHOLE_NUMBER.fullmatch(posted):
        return None
    return str(int(posted))


def _partition(request, posted) -> Partition | None:
    return groups.partition_of(_event(request).id, posted)


def _racer_sheet_context(
    request, racer: Racer | None, *, values=None, notice="", warnings=(), error=""
) -> dict:
    event = _event(request)
    partitions = state.partitions(event)
    if racer is not None:
        values = values or {
            "firstname": racer.first_name,
            "lastname": racer.last_name,
            "carname": racer.car_name,
            "note_from": racer.note,
            "partitionid": racer.partition_id,
            "carno": state.car(racer.car_number),
            "exclude": racer.exclude,
            "no_speed": racer.not_eligible_for_speed_awards,
        }
    elif values is None:
        first = groups.first_partition(event.id)
        suggested = car_numbers.suggested_number(event.id, first)
        values = {
            "partitionid": first.pk if first else "",
            "carno": state.car(suggested) if suggested is not None else "",
        }
    partition = _partition(request, values.get("partitionid"))
    context = {
        "racer": racer,
        "card": state.card(event, racer.pk) if racer else None,
        "values": values,
        "partitions": partitions,
        "notice": notice,
        "warnings": list(warnings),
        "error": error,
        "range_full": car_numbers.ranges_full(partition),
        "range_partition": partition,
        "weigh_setting": weigh_in.setting(partition),
        **_perms(request),
    }
    return context


def _racer_values(post) -> dict:
    return {
        "firstname": post.get("firstname", ""),
        "lastname": post.get("lastname", ""),
        "carname": post.get("carname", ""),
        "note_from": post.get("note_from", ""),
        "partitionid": post.get("partitionid", ""),
        "carno": post.get("carno", ""),
        "exclude": php_truthy(post.get("exclude", "")),
        "no_speed": php_truthy(post.get("no_speed", "")),
        "weigh_passed": post.get("weigh_passed", ""),
        "weight": post.get("weight", ""),
    }


def _weigh_entry(values) -> weigh_in.Entry | None:
    """The add sheet's weigh-in, or None when the volunteer left it for
    later."""
    passed = _posted_pass(values["weigh_passed"])
    weight = _posted_weight(values["weight"])
    if passed is None and weight is None:
        return None
    return weigh_in.Entry(passed=passed, weight=weight)


def _sheet_error(request, racer, values, error: str):
    """The sheet again with the volunteer's values and the refusal in it,
    status 200 so it swaps into the sheet."""
    return render(
        request,
        "ui/checkin/sheets/racer.html",
        _racer_sheet_context(request, racer, values=values, error=error),
    )


@require_http_methods(["GET", "POST"])
@guard
def racer_new(request):
    """The add sheet (GET) and the new racer (POST): name, car name and class
    first, the car number from the class's range (D6, D48), the weigh-in the
    class asks for (#70, D8, D54), then Take car photo and Pass. A duplicate
    or out-of-range number, and a weight over the maximum, is saved with its
    warning on the sheet, which stays open for a fix."""
    if no := _need(request, Perm.REGISTER_NEW_RACER, "Your role cannot add racers."):
        return no
    if request.method == "GET":
        return render(
            request,
            "ui/checkin/sheets/racer.html",
            _racer_sheet_context(request, None),
        )
    event = _event(request)
    values = _racer_values(request.POST)
    number = _number_text(values["carno"])
    if number is None:
        return _sheet_error(
            request, None, values, "The car number must be a whole number."
        )
    if not values["firstname"].strip() and not values["lastname"].strip():
        return _sheet_error(request, None, values, "Enter the racer's name.")
    posted_partition = values["partitionid"]
    partition = _partition(request, posted_partition)
    if partition is None and groups.first_partition(event.id) is not None:
        return _sheet_error(request, None, values, "Pick the racer's class.")
    try:
        new = racers.insert_new_racer(
            event,
            first_name=values["firstname"],
            last_name=values["lastname"],
            car_number=number,
            car_name=values["carname"],
            note=values["note_from"],
            partition=partition,
            exclude=values["exclude"],
            no_speed_awards=values["no_speed"],
            weigh_in=_weigh_entry(values),
        )
    except RacerFailure as failure:
        if failure.code == Failure.RANGE_FULL:
            values["carno"] = ""
        return _sheet_error(request, None, values, failure.description)
    except WeighInFailure as failure:
        return _sheet_error(request, None, values, failure.description)
    racer = Racer.objects.select_related("group", "partition").get(pk=new.racer.pk)
    return sheet_reply(
        request,
        _sheet_racer(
            request,
            racer,
            notice=f"Added {state.car(racer.car_number)} {name_of(racer)}.",
            warnings=new.warnings,
        ),
    )


@require_http_methods(["GET", "POST"])
@guard
def racer_edit(request, racer_id):
    """The edit sheet (GET) and its save (POST): the same seven fields as
    PHP's dialog, a partition picked from the existing ones (D34), and both
    award settings (D33)."""
    racer = _racer(request, racer_id)
    if request.method == "GET":
        return render(
            request,
            "ui/checkin/sheets/racer.html",
            _racer_sheet_context(request, racer),
        )
    if no := _need(request, Perm.EDIT_RACER, "Your role cannot edit racers."):
        return no
    values = _racer_values(request.POST)
    number = _number_text(values["carno"])
    if not number:
        return _sheet_error(
            request, racer, values, "The car number must be a whole number."
        )
    partition = _partition(request, values["partitionid"])
    if partition is None:
        return _sheet_error(request, racer, values, "Pick the racer's class.")
    edit = Edit(
        partition=partition,
        first_name=values["firstname"],
        last_name=values["lastname"],
        car_number=number,
        car_name=values["carname"],
        note=values["note_from"],
        exclude=values["exclude"],
        no_speed_awards=values["no_speed"],
    )
    try:
        result = racers.edit_racer(racer, edit)
    except RacerFailure as failure:
        return _sheet_error(request, racer, values, failure.description)
    racer = Racer.objects.select_related("group", "partition").get(pk=racer.pk)
    warnings = car_numbers.number_warnings(
        racer.event_id, racer.partition, racer.car_number, racer.pk
    )
    if result.warning and not warnings:
        warnings = [w for w in result.warning if w.strip()]
    if warnings:
        return sheet_reply(
            request, _sheet_racer(request, racer, notice="Saved.", warnings=warnings)
        )
    return closed_reply(
        request, f"Saved {state.car(racer.car_number)} {name_of(racer)}."
    )


@require_GET
@guard
def number(request):
    """The car number's hint as the volunteer types or picks the class: the
    D48 warnings, the range-full form, and, while the volunteer has not typed
    a number on the add sheet, the class's next number (refreshed every
    10 s, so two stations rarely suggest the same one)."""
    event = _event(request)
    partition = _partition(request, request.GET.get("partitionid"))
    racer = None
    try:
        racer = Racer.objects.filter(
            event=event, pk=int(request.GET.get("racer", ""))
        ).first()
    except ValueError:
        pass
    fill = racer is None and request.GET.get("touched", "") != "1"
    suggested = car_numbers.suggested_number(event.id, partition) if fill else None
    typed = _number_text(request.GET.get("carno", ""))
    if fill:
        typed = str(suggested) if suggested is not None else ""
    shown = int(typed) if typed else None
    warnings = car_numbers.number_warnings(
        event.id, partition, shown, racer.pk if racer else None
    )
    if typed is None:
        warnings = ["The car number must be a whole number."]
    context = {
        "warnings": warnings,
        "fill": fill,
        "carno": state.car(suggested) if suggested is not None else "",
        "range_full": car_numbers.ranges_full(partition),
        "range_partition": partition,
        **_perms(request),
    }
    return render(request, "ui/checkin/number.html", context)


@require_POST
@guard
def add_range(request, partition_id):
    """D48: when every range of a partition is full, a SET_UP login adds
    another, through the car-range service of #123."""
    if no := _need(
        request,
        Perm.SET_UP,
        "Every car number range is full. Ask the race coordinator for another range.",
    ):
        return no
    partition = get_object_or_404(Partition, pk=partition_id, event=_event(request))
    first = _number_text(request.POST.get("range_first", ""))
    last = _number_text(request.POST.get("range_last", ""))
    if not first or not last:
        return service_error(request, "A range needs a first and a last whole number.")
    try:
        car_numbers.add_range(partition, int(first), int(last))
    except RangeFailure as failure:
        return refused(request, failure)
    values = _racer_values(request.POST)
    suggested = car_numbers.suggested_number(partition.event_id, partition)
    values["carno"] = state.car(suggested) if suggested is not None else ""
    return sheet_reply(
        request,
        render_to_string(
            "ui/checkin/sheets/racer.html",
            _racer_sheet_context(request, None, values=values),
            request,
        ),
        f"{partition.name} numbers {first} to {last} added.",
    )


@require_http_methods(["GET", "POST"])
@guard
def racer_delete(request, racer_id):
    """The confirm sheet (GET) and the delete (POST). The service refuses a
    racer with heats or in more than one round."""
    racer = _racer(request, racer_id)
    if no := _need(request, Perm.EDIT_RACER, "Your role cannot delete racers."):
        return no
    if request.method == "GET":
        return render(
            request,
            "ui/checkin/sheets/delete.html",
            {"racer": racer, "car": state.car(racer.car_number)},
        )
    try:
        racers.delete_racer(_event(request), racer, str(racer.pk))
    except RacerFailure as failure:
        return refused(request, failure)
    return closed_reply(
        request, f"Deleted {state.car(racer.car_number)} {name_of(racer)}."
    )


# ----- bulk -----

BULK_ACTIONS = (
    ("checkin", "Check in"),
    ("uncheckin", "Undo check-in"),
    ("number", "Renumber"),
    ("eligibility", "Award eligibility"),
)


@require_http_methods(["GET", "POST"])
@guard
def bulk(request):
    """Check in, undo check-in, renumber (standard or from a start), or set
    award eligibility, for everyone or one partition or group, as PHP's
    `racer.bulk`. Eligibility sets the legacy `exclude` only (D33)."""
    if no := _need(request, Perm.EDIT_RACER, "Your role cannot change racers in bulk."):
        return no
    event = _event(request)
    if request.method == "GET":
        return render(
            request,
            "ui/checkin/sheets/bulk.html",
            {
                "choices": racers.bulk_choices(event),
                "actions": BULK_ACTIONS,
                **_perms(request),
            },
        )
    who = request.POST.get("who", "all")
    action = request.POST.get("what", "")
    value, what, start, auto = "1", action, "", False
    match action:
        case "checkin":
            pass
        case "uncheckin":
            what, value = "checkin", "0"
        case "number":
            auto = request.POST.get("numbering", "standard") != "custom"
            start = request.POST.get("start", "")
            if not auto and not WHOLE_NUMBER.fullmatch(start.strip()):
                return service_error(request, "Enter the first car number.")
        case "eligibility":
            value = "1" if request.POST.get("eligible", "1") == "1" else "0"
        case _:
            return service_error(request, "Pick what to change.")
    if who not in dict(racers.bulk_choices(event)):
        return service_error(request, "Pick which racers to change.")
    try:
        racers.apply_bulk(
            event,
            Bulk(
                who=who,
                what=what,
                value=value,
                start=start.strip(),
                auto=auto,
                may_revert=allows(request, Perm.REVERT_CHECK_IN),
            ),
        )
    except RacerFailure as failure:
        return refused(request, failure)
    label = dict(BULK_ACTIONS)[action]
    return closed_reply(
        request, f"{label}: done for {dict(racers.bulk_choices(event))[who]}."
    )


# ----- photos -----


def _repo(request, repo: str) -> photos.Repository:
    if repo not in REPOS:
        raise Http404
    found = photos.repository(_event(request), repo)
    assert found is not None
    return found


@require_GET
@guard
def photo_sheet(request, racer_id, repo):
    """The photo sheet: the file input with `capture` is the main path, so it
    works over plain HTTP; a secure context also offers the live camera."""
    racer = _racer(request, racer_id)
    _repo(request, repo)
    has_photo = bool(racer.car_photo if repo == "car" else racer.headshot)
    return render(
        request,
        "ui/checkin/sheets/photo.html",
        {
            "repo": repo,
            "title": f"{REPOS[repo]} · {state.car(racer.car_number)} {name_of(racer)}",
            "upload_url": reverse("checkin-upload", args=[racer.pk, repo]),
            "crop_url": reverse("checkin-crop-sheet", args=[racer.pk, repo])
            if has_photo and allows(request, Perm.ASSIGN_RACER_IMAGE)
            else "",
        },
    )


def _crop_context(request, racer: Racer, repository: photos.Repository, name: str):
    repo = repository.name
    return {
        "repo": repo,
        "title": f"Crop {REPOS[repo].lower()}",
        "alt": f"{REPOS[repo]} of {name_of(racer)}",
        "work_url": get_script_prefix()
        + photos.page_url(repository, name, photos.WORK),
        "crop_url": reverse("checkin-crop", args=[racer.pk, repo]),
        "rotate_left_url": reverse("checkin-rotate", args=[racer.pk, repo, "left"]),
        "rotate_right_url": reverse("checkin-rotate", args=[racer.pk, repo, "right"]),
    }


def _crop_sheet(request, racer, repository, name) -> str:
    return render_to_string(
        "ui/checkin/sheets/crop.html",
        _crop_context(request, racer, repository, name),
        request,
    )


@require_POST
@guard
def photo_upload(request, racer_id, repo):
    """The photo the browser downscaled, for the racer, with or without
    autocrop. "Capture and check in" passes the racer too. Then the crop
    sheet, when the role may crop."""
    if no := _need(request, Perm.PHOTO_UPLOAD, "Your role cannot upload photos."):
        return no
    racer = _racer(request, racer_id)
    repository = _repo(request, repo)
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
    message = f"{REPOS[repo]} saved for {name_of(racer)}."
    if result.warning:
        message += f" {result.warning}"
    if php_truthy(request.POST.get("checkin", "")):
        try:
            racers.pass_racer(racer, True)
        except RacerFailure as failure:
            return refused(request, failure)
        message += " Passed."
        question = _question(request, racer)
        if question is not None:
            return sheet_reply(request, question, message)
    if allows(request, Perm.ASSIGN_RACER_IMAGE):
        return sheet_reply(
            request, _crop_sheet(request, racer, repository, result.uploaded), message
        )
    return closed_reply(request, message)


def _stored_name(racer: Racer, repo: str) -> str:
    path = racer.car_photo if repo == "car" else racer.headshot
    return path.rsplit("/", 1)[-1]


@require_GET
@guard
def crop_sheet(request, racer_id, repo):
    """The crop sheet for the racer's photo: Cropper.js at the 3:4 autocrop
    ratio, with rotate buttons. #47 reuses it."""
    racer = _racer(request, racer_id)
    repository = _repo(request, repo)
    name = _stored_name(racer, repo)
    if not name:
        raise Http404("The racer has no photo.")
    return HttpResponse(_crop_sheet(request, racer, repository, name))


CROP_FIELDS = ("left", "top", "right", "bottom", "original_width", "original_height")


@require_POST
@guard
def crop(request, racer_id, repo):
    if no := _need(request, Perm.ASSIGN_RACER_IMAGE, "Your role cannot crop photos."):
        return no
    racer = _racer(request, racer_id)
    repository = _repo(request, repo)
    values = [php_numeric(request.POST.get(name, "")) for name in CROP_FIELDS]
    if any(v is None or v != v or v in (float("inf"), float("-inf")) for v in values):
        return service_error(request, "Draw the crop box first.")
    try:
        photos.crop_photo(
            _event(request),
            repository,
            _stored_name(racer, repo),
            photos.CropBox(*values),
        )
    except photos.Refused as failure:
        return refused(request, failure)
    return closed_reply(request, f"{REPOS[repo]} cropped.")


@require_POST
@guard
def rotate(request, racer_id, repo, direction):
    if no := _need(request, Perm.ASSIGN_RACER_IMAGE, "Your role cannot turn photos."):
        return no
    racer = _racer(request, racer_id)
    repository = _repo(request, repo)
    if direction not in ROTATIONS:
        raise Http404
    name = _stored_name(racer, repo)
    try:
        photos.rotate_photo(_event(request), repository, name, ROTATIONS[direction])
    except photos.Refused as failure:
        return refused(request, failure)
    return sheet_reply(request, _crop_sheet(request, racer, repository, name))


# ----- a racer's voting code ---------------------------------------------------------


@require_GET
@guard
def voting_code(request, racer_id):
    """The racer's voting code (#206, D86): a QR code of the vote link that
    holds the racer's voting token, beside the check-in link's QR code. The
    token is read here only, never in the card or its poll. The sheet is
    for a logged-in crew role that checks racers in (#206, D5, D86); a
    phone holding only the check-in link's grant (#16) does not see it."""
    if not role_allows(request, Perm.CHECK_IN_RACERS):
        return service_error(
            request, "Only a logged-in role that checks racers in sees voting codes."
        )
    racer = _racer(request, racer_id)
    url = request.build_absolute_uri(
        reverse("vote-code", kwargs={"token": str(racer.voting_token)})
    )
    return render(
        request,
        "ui/checkin/sheets/voting_code.html",
        {"racer": racer, "car": state.car(racer.car_number), "url": url},
    )


# ----- the QR code for other phones --------------------------------------------------


@require_GET
@guard
def qr_sheet(request):
    """A QR code that opens this page on another phone with a signed token
    good for 60 minutes, which grants only check-in, un-pass and photo
    upload (#16). Only a role that holds all three may hand them out."""
    if not all(allows(request, bit) for bit in MCHECKIN):
        return service_error(request, "Your role cannot hand out check-in.")
    url = (
        request.build_absolute_uri(reverse("checkin")) + "?tok=" + make_mcheckin_token()
    )
    return render(request, "ui/checkin/sheets/qr.html", {"url": url})
