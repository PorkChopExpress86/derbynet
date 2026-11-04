"""The timer pages' polls, sheet and controls (#50), and the browser timer.

The pages themselves are the registry entries `timer`, `timer-test` and
`fake-timer` (`ui.navigation`). `/timer.php` and the `js/`, `css/` and
`img/` files it loads by relative paths answer at the server root and, as
`action.php` does, under the Debian layout's `derbynet/` prefix.
"""

from django.conf import settings
from django.urls import path

from ui.timer import views

_legacy_prefix = (settings.FORCE_SCRIPT_NAME or "/derbynet").strip("/")

urlpatterns = [
    path("timer/status/", views.status_poll, name="timer-status"),
    path("timer/start/", views.remote_start, name="timer-remote-start"),
    path("timer/logging/", views.remote_logging, name="timer-logging"),
    path("timer/log/", views.log_tail, name="timer-log"),
    path("timer/settings/", views.settings_sheet, name="timer-settings"),
    path("timer/settings/port/", views.assign_port, name="timer-assign-port"),
    path("timer/settings/device/", views.assign_device, name="timer-assign-device"),
    path("timer/settings/flag/", views.assign_flag, name="timer-assign-flag"),
    path("timer/test/live/", views.test_live, name="timer-test-live"),
    path("timer/test/mode/", views.test_mode, name="timer-test-mode"),
    path("timer/test/lane/", views.test_lane, name="timer-test-lane"),
    path("timer/test/lanes/", views.test_lane_count, name="timer-test-lanes"),
    path("timer/test/reverse/", views.test_reverse_lanes, name="timer-test-reverse"),
    path("timer/no-serial/", views.no_serial, name="timer-no-serial"),
]
for prefix in ("", f"{_legacy_prefix}/"):
    named = prefix == ""
    urlpatterns += [
        path(
            f"{prefix}timer.php",
            views.browser_timer,
            **({"name": "timer-php"} if named else {}),
        ),
        *(
            path(
                f"{prefix}{folder}/<path:path>",
                views.browser_asset,
                {"folder": folder},
            )
            for folder in ("js", "css", "img")
        ),
    ]
