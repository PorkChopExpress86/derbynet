"""Photo management's partial and action URLs (#47). The pages are the
registry entries `racer-photos` and `car-photos` (`ui.navigation`); every URL
here takes their guard, `requires_page("racer-photos")`, and names the
repository, `head` or `car`."""

from django.urls import path

from ui.photos import views

urlpatterns = [
    path("photos/<str:repo>/list/", views.listing, name="photos-list"),
    path(
        "photos/<str:repo>/racer/<int:racer_id>/photos/",
        views.photo_choices,
        name="photos-photo-choices",
    ),
    path("photos/<str:repo>/racers/", views.racer_choices, name="photos-racer-choices"),
    path(
        "photos/<str:repo>/racers/list/",
        views.racer_choices_list,
        name="photos-racer-choices-list",
    ),
    path("photos/<str:repo>/assign/", views.assign, name="photos-assign"),
    path(
        "photos/<str:repo>/racer/<int:racer_id>/unassign/",
        views.unassign,
        name="photos-unassign",
    ),
    path("photos/<str:repo>/crop/", views.crop_sheet, name="photos-crop-sheet"),
    path("photos/<str:repo>/crop/save/", views.crop, name="photos-crop"),
    path(
        "photos/<str:repo>/rotate/<str:direction>/",
        views.rotate,
        name="photos-rotate",
    ),
    path("photos/<str:repo>/delete/", views.delete, name="photos-delete"),
    path("photos/<str:repo>/upload/", views.upload, name="photos-upload"),
    path(
        "photos/<str:repo>/racer/<int:racer_id>/capture/",
        views.capture_sheet,
        name="photos-capture",
    ),
    path(
        "photos/<str:repo>/racer/<int:racer_id>/capture/upload/",
        views.capture_upload,
        name="photos-capture-upload",
    ),
]
