"""The timer pages (#50): timer status and settings, the timer test, the
fake timer, and the browser timer at `/timer.php`.

- Timer status (`/timer/`) is the summary half of `timer-test.php`: the
  timer's state, details and client, its settings sheet (`timer.settings`
  and `timer.assign-*`), remote logging and the log tail (`timer.log`),
  Start Race (`timer.remote-start`), and the coordinator page's count of
  refused results. Its status card polls every 2 s and answers 204 while
  what it renders is unchanged; the seconds since the last contact count up
  on the page from the `X-Timer-Contact` header every reply carries, so a
  heartbeat leaves the poll at 204.
- Timer test (`/timer/test/`) is the other half: simulate racing
  (`timer.test` with `test-mode`), the lane count and reverse lanes
  (`settings.write`), a toggle per lane for the test's lane mask, and the
  test heat's times and places, polled every 500 ms as PHP polls
  `poll.timer.test`.
- Fake timer (`/timer/fake/`) is `fake-timer.php` for a phone. Its script,
  `ui/fake-timer.js`, posts the same `timer-message` messages to
  `/action.php`, so it still tests the legacy protocol.
- `/timer.php` serves the browser timer from `templates/timer.html`
  (`ui.timer_build`), its scripts unchanged, at the relative paths it loads
  them from. The template differs from PHP's `timer.php` where the README's
  "The browser timer" section lists.

Every timer control needs CONTROL_RACE, as #15 hardened `timer.assign-*`,
`timer.test`, `timer.log` and `timer.settings` (S21); the status and test
pages take it as their page gate. The browser timer needs TIMER_MESSAGE, the
Timer role's one permission (D5), and shows its Timer settings button only
to a session with CONTROL_RACE (D53).
"""

import hashlib
import mimetypes

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import Perm, allows
from accounts.views import page_requires
from core import settings_form
from core.models import RaceSettings
from racing.models import TimerStatus
from racing.services import timer
from ui import timer_build
from ui.navigation import requires_page
from ui.responses import active_event as _event
from ui.responses import (
    closing_sheet,
    posted_int,
    service_error,
    toast_html,
)
from ui.timer import state
from ui.version import timer_version

status_guard = requires_page("timer")
test_guard = requires_page("timer-test")
SHEET_BODY = "timer-sheet-body"
CONTACT_HEADER = "X-Timer-Contact"


def _signed(html: str, sig_id: str) -> tuple[str, str]:
    sig = hashlib.sha256(html.encode()).hexdigest()[:20]
    return f'<input type="hidden" id="{sig_id}" name="sig" value="{sig}">{html}', sig


def _with_contact(response: HttpResponse, found: state.Status) -> HttpResponse:
    response[CONTACT_HEADER] = "" if found.contact is None else str(found.contact)
    return response


# ----- timer status ----------------------------------------------------------------


def status_html(request) -> tuple[str, str, state.Status]:
    found = state.status(_event(request), timezone.now())
    html = render_to_string(
        "ui/timer/status.html",
        {"status": found, "fake_timer_url": reverse("fake-timer")},
        request,
    )
    return (*_signed(html, "timer-sig"), found)


@require_GET
def page(request):
    event = _event(request)
    html, _, found = status_html(request)
    settings = RaceSettings.objects.get(event=event)
    log_path = TimerStatus.objects.get(event=event).log_path
    return render(
        request,
        "ui/timer/page.html",
        {
            "status_html": html,
            "status": found,
            "server_now": int(timezone.now().timestamp()),
            "poll_seconds": state.STATUS_POLL_SECONDS,
            "send_logs": settings.timer_send_logs,
            "log_path": "" if timer.log_path_hidden() else log_path,
            "page_title": "Timer",
        },
    )


@require_GET
@status_guard
def status_poll(request):
    """The 2 s poll. 204 while the card is what the page sent the signature
    of; the contact header comes either way."""
    html, sig, found = status_html(request)
    if request.GET.get("sig") == sig:
        return _with_contact(HttpResponse(status=204), found)
    return _with_contact(HttpResponse(html), found)


@require_POST
@status_guard
def remote_start(request):
    if not timer.request_remote_start(_event(request)):
        return service_error(request, "The timer does not offer a remote start now.")
    return HttpResponse(toast_html(request, "Start sent to the timer.", "ok"))


def _write_setting(request, post: dict, message: str) -> HttpResponse:
    result = settings_form.apply_form(
        _event(request), settings_form.SETTINGS_WRITE | post
    )
    if result.failure:
        return service_error(request, result.failure[1])
    return HttpResponse(toast_html(request, message, "ok"))


@require_POST
@status_guard
def remote_logging(request):
    """The remote logging switch, `settings.write timer-send-logs`. The
    timer starts or stops sending its log at its next message."""
    on = request.POST.get("timer-send-logs") == "1"
    return _write_setting(
        request,
        {"timer-send-logs": "1" if on else "0"},
        "The timer will send its log."
        if on
        else "The timer will stop sending its log.",
    )


@require_GET
@status_guard
def log_tail(request):
    """`query=timer.log` for the log tail: the log from byte `seek`. The page
    keeps `timer-test.js`'s seek and backoff."""
    seek = posted_int(request.GET.get("seek", "0")) or 0
    read = timer.read_log(_event(request), max(seek, 0))
    reply = {"found": read.found, "size": read.size}
    if not timer.log_path_hidden():
        reply["path"] = read.path
    if read.data is not None:
        reply["data"] = read.data
    return JsonResponse(reply)


# ----- the settings sheet ----------------------------------------------------------


@require_GET
@status_guard
def settings_sheet(request):
    return render(
        request, "ui/timer/sheets/settings.html", state.settings_sheet(_event(request))
    )


def _sent(request, message: str) -> HttpResponse:
    """An assignment's reply. It reaches the timer with its next message,
    which then reports its new settings."""
    return HttpResponse(
        closing_sheet(request, SHEET_BODY, f"{message} The timer gets it next.")
    )


@require_POST
@status_guard
def assign_port(request):
    port = request.POST.get("port", "")
    timer.assign_port(_event(request), port)
    return _sent(request, f"Port {port} sent." if port else "Auto port sent.")


@require_POST
@status_guard
def assign_device(request):
    device = request.POST.get("device", "")
    timer.assign_device(_event(request), device)
    return _sent(request, f"Device {device} sent." if device else "Auto device sent.")


@require_POST
@status_guard
def assign_flag(request):
    flag = request.POST.get("flag", "")
    if not flag:
        return service_error(request, "Pick a setting.")
    value = request.POST.get("value", "")
    if request.POST.get("kind") == "bool":
        value = "true" if value == "1" else "false"
    timer.assign_flag(_event(request), flag, value)
    return _sent(request, f"{flag} = {value} sent.")


# ----- the timer test --------------------------------------------------------------


def test_html(request) -> tuple[str, str]:
    found = state.test(_event(request))
    html = render_to_string(
        "ui/timer/test_live.html",
        {
            "test": found,
            "lanes_min": timer.TEST_PAGE_LANES[0],
            "lanes_max": timer.TEST_PAGE_LANES[1],
        },
        request,
    )
    return _signed(html, "timer-test-sig")


@require_GET
def test_page(request):
    html, _ = test_html(request)
    status, _, found = status_html(request)
    return render(
        request,
        "ui/timer/test.html",
        {
            "test_html": html,
            "status_html": status,
            "status": found,
            "server_now": int(timezone.now().timestamp()),
            "poll_ms": state.TEST_POLL_MS,
            "poll_seconds": state.STATUS_POLL_SECONDS,
            "page_title": "Timer Test",
        },
    )


@require_GET
@test_guard
def test_live(request):
    """The 500 ms poll of the test heat."""
    html, sig = test_html(request)
    if request.GET.get("sig") == sig:
        return HttpResponse(status=204)
    return HttpResponse(html)


def _test_changed(request, message: str) -> HttpResponse:
    html, _ = test_html(request)
    return HttpResponse(
        toast_html(request, message, "ok")
        + f'<div id="timer-test-live" hx-swap-oob="innerHTML">{html}</div>'
    )


@require_POST
@test_guard
def test_mode(request):
    on = request.POST.get("test-mode") == "1"
    timer.set_test_mode(_event(request), on)
    return _test_changed(
        request, "Simulated racing on." if on else "Simulated racing off."
    )


@require_POST
@test_guard
def test_lane(request):
    lane = posted_int(request.POST.get("lane", ""), positive=True)
    occupied = request.POST.get("occupied") == "1"
    if lane is None or not timer.set_test_lane(_event(request), lane, occupied):
        return service_error(request, "The track has no such lane.")
    return _test_changed(
        request, f"Lane {lane} {'has a car' if occupied else 'is empty'}."
    )


@require_POST
@test_guard
def test_lane_count(request):
    lanes = posted_int(request.POST.get("n-lanes", ""))
    if lanes is None:
        return service_error(request, "Type the number of lanes.")
    result = settings_form.apply_form(
        _event(request), settings_form.SETTINGS_WRITE | {"n-lanes": str(lanes)}
    )
    if result.failure:
        return service_error(request, result.failure[1])
    return _test_changed(request, f"The track has {lanes} lane{'s' * (lanes != 1)}.")


@require_POST
@test_guard
def test_reverse_lanes(request):
    post = {"reverse-lanes-checkbox": "1"}
    on = request.POST.get("reverse-lanes") == "1"
    if on:
        post["reverse-lanes"] = "1"
    result = settings_form.apply_form(
        _event(request), settings_form.SETTINGS_WRITE | post
    )
    if result.failure:
        return service_error(request, result.failure[1])
    return _test_changed(
        request,
        "Lanes are numbered in reverse." if on else "Lanes are numbered as usual.",
    )


# ----- the fake timer --------------------------------------------------------------


@require_GET
def fake_page(request):
    settings = RaceSettings.objects.get(event=_event(request))
    return render(
        request,
        "ui/timer/fake.html",
        {
            "lane_count": settings.lane_count or 0,
            "lanes_min": timer.FAKE_TIMER_LANES[0],
            "lanes_max": timer.FAKE_TIMER_LANES[1],
            "action_url": reverse("action-php"),
            "home_url": reverse("home"),
            "page_title": "Fake Timer",
        },
    )


# ----- the browser timer (/timer.php) ----------------------------------------------

_hosting = page_requires(Perm.TIMER_MESSAGE)


@require_GET
@_hosting
def browser_timer(request):
    """`timer.php`, from `templates/timer.html`. The Timer settings button
    shows only with CONTROL_RACE (D53)."""
    return render(
        request,
        "ui/timer/browser.html",
        {
            "version": timer_version(),
            "can_set_timer": allows(request, Perm.CONTROL_RACE),
            "no_serial_url": reverse("timer-no-serial"),
        },
    )


@require_GET
@_hosting
def no_serial(request):
    """What a browser without Web Serial gets instead of the timer. A page
    on plain HTTP away from localhost is no secure context, so it also
    links to the `https` address of the same page."""
    return render(
        request,
        "ui/timer/no_serial.html",
        {"timer_url": reverse("timer-php"), "page_title": "Browser Timer"},
    )


@require_GET
def browser_asset(request, folder: str, path: str):
    """A file the browser timer loads by a relative path: `js/`, `css/` or
    `img/` under `ui/static/timer/`. Only files that are there are served,
    and no path leaves the folder."""
    root = (timer_build.ASSETS / folder).resolve()
    found = (root / path).resolve()
    if root not in found.parents or not found.is_file():
        raise Http404
    content_type, _ = mimetypes.guess_type(found.name)
    response = FileResponse(found.open("rb"), content_type=content_type)
    # A new build replaces the files at the same paths.
    response["Cache-Control"] = "no-cache"
    return response
