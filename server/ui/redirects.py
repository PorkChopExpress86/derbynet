"""301 redirects from the PHP page URLs to the new pages (D20).

Bookmarks and printed links keep working, with their query string. The table
comes from the navigation registry: each entry whose page is built (its
`view` is set) answers its `php` page. A placeholder never takes its PHP URL,
so a page's redirect turns on with the ticket that builds it.

A `php` value with a query string, such as `photo-thumbs.php?repo=head`, is
not a URL path, so it gets no entry here. The ticket that builds those pages
(#47) adds one view for the PHP path that reads the query and redirects.
`EXTRA_PAGES` holds PHP pages that are not registry pages. Machine paths,
such as `action.php` and `kiosk.php`, keep their own behavior and are never
here.
"""

from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.urls import path, reverse

from ui import navigation

# PHP page to the URL name of its replacement, for pages not in the registry.
# The phone page `mcheckin.php` folds into check-in, which reads its `tok`
# (#42).
EXTRA_PAGES: dict[str, str] = {"mcheckin.php": "checkin"}


def legacy_pages() -> dict[str, str]:
    """PHP page to the URL name of the page that replaces it."""
    built = {
        e.php: e.key
        for e in navigation.ENTRIES
        if e.view and e.php and "?" not in e.php
    }
    return {**built, **EXTRA_PAGES}


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
    for php, name in legacy_pages().items():
        view = _redirect_to(name)
        patterns += [path(php, view), path(f"{prefix}/{php}", view)]
    return patterns
