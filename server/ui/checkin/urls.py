"""The check-in page's partial and action URLs (#42). The page itself is the
registry entry `checkin` (`ui.navigation`); every URL here takes the same
guard, `requires_page("checkin")`."""

from django.urls import path

from ui.checkin import views

urlpatterns = [
    path("checkin/list/", views.listing, name="checkin-list"),
    path("checkin/number/", views.number, name="checkin-number"),
    path("checkin/bulk/", views.bulk, name="checkin-bulk"),
    path("checkin/qr/", views.qr_sheet, name="checkin-qr"),
    path("checkin/racer/new/", views.racer_new, name="checkin-new"),
    path("checkin/racer/<int:racer_id>/", views.racer_edit, name="checkin-edit"),
    path(
        "checkin/racer/<int:racer_id>/delete/",
        views.racer_delete,
        name="checkin-delete",
    ),
    path("checkin/racer/<int:racer_id>/pass/", views.pass_racer, name="checkin-pass"),
    path("checkin/racer/<int:racer_id>/weigh/", views.weigh, name="checkin-weigh"),
    path("checkin/weigh/", views.weigh_fields, name="checkin-weigh-fields"),
    path(
        "checkin/racer/<int:racer_id>/round/",
        views.question_sheet,
        name="checkin-question",
    ),
    path(
        "checkin/racer/<int:racer_id>/round/<str:choice>/",
        views.answer,
        name="checkin-answer",
    ),
    path(
        "checkin/racer/<int:racer_id>/final/",
        views.final_request,
        name="checkin-final",
    ),
    path("checkin/racer/<int:racer_id>/xbs/", views.xbs, name="checkin-xbs"),
    path(
        "checkin/racer/<int:racer_id>/photo/<str:repo>/",
        views.photo_sheet,
        name="checkin-photo",
    ),
    path(
        "checkin/racer/<int:racer_id>/photo/<str:repo>/upload/",
        views.photo_upload,
        name="checkin-upload",
    ),
    path(
        "checkin/racer/<int:racer_id>/photo/<str:repo>/crop/",
        views.crop_sheet,
        name="checkin-crop-sheet",
    ),
    path(
        "checkin/racer/<int:racer_id>/photo/<str:repo>/crop/save/",
        views.crop,
        name="checkin-crop",
    ),
    path(
        "checkin/racer/<int:racer_id>/photo/<str:repo>/rotate/<str:direction>/",
        views.rotate,
        name="checkin-rotate",
    ),
    path(
        "checkin/partition/<int:partition_id>/range/",
        views.add_range,
        name="checkin-range",
    ),
]
