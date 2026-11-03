"""The scene editor's sheet and action URLs (#46). The page itself is the
registry entry `scenes` (`ui.navigation`); every URL here takes the same
guard, `requires_page("scenes")`."""

from django.urls import path

from ui.scenes import views

urlpatterns = [
    path("kiosks/scenes/new/", views.new, name="scenes-new"),
    path("kiosks/scenes/delete/", views.delete, name="scenes-delete"),
    path("kiosks/scenes/name/", views.add_name, name="scenes-name"),
    path("kiosks/scenes/page/", views.page_sheet, name="scenes-page"),
    path("kiosks/scenes/configure/", views.configure, name="scenes-configure"),
]
