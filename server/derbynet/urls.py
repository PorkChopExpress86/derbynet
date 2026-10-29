from django.conf import settings
from django.urls import path

from accounts import views as accounts
from core import views
from legacy_api.views import dispatch, export_json, post_timer_log, preferences

# A proxy that strips the URL prefix sends `action.php`. A server reached
# directly, such as runserver, sees the prefix in the path. A root install also
# answers clients set up for the Debian layout under `derbynet/` (#17).
_legacy_prefix = (settings.FORCE_SCRIPT_NAME or "/derbynet").strip("/")

urlpatterns = [
    path("health/", views.health, name="health"),
    path("login/", accounts.login, name="login"),
    path("logout/", accounts.logout, name="logout"),
    path("mcheckin.php", accounts.mcheckin, name="mcheckin"),
    path("action.php", dispatch, name="action-php"),
    path(f"{_legacy_prefix}/action.php", dispatch),
    path("post-timer-log.php", post_timer_log, name="post-timer-log"),
    path(f"{_legacy_prefix}/post-timer-log.php", post_timer_log),
    path("export-json.php", export_json, name="export-json"),
    path(f"{_legacy_prefix}/export-json.php", export_json),
    path("preferences.php", preferences, name="preferences"),
    path(f"{_legacy_prefix}/preferences.php", preferences),
]
