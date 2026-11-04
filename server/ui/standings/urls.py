"""The standings list a catalog choice fetches (#49). The page is the
registry entry `standings` (`ui.navigation`), and the list takes its
guard."""

from django.urls import path

from ui.standings import views

urlpatterns = [
    path("standings/list/", views.listing, name="standings-list"),
]
