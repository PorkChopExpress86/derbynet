from django.urls import path, re_path

from spike import views

urlpatterns = [
    path("", views.index),
    path("replay-harness", views.replay_harness),
    path("api/standings", views.api_standings),
    path("api/leaders", views.api_leaders),
    path("events", views.events),
    path("a/now-racing/", views.a_now_racing),
    path("a/now-racing/board", views.a_now_racing_board),
    path("a/kiosk-poll", views.a_kiosk_poll),
    path("a/standings/", views.a_standings),
    path("a/standings/table", views.a_standings_table),
    path("a/leaders/", views.a_leaders),
    path("a/leaders/panel", views.a_leaders_panel),
    re_path(r"^b/(?P<path>.*)$", views.b_static),
]
