"""Role login for pages: the login and logout views, `page_requires()`, and
the mobile check-in link (#15, #16, #40)."""

from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import get_script_prefix, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import Role
from accounts.permissions import (
    Perm,
    Requirement,
    add_bits,
    allows,
    log_in,
    log_out,
    role,
)

MCHECKIN = Perm.CHECK_IN_RACERS | Perm.REVERT_CHECK_IN | Perm.PHOTO_UPLOAD
_MCHECKIN_SIGNER = signing.TimestampSigner(salt="derbynet.mcheckin")
_MCHECKIN_VALUE = "mcheckin"
# A QR code on the check-in page grants check-in for an hour, as in PHP.
MCHECKIN_MAX_AGE = 3600


def make_mcheckin_token() -> str:
    return _MCHECKIN_SIGNER.sign(_MCHECKIN_VALUE)


def _valid_mcheckin_token(token: str) -> bool:
    try:
        value = _MCHECKIN_SIGNER.unsign(token, max_age=MCHECKIN_MAX_AGE)
    except signing.BadSignature:
        return False
    return value == _MCHECKIN_VALUE


def _to_login(request) -> HttpResponseRedirect:
    query = urlencode({"next": request.get_full_path()})
    return HttpResponseRedirect(f"{reverse('login')}?{query}")


def _forbidden(request) -> HttpResponse:
    """An htmx request cannot follow a redirect to a page, so it gets 403 and
    a toast, which the base template's htmx config swaps into the toasts."""
    toast = render_to_string(
        "ui/components/toast.html",
        {"message": "You are not allowed to do that. Log in again.", "level": "error"},
        request,
    )
    return HttpResponse(toast, status=403)


def page_requires(requirement: Requirement):
    """Send a page request without `requirement` to the login page, which
    returns it to this page after a login. An htmx request gets 403."""

    def decorate(view):
        @wraps(view)
        def checked(request, *args, **kwargs):
            if not allows(request, requirement):
                if request.headers.get("HX-Request") == "true":
                    return _forbidden(request)
                return _to_login(request)
            return view(request, *args, **kwargs)

        return checked

    return decorate


def _safe_next(request) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    # PHP's login page goes on to the home menu.
    return reverse("home")


@require_http_methods(["GET", "POST"])
def login(request):
    """One button per role opens a password sheet. A failed login shows the
    sheet again with the error in it (no `alert()`)."""
    error = ""
    attempted = ""
    if request.method == "POST":
        attempted = request.POST.get("name", "")
        found = Role.objects.named(attempted)
        if found is None or not found.name:
            error = "No such role."
        elif not found.check_password(request.POST.get("password", "")):
            error = "Incorrect password."
        else:
            log_in(request.session, found)
            return HttpResponseRedirect(_safe_next(request))
    show_all = "all" in request.GET or request.POST.get("all") == "1"
    roles = Role.objects.exclude(name="")
    if not show_all:
        roles = roles.filter(interactive=True)
    context = {
        "roles": roles,
        "show_all": show_all,
        "current": role(request),
        "next": request.POST.get("next") or request.GET.get("next", ""),
        "error": error,
        "attempted": attempted if isinstance(attempted, str) else "",
        # PHP hides the documentation hint on a hosted install.
        "locked_settings": settings.LOCK_SETTINGS,
        # The kiosk and camera pages keep their PHP paths (#52, #53).
        "script_prefix": get_script_prefix(),
    }
    return render(request, "accounts/login.html", context, status=401 if error else 200)


@require_POST
def logout(request):
    """The app bar's Log out button posts here."""
    log_out(request.session)
    return HttpResponseRedirect(reverse("login"))


@require_GET
def mcheckin(request):
    """The phone link from the check-in page's QR code."""
    if _valid_mcheckin_token(request.GET.get("tok", "")):
        add_bits(request.session, MCHECKIN)
    if not all(allows(request, bit) for bit in MCHECKIN):
        return _to_login(request)
    return render(request, "accounts/mcheckin.html")
