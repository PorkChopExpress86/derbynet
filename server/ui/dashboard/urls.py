"""The race dashboard's partial and action URLs (#41). The page itself is
the registry entry `dashboard` (`ui.navigation`); every URL here takes the
same guard, `requires_page("dashboard")`."""

from django.urls import path

from ui.dashboard import views

urlpatterns = [
    path("dashboard/live/", views.live, name="dashboard-live"),
    path("dashboard/racing/", views.racing, name="dashboard-racing"),
    path("dashboard/heat/<str:which>/", views.heat, name="dashboard-heat"),
    path("dashboard/rerun/", views.rerun, name="dashboard-rerun"),
    path("dashboard/remote-start/", views.remote_start, name="dashboard-remote-start"),
    path("dashboard/results/", views.results_write, name="dashboard-results"),
    path(
        "dashboard/results/sheet/", views.results_sheet, name="dashboard-results-sheet"
    ),
    path(
        "dashboard/results/discard/",
        views.results_discard,
        name="dashboard-results-discard",
    ),
    path(
        "dashboard/results/restore/",
        views.results_restore,
        name="dashboard-results-restore",
    ),
    path(
        "dashboard/replay/trigger/",
        views.replay_trigger,
        name="dashboard-replay-trigger",
    ),
    path("dashboard/replay/test/", views.replay_test, name="dashboard-replay-test"),
    path(
        "dashboard/replay/settings/",
        views.replay_settings,
        name="dashboard-replay-settings",
    ),
    path(
        "dashboard/replay/settings/sheet/",
        views.replay_settings_sheet,
        name="dashboard-replay-settings-sheet",
    ),
    path("dashboard/timer-test/stop/", views.stop_test, name="dashboard-stop-test"),
    path(
        "dashboard/round/<int:round_id>/sheet/<str:kind>/",
        views.round_sheet,
        name="dashboard-round-sheet",
    ),
    path(
        "dashboard/round/<int:round_id>/<str:op>/",
        views.round_action,
        name="dashboard-round",
    ),
    path("dashboard/new-round/", views.new_round, name="dashboard-new-round"),
    path(
        "dashboard/new-round/sheet/",
        views.new_round_sheet,
        name="dashboard-new-round-sheet",
    ),
    path(
        "dashboard/playlist/start/",
        views.playlist_start,
        name="dashboard-playlist-start",
    ),
    path(
        "dashboard/playlist/next/",
        views.start_next_round,
        name="dashboard-start-next-round",
    ),
    path(
        "dashboard/late/<int:round_id>/<int:racer_id>/preview/",
        views.late_preview,
        name="dashboard-late-preview",
    ),
    path(
        "dashboard/late/<int:round_id>/<int:racer_id>/add/",
        views.late_add,
        name="dashboard-late-add",
    ),
    path(
        "dashboard/late/<int:round_id>/<int:racer_id>/roster/",
        views.roster_add,
        name="dashboard-roster-add",
    ),
    path(
        "dashboard/racer/<int:racer_id>/awards/",
        views.award_settings,
        name="dashboard-awards",
    ),
    path(
        "dashboard/final-request/<int:request_id>/<str:decision>/",
        views.final_request,
        name="dashboard-final-request",
    ),
    path(
        "dashboard/finalist/<int:round_id>/<str:op>/<int:racer_id>/",
        views.finalist,
        name="dashboard-finalist",
    ),
    path("dashboard/lanes/sheet/", views.lanes_sheet, name="dashboard-lanes-sheet"),
    path("dashboard/lanes/close/", views.lanes_close, name="dashboard-lanes-close"),
    path(
        "dashboard/lanes/<int:lane>/reopen/",
        views.lanes_reopen,
        name="dashboard-lanes-reopen",
    ),
]
