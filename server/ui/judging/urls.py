"""Judging's poll, sheet and action URLs (#45). The page itself is the
registry entry `judging` (`ui.navigation`); every URL here takes the same
guard, `requires_page("judging")`."""

from django.urls import path

from ui.judging import views

urlpatterns = [
    path("judging/board/", views.board, name="judging-board"),
    path("judging/racer/<int:racer_id>/", views.racer, name="judging-racer"),
    path("judging/racer/<int:racer_id>/adhoc/", views.ad_hoc, name="judging-adhoc"),
    path("judging/award/<int:award_id>/", views.award, name="judging-award"),
    path("judging/award/<int:award_id>/assign/", views.assign, name="judging-assign"),
    path("judging/award/<int:award_id>/remove/", views.remove, name="judging-remove"),
    path(
        "judging/award/<int:award_id>/restore/",
        views.restore,
        name="judging-restore",
    ),
]
