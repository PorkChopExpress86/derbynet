"""Role login for pages: the login and logout views, `page_requires()`, and
the mobile check-in link (#15, #16). #40 styles the pages."""

from functools import wraps
from urllib.parse import urlencode

from django.core import signing
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
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


def page_requires(requirement: Requirement):
    """Send a request without `requirement` to the login page, which returns
    it to this page after a login."""

    def decorate(view):
        @wraps(view)
        def checked(request, *args, **kwargs):
            if not allows(request, requirement):
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
    return reverse("login")


@require_http_methods(["GET", "POST"])
def login(request):
    error = ""
    if request.method == "POST":
        found = Role.objects.named(request.POST.get("name", ""))
        if found is None or not found.name:
            error = "No such role."
        elif not found.check_password(request.POST.get("password", "")):
            error = "Incorrect password."
        else:
            log_in(request.session, found)
            return HttpResponseRedirect(_safe_next(request))
    roles = Role.objects.exclude(name="")
    if "all" not in request.GET:
        roles = roles.filter(interactive=True)
    context = {
        "roles": roles,
        "current": role(request),
        "next": request.POST.get("next") or request.GET.get("next", ""),
        "error": error,
    }
    return render(request, "accounts/login.html", context, status=401 if error else 200)


@require_POST
def logout(request):
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
