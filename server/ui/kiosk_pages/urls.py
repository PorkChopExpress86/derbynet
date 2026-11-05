"""The kiosk display pages (#52). `kiosk.php` and `fullscreen.php` answer at
the server root and, as `action.php` does, under the Debian layout's
`derbynet/` prefix, so a Raspberry Pi set up for PHP keeps working with no
config edit. The standalone on-deck and slideshow pages are the registry
entries `ondeck` and `slideshow` (`ui.navigation`)."""

from django.conf import settings
from django.urls import path

from ui.kiosk_pages import views

_legacy_prefix = (settings.FORCE_SCRIPT_NAME or "/derbynet").strip("/")

urlpatterns = [
    path("kiosk.php", views.kiosk, name="kiosk-php"),
    path(f"{_legacy_prefix}/kiosk.php", views.kiosk),
    path("fullscreen.php", views.fullscreen, name="fullscreen-php"),
    path(f"{_legacy_prefix}/fullscreen.php", views.fullscreen),
    path("kiosk/standings/", views.standings_list, name="kiosk-standings"),
    path("kiosk/check-in/", views.check_in_list, name="kiosk-check-in"),
]
