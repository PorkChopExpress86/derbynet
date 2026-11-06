from django.urls import path

from ui.settings_page import views

urlpatterns = [
    path("settings/save/", views.save, name="settings-save"),
    path("settings/lane-colors/", views.lane_colors, name="settings-lane-colors"),
    path(
        "settings/weigh-in/<int:partition_id>/",
        views.weigh_in,
        name="settings-weigh-in",
    ),
]
