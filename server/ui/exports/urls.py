"""The export files (#49). The page is the registry entry `export`
(`ui.navigation`), and each file takes its guard."""

from django.urls import path

from ui.exports import views

urlpatterns = [
    path("export/everything.<str:kind>", views.everything, name="export-everything"),
    path("export/sheet/<int:index>.csv", views.sheet, name="export-sheet"),
]
