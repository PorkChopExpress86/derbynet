"""The rounds playlist page's sheet, partial and action URLs (#44). The page
itself is the registry entry `playlist` (`ui.navigation`); every URL here
takes the same guard, `requires_page("playlist")`."""

from django.urls import path

from ui.playlist import views

urlpatterns = [
    path("playlist/scene/", views.racing_scene, name="playlist-scene"),
    path("playlist/add/", views.add, name="playlist-add"),
    path("playlist/step/<int:entry_id>/", views.step, name="playlist-step"),
    path("playlist/step/<int:entry_id>/waits/", views.waits, name="playlist-waits"),
    path("playlist/step/<int:entry_id>/remove/", views.remove, name="playlist-remove"),
    path("playlist/restore/", views.restore, name="playlist-restore"),
    path("playlist/order/", views.order, name="playlist-order"),
    path("playlist/start/", views.start, name="playlist-start"),
    path("playlist/start-next/", views.start_next, name="playlist-start-next"),
    path("playlist/scheduling/", views.scheduling, name="playlist-scheduling"),
    path(
        "playlist/scheduling/method/",
        views.scheduling_method,
        name="playlist-scheduling-method",
    ),
    path(
        "playlist/scheduling/lanes/",
        views.scheduling_lanes,
        name="playlist-scheduling-lanes",
    ),
]
