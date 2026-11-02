"""The race dashboard (#41): the page, its 2 s poll, its sheets, and every
control, over the racing services.

Every POST answers with the live region re-rendered out of band, so the
page needs no GET after it (#39 htmx pattern 4), and with a toast where one
helps. A refusal is a 422 error toast (`ui.responses.service_error`). The
page and the poll never write the session.
"""

from urllib.parse import urlencode

from django.db import transaction
from django.http import Http404, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import Perm, allows
from core import settings_form
from core.models import RaceSettings, SiteState
from racing.engine.phpcompat import php_truthy
from racing.models import FinalRequest, HeatLane, Round
from racing.services import (
    finals,
    heats,
    lanes,
    playlist,
    purge,
    replay,
    reschedule,
    results,
    rounds,
    schedule,
    timer,
)
from racing.services.finals import FinalsFailure
from racing.services.lanes import LanesFailure
from racing.services.playlist import PlaylistFailure
from racing.services.reschedule import RescheduleFailure
from racing.services.rounds import (
    FollowOn,
    FromAggregate,
    NewAggregate,
    RoundFailure,
)
from racing.services.schedule import ScheduleFailure
from roster.models import Group, Racer, Subgroup
from roster.services.groups import labels
from roster.services.racers import set_award_settings
from ui.dashboard import presenter
from ui.dashboard.state import Shown, build, racers_of_heat
from ui.navigation import requires_page
from ui.responses import service_error

guard = requires_page("dashboard")

RUNS_PER_LANE = range(1, 7)
REPLAY_CHOICES = {
    "replay-skipback": [
        ("2500", "2.5"),
        ("3000", "3.0"),
        ("3500", "3.5"),
        ("4000", "4.0"),
        ("4500", "4.5"),
        ("5000", "5.0"),
        ("6000", "6.0"),
    ],
    "replay-num-showings": [("1", "1"), ("2", "2"), ("3", "3")],
    "replay-rate": [
        ("10", "0.1x"),
        ("25", "0.25x"),
        ("50", "0.5x"),
        ("75", "0.75x"),
        ("100", "1x"),
    ],
}


def _event(request):
    """The open event. A new install with no event has no race to run."""
    found = getattr(request, "_dashboard_event", None)
    if found is None:
        state = SiteState.objects.select_related("active_event").filter(pk=1).first()
        if state is None or state.active_event is None:
            raise Http404("No event is open.")
        found = request._dashboard_event = state.active_event
    return found


def _live_html(request, live, *, oob: bool) -> str:
    return render_to_string(
        "ui/dashboard/live.html", {**live.context, "oob": oob}, request
    )


def _toast(request, message: str, level: str = "ok", undo_url: str = "") -> str:
    return render_to_string(
        "ui/components/toast.html",
        {"message": message, "level": level, "undo_url": undo_url},
        request,
    )


def _notices(request, notices) -> str:
    if not notices:
        return ""
    return render_to_string(
        "ui/dashboard/notices.html", {"notices": notices, "oob": True}, request
    )


def updated(request, message: str = "", *, level="ok", undo_url="", notices=()):
    """A mutation's reply: the live region out of band, then a toast for the
    toast region the request swaps into."""
    live = build(request, _event(request), Shown.from_request(request.POST))
    body = _live_html(request, live, oob=True) + _notices(request, notices)
    if message:
        body += _toast(request, message, level, undo_url)
    return HttpResponse(body)


def refused(request, failure) -> HttpResponse:
    return service_error(request, getattr(failure, "description", str(failure)))


def _hs_reply(request, failure, message: str = ""):
    if failure is not None:
        return refused(request, failure)
    return updated(request, message)


# ----- the page and its poll -------------------------------------------------------


@require_GET
def dashboard(request):
    live = build(request, _event(request), Shown(), poll=True)
    return render(
        request,
        "ui/dashboard/page.html",
        {**live.context, "page_title": "Race Dashboard"},
    )


@require_GET
@guard
def live(request):
    """The 2 s poll. 204 when the signature the page sent still matches, so
    htmx swaps nothing and no open control resets (#39 pattern 2)."""
    current = build(
        request, _event(request), Shown.from_request(request.GET), poll=True
    )
    if request.GET.get("sig") == current.signature:
        return HttpResponse(status=204)
    return HttpResponse(_live_html(request, current, oob=False))


# ----- the current heat -------------------------------------------------------------


@require_POST
@guard
def racing(request):
    on = php_truthy(request.POST.get("now_racing", ""))
    return _hs_reply(request, heats.set_racing(_event(request), on))


@require_POST
@guard
def heat(request, which):
    """Previous heat, next heat (or Skip for an emptied heat), and Next Up
    under the master schedule."""
    if which not in ("prev", "next", "next-up"):
        raise Http404
    racing_after = False if which == "next-up" else None
    failure = heats.select_heat(_event(request), heat=which, now_racing=racing_after)
    return _hs_reply(request, failure)


@require_POST
@guard
def rerun(request):
    """Re-Run in its four states (`data-rerun`): rerun the current heat or the
    last heat with a result, or reinstate the heat a rerun cleared."""
    event = _event(request)
    match request.POST.get("rerun"):
        case "current":
            return _hs_reply(
                request, heats.rerun(event, "current"), "Heat cleared to run again."
            )
        case "available":
            return _hs_reply(
                request,
                heats.rerun(event, "last"),
                "Previous heat cleared to run again.",
            )
        case "recoverable":
            return _hs_reply(request, heats.reinstate(event), "Heat reinstated.")
    return service_error(request, "There is no heat to run again.")


@require_POST
@guard
def remote_start(request):
    if not timer.request_remote_start(_event(request)):
        return service_error(request, "The timer does not offer a remote start now.")
    return updated(request, "Start sent to the timer.")


def _shown_heat(request) -> tuple[Round, int]:
    event = _event(request)
    try:
        heat_no = int(request.POST.get("heat") or request.GET.get("heat"))
        round_id = int(request.POST.get("round") or request.GET.get("round"))
    except TypeError, ValueError:
        raise Http404 from None
    return get_object_or_404(Round, pk=round_id, event=event), heat_no


@require_GET
@guard
def results_sheet(request):
    """The manual results sheet: one number per lane of the heat on screen.
    When the page does not watch a timer and the heat has no times yet,
    opening it triggers a replay, as PHP's Manual Results button does."""
    event = _event(request)
    round, heat_no = _shown_heat(request)
    settings = RaceSettings.objects.get(event=event)
    lanes_found = {t.lane: t for t in results.heat_times(event, round, heat_no)}
    rows = []
    racers = racers_of_heat(event, settings, round.pk, heat_no)
    for racer in racers:
        found = lanes_found.get(racer["lane"])
        rows.append(
            {
                "lane": racer["lane"],
                "car": presenter.car(racer["carnumber"]),
                "name": racer["name"],
                "time": "" if not found or found.time is None else f"{found.time:g}",
                "place": "" if not found or found.place is None else found.place,
            }
        )
    context = {
        "round": round,
        "heat": heat_no,
        "rows": rows,
        "use_points": settings.use_points,
        "any_results": bool(lanes_found),
        "trigger_replay": not settings.warn_no_timer and not lanes_found,
    }
    return render(request, "ui/dashboard/sheets/results.html", context)


@require_POST
@guard
def results_write(request):
    """Manual results for the heat on screen. A heat that moved meanwhile is
    refused with `wrong-heat` (D19)."""
    event = _event(request)
    round, heat_no = _shown_heat(request)
    fields = [
        (key, value)
        for key, value in request.POST.items()
        if key.startswith(("lane", "place"))
    ]
    recorded = results.record_manual_result(event, fields, expected=(round.pk, heat_no))
    if recorded.failure is not None:
        return refused(request, recorded.failure)
    return updated(request, "Results saved.")


@require_POST
@guard
def results_discard(request):
    """Discard the results of the heat on screen, named by its round and heat,
    never "current" (#41), with Undo."""
    event = _event(request)
    round, heat_no = _shown_heat(request)
    found = results.heat_times(event, round, heat_no)
    failure = results.delete_results(event, round, heat_no)
    if failure is not None:
        return refused(request, failure)
    query = [("round", round.pk), ("heat", heat_no)]
    for t in found:
        query += [
            (f"t{t.lane}", "" if t.time is None else repr(t.time)),
            (f"p{t.lane}", "" if t.place is None else t.place),
        ]
    undo = f"{reverse('dashboard-results-restore')}?{urlencode(query)}"
    return updated(
        request, f"Results of heat {heat_no} discarded.", level="info", undo_url=undo
    )


@require_POST
@guard
def results_restore(request):
    """Undo a discard: the times and places come back to lanes that have no
    result again."""
    event = _event(request)
    data = request.GET
    try:
        round = get_object_or_404(Round, pk=int(data["round"]), event=event)
        heat_no = int(data["heat"])
    except KeyError, ValueError:
        raise Http404 from None
    found = []
    for key in data:
        if key.startswith("t") and key[1:].isdigit():
            lane = int(key[1:])
            time_text, place_text = data.get(key, ""), data.get(f"p{lane}", "")
            found.append(
                results.LaneTimes(
                    lane,
                    float(time_text) if time_text else None,
                    int(place_text) if place_text else None,
                )
            )
    failure = results.restore_results(event, round, heat_no, found)
    if failure is not None:
        return refused(request, failure)
    return updated(request, f"Results of heat {heat_no} restored.")


# ----- timer, replay, simulated racing ---------------------------------------------


@require_POST
@guard
def replay_trigger(request):
    replay.trigger(_event(request).id)
    return HttpResponse(status=204)


@require_POST
@guard
def replay_test(request):
    """Trigger Replay: PHP's button sends `replay.test`."""
    replay.send_test(_event(request).id)
    return updated(request, "Replay triggered.")


def _need_set_up(request):
    if not allows(request, Perm.SET_UP):
        return service_error(
            request, "Only a role that can set up the race can change this."
        )
    return None


@require_GET
@guard
def replay_settings_sheet(request):
    settings = RaceSettings.objects.get(event=_event(request))
    current = {
        "replay-skipback": str(settings.replay_skipback_ms),
        "replay-num-showings": str(settings.replay_num_showings),
        "replay-rate": str(settings.replay_rate),
    }
    labels_of = {
        "replay-skipback": "Length of the replay, in seconds",
        "replay-num-showings": "Times to show the replay",
        "replay-rate": "Playback speed",
    }
    fields = [
        (key, labels_of[key], choices, current[key])
        for key, choices in REPLAY_CHOICES.items()
    ]
    return render(
        request, "ui/dashboard/sheets/replay_settings.html", {"fields": fields}
    )


@require_POST
@guard
def replay_settings(request):
    if (refusal := _need_set_up(request)) is not None:
        return refusal
    # `apply_form` counts the legacy form's `action` field among the keys.
    post = {"action": "settings.write"} | {
        key: request.POST[key]
        for key, choices in REPLAY_CHOICES.items()
        if request.POST.get(key) in {value for value, _ in choices}
    }
    with transaction.atomic():
        result = settings_form.apply_form(_event(request), post)
    if result.failure:
        return service_error(request, result.failure[1])
    return updated(request, "Replay settings saved.")


@require_POST
@guard
def stop_test(request):
    timer.set_test_mode(_event(request), False)
    return updated(request, "Simulated racing stopped.")


# ----- rounds ----------------------------------------------------------------------


def _round(request, round_id) -> Round:
    return get_object_or_404(
        Round.objects.select_related("group"), pk=round_id, event=_event(request)
    )


def _round_name(round: Round) -> str:
    return f"{round.group.name}, Round {round.number}"


@require_GET
@guard
def round_sheet(request, round_id, kind):
    """The sheet behind a round card's button: Schedule, or the confirm
    sheet of Unschedule, Delete Round, Repeat Round (purge) and Adjust
    Schedule, which names the round and the heats it touches."""
    round = _round(request, round_id)
    live = build(request, _event(request), Shown())
    card = next(
        (
            c
            for _, _, cards in live.context["groups"]
            for c in cards
            if c.roundid == round.pk
        ),
        None,
    )
    if card is None or kind not in (
        "schedule",
        "unschedule",
        "delete",
        "purge",
        "adjust",
    ):
        raise Http404
    context = {
        "round": round,
        "card": card,
        "name": _round_name(round),
        "kind": kind,
        "runs": RUNS_PER_LANE,
        "use_master_sched": live.context["use_master_sched"],
    }
    return render(request, "ui/dashboard/sheets/round.html", context)


@require_POST
@guard
def round_action(request, round_id, op):
    event = _event(request)
    round = _round(request, round_id)
    name = _round_name(round)
    match op:
        case "schedule":
            try:
                runs = int(request.POST.get("runs", "1"))
            except ValueError:
                runs = 1
            try:
                schedule.generate_schedule(round, runs)
            except ScheduleFailure as failure:
                return refused(request, failure)
            if php_truthy(request.POST.get("race", "")):
                failure = heats.select_heat(
                    event, round_id=round.pk, heat=1, now_racing=True
                )
                return _hs_reply(request, failure, f"{name} scheduled and racing.")
            return updated(request, f"{name} scheduled.")
        case "race":
            failure = heats.select_heat(
                event, round_id=round.pk, heat=1, now_racing=True
            )
            return _hs_reply(request, failure, f"Racing {name}.")
        case "changes":
            failure = heats.select_heat(
                event, round_id=round.pk, heat=1, now_racing=False
            )
            return _hs_reply(
                request, failure, f"{name} is the current round, with racing off."
            )
        case "adjust":
            try:
                reschedule.reschedule(round)
            except RescheduleFailure as failure:
                return refused(request, failure)
            return updated(request, f"Schedule of {name} adjusted.")
        case "unschedule":
            schedule.unschedule(round)
            return updated(request, f"{name} unscheduled.")
        case "delete":
            try:
                rounds.delete_round_roster(round)
            except RoundFailure as failure:
                return refused(request, failure)
            return updated(request, f"{name} deleted.")
        case "purge":
            if (refusal := _need_set_up(request)) is not None:
                return refusal
            purge.purge(event, "results", str(round.pk))
            return updated(request, f"Results of {name} purged.")
    raise Http404


# ----- the new-round stepper --------------------------------------------------------


def _stepper_context(request):
    event = _event(request)
    live = build(request, event, Shown())
    payload = live.context["payload"]
    settings = RaceSettings.objects.get(event=event)
    names = labels(settings)
    done = live.context["completed_rounds"]
    use_subgroups = settings.use_subgroups
    aggregate_ids = {r["roundid"] for r in payload["rounds"] if r["aggregate"]}
    add_aggregate = len(done) > 1 or (
        use_subgroups and len(done) == 1 and len(done[0]["subgroups"]) > 1
    )
    ready = [
        {"classid": a["classid"], "class": a["class"], "by_subgroup": a["by-subgroup"]}
        for a in payload["ready-aggregate"]
        if add_aggregate and (len(done) > 1 or a["by-subgroup"])
    ]
    constituents = [
        {"id": f"classid_{r['classid']}", "name": r["class"], "checked": True}
        for r in done
    ] + [
        {"id": f"classid_{a['classid']}", "name": a["class"], "checked": False}
        for a in payload["ready-aggregate"]
        if not a["by-subgroup"]
    ]
    subgroups = [
        {"id": f"rankid_{s['rankid']}", "name": s["name"]}
        for r in done
        for s in r["subgroups"]
    ]
    return {
        "follow_on": [
            {
                **r,
                "hide_bucketed": not use_subgroups and r["roundid"] in aggregate_ids,
            }
            for r in done
        ],
        "ready": ready,
        "add_aggregate": add_aggregate,
        "constituents": constituents,
        "subgroups": subgroups,
        "use_subgroups": use_subgroups,
        "group_label": names.group,
        "subgroup_label": names.subgroup,
    }


@require_GET
@guard
def new_round_sheet(request):
    return render(
        request, "ui/dashboard/sheets/new_round.html", _stepper_context(request)
    )


def _flagged(event, data: QueryDict, model, prefix: str) -> list:
    ids = [key.removeprefix(prefix) for key in data if key.startswith(prefix)]
    found = model.objects.filter(event=event, pk__in=[i for i in ids if i.isdigit()])
    return list(found.order_by("pk"))


@require_POST
@guard
def new_round(request):
    """A follow-on round, round 1 of a ready aggregate group, or a new
    aggregate round, with the top racers overall or from each group or
    subgroup (`roster.new`). How many racers always comes from the race
    coordinator (D43)."""
    event = _event(request)
    data = request.POST
    try:
        top = int(data.get("top", ""))
    except ValueError:
        top = 0
    if top < 1:
        return service_error(request, "Choose how many racers go to the new round.")
    bucketed = php_truthy(data.get("bucketed", ""))
    match data.get("kind"):
        case "follow-on":
            source = FollowOn(
                get_object_or_404(Round, pk=data.get("roundid"), event=event)
            )
        case "aggregate":
            source = FromAggregate(
                get_object_or_404(Group, pk=data.get("classid"), event=event)
            )
        case "new-aggregate":
            name = (data.get("classname") or "").strip() or "Grand Finals"
            if php_truthy(data.get("by_subgroup", "")):
                source = NewAggregate(
                    name, subgroups=_flagged(event, data, Subgroup, "rankid_")
                )
            else:
                source = NewAggregate(
                    name, groups=_flagged(event, data, Group, "classid_")
                )
        case _:
            return service_error(request, "Choose what kind of round to add.")
    try:
        built = rounds.new_round_roster(source, top, bucketed=bucketed)
    except RoundFailure as failure:
        return refused(request, failure)
    count = len(built.qualifiers) + len(built.extras)
    plural = "s" if count != 1 else ""
    message = f"{_round_name(built.round)} made with {count} racer{plural}."
    return updated(request, message, notices=built.concerns)


# ----- the playlist -----------------------------------------------------------------


@require_POST
@guard
def playlist_start(request):
    playlist.start_playlist(_event(request), rounds.playlist_finalists())
    return updated(request, "Playlist started.")


@require_POST
@guard
def start_next_round(request):
    """ "Start next round" in the playlist intermission (D12)."""
    try:
        round = playlist.start_waiting_step(_event(request))
    except PlaylistFailure as failure:
        return refused(request, failure)
    return updated(request, f"Racing {_round_name(round)}.")


# ----- late racers, final requests, finalist suggestions ----------------------------


def _racer(request, racer_id) -> Racer:
    return get_object_or_404(Racer, pk=racer_id, event=_event(request))


def _grid(
    heats_by_number: dict[int, dict[int, int]],
    cars: dict[int, int],
    lane_count: int,
    racer_id: int,
):
    """A chart as rows of (heat, cells), each cell a car number and whether
    it is the racer being added."""
    width = max([lane_count, *(max(h, default=0) for h in heats_by_number.values())])
    rows = []
    for number in sorted(heats_by_number):
        lanes_of = heats_by_number[number]
        cells = []
        for lane in range(1, width + 1):
            rid = lanes_of.get(lane)
            cells.append(
                {
                    "car": presenter.car(cars[rid]) if rid in cars else "",
                    "racer": rid == racer_id,
                }
            )
        rows.append((number, cells))
    return rows


def _chart_now(round: Round) -> dict[int, dict[int, int]]:
    found: dict[int, dict[int, int]] = {}
    for heat_no, lane, racer_id in HeatLane.objects.filter(round=round).values_list(
        "heat", "lane", "racer_id"
    ):
        found.setdefault(heat_no, {})[lane] = racer_id
    return found


def _chart_reply(chart: list[str | None]) -> dict[int, dict[int, int]]:
    """The reschedule reply's chart: heat N's racer ids by lane, "-" a bye."""
    found: dict[int, dict[int, int]] = {}
    for number, line in enumerate(chart):
        if not line:
            continue
        found[number] = {
            lane: int(rid) for lane, rid in enumerate(line.split(), 1) if rid != "-"
        }
    return found


@require_POST
@guard
def late_preview(request, round_id, racer_id):
    """The chart a `schedule.reschedule` dry run gives for one racer left out
    of a round, beside the chart now (D32, D36). The dry run changes nothing
    (Q5)."""
    round = _round(request, round_id)
    racer = _racer(request, racer_id)
    context = {
        "round": round,
        "name": _round_name(round),
        "racer": racer,
        "car": presenter.car(racer.car_number),
    }
    before = _chart_now(round)
    try:
        preview = reschedule.reschedule(round, racer.pk, dry_run=True)
    except RescheduleFailure as failure:
        context["failure"] = failure
        context["not_on_roster"] = failure.code == reschedule.Failure.NOT_ON_ROSTER
        context["is_final"] = finals.is_final(round)
    else:
        after = _chart_reply(preview.chart)
        ids = {
            rid for heat in [*before.values(), *after.values()] for rid in heat.values()
        }
        cars = dict(Racer.objects.filter(pk__in=ids).values_list("pk", "car_number"))
        lane_count = RaceSettings.objects.get(event=round.event).lane_count
        context["charts"] = [
            ("The schedule now", _grid(before, cars, lane_count, racer.pk)),
            ("With the racer added", _grid(after, cars, lane_count, racer.pk)),
        ]
        context["new_heats"] = sorted(
            n for n, row in after.items() if racer.pk in row.values()
        )
    return render(request, "ui/dashboard/sheets/late.html", context)


@require_POST
@guard
def late_add(request, round_id, racer_id):
    """Add to schedule: the same reschedule without the dry run (D32)."""
    round = _round(request, round_id)
    racer = _racer(request, racer_id)
    try:
        reschedule.reschedule(round, racer.pk)
    except RescheduleFailure as failure:
        return refused(request, failure)
    return updated(
        request, f"{racer.first_name} {racer.last_name} added to {_round_name(round)}."
    )


@require_POST
@guard
def roster_add(request, round_id, racer_id):
    """Put a racer refused with `notonroster` on the final's roster first
    (D39)."""
    round = _round(request, round_id)
    racer = _racer(request, racer_id)
    try:
        finals.add_to_final(racer, round)
    except FinalsFailure as failure:
        return refused(request, failure)
    return updated(
        request,
        f"{racer.first_name} {racer.last_name} is on the roster"
        f" of {_round_name(round)}.",
    )


@require_POST
@guard
def award_settings(request, racer_id):
    """Either award setting of D33 for a racer added late."""
    racer = _racer(request, racer_id)
    setting = request.GET.get("setting")
    on = php_truthy(request.POST.get("on", ""))
    if setting == "exclude":
        set_award_settings(racer, exclude=on)
    elif setting == "no-speed":
        set_award_settings(racer, no_speed_awards=on)
    else:
        raise Http404
    return updated(request)


@require_POST
@guard
def final_request(request, request_id, decision):
    found = get_object_or_404(FinalRequest, pk=request_id, event=_event(request))
    name = f"{found.racer.first_name} {found.racer.last_name}"
    try:
        if decision == "allow":
            finals.allow_final_request(found)
            message = f"{name} allowed into the final."
        elif decision == "decline":
            finals.decline_final_request(found)
            message = f"{name} declined."
        else:
            raise Http404
    except FinalsFailure as failure:
        return refused(request, failure)
    return updated(request, message)


@require_POST
@guard
def finalist(request, round_id, op, racer_id):
    """Act on one finalist suggestion (D32). Nothing changes until the race
    coordinator taps one."""
    round = _round(request, round_id)
    racer = _racer(request, racer_id)
    name = f"{racer.first_name} {racer.last_name}"
    try:
        if op == "add":
            finals.add_to_final(racer, round)
            message = f"{name} added to {_round_name(round)}."
        elif op == "remove":
            finals.remove_from_final(racer, round)
            message = f"{name} taken out of {_round_name(round)}."
        else:
            raise Http404
    except FinalsFailure as failure:
        return refused(request, failure)
    return updated(request, message)


# ----- closed lanes -----------------------------------------------------------------


@require_GET
@guard
def lanes_sheet(request):
    """Close a damaged lane (D36): pick the lanes, answer how to score, and
    see every round the closure reschedules before committing (D45)."""
    event = _event(request)
    settings = RaceSettings.objects.get(event=event)
    polled = lanes.poll_lanes(event)
    closed = {row["lane"] for row in polled["closed-lanes"]}
    unused = settings.unused_lane_mask
    open_lanes = [
        n
        for n in range(1, settings.lane_count + 1)
        if n not in closed and not unused & (1 << (n - 1))
    ]
    affected = [
        _round_name(r)
        for r in Round.objects.filter(
            pk__in=[r.pk for r in lanes.rounds_to_close(event)]
        )
        .select_related("group")
        .order_by("pk")
    ]
    context = {
        "open_lanes": open_lanes,
        "scoring": polled["scoring"],
        "choices": list(presenter.SCORING.items()),
        "affected": affected,
    }
    return render(request, "ui/dashboard/sheets/lanes.html", context)


@require_POST
@guard
def lanes_close(request):
    event = _event(request)
    chosen = [int(n) for n in request.POST.getlist("lane") if n.isdigit()]
    try:
        scoring = int(request.POST.get("scoring", ""))
    except ValueError:
        scoring = None
    if not chosen:
        return service_error(request, "Choose the lane to close.")
    try:
        lanes.close_lanes(event, chosen, scoring)
    except LanesFailure as failure:
        return refused(request, failure)
    names = ", ".join(str(n) for n in sorted(chosen))
    return updated(request, f"Lane {names} closed.")


@require_POST
@guard
def lanes_reopen(request, lane):
    try:
        lanes.reopen_lanes(_event(request), [lane])
    except LanesFailure as failure:
        return refused(request, failure)
    return updated(request, f"Lane {lane} reopened.")
