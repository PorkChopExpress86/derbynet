"""301 redirects from the PHP page URLs to the new pages (D20).

Bookmarks and printed links keep working, with their query string. Each M4
ticket adds the PHP pages it replaces to `LEGACY_PAGES`. Machine paths, such
as `action.php` and `kiosk.php`, keep their own behavior and are never here.
"""

from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.urls import path, reverse

# PHP page to the URL name of the page that replaces it.
LEGACY_PAGES = {
    "index.php": "home",
    "login.php": "login",
}


def _redirect_to(name: str):
    def legacy_page(request):
        target = reverse(name)
        if query := request.META.get("QUERY_STRING", ""):
            target = f"{target}?{query}"
        return HttpResponsePermanentRedirect(target)

    return legacy_page


def urlpatterns():
    # As for action.php, a root install also answers the Debian layout's
    # `derbynet/` prefix.
    prefix = (settings.FORCE_SCRIPT_NAME or "/derbynet").strip("/")
    patterns = []
    for php, name in LEGACY_PAGES.items():
        view = _redirect_to(name)
        patterns += [path(php, view), path(f"{prefix}/{php}", view)]
    return patterns
