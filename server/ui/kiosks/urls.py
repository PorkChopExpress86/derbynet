"""The kiosk dashboard's poll, sheet and action URLs (#46). The page itself
is the registry entry `kiosk-dashboard` (`ui.navigation`); every URL here
takes the same guard, `requires_page("kiosk-dashboard")`. A kiosk is named
by its `address` field, which can hold any text."""

from django.urls import path

from ui.kiosks import views

urlpatterns = [
    path("kiosks/board/", views.board, name="kiosks-board"),
    path("kiosks/scene/", views.scene, name="kiosks-scene"),
    path("kiosks/page/", views.page_sheet, name="kiosks-page"),
    path("kiosks/name/", views.name_sheet, name="kiosks-name"),
    path("kiosks/configure/", views.configure, name="kiosks-configure"),
    path("kiosks/reload/", views.reload, name="kiosks-reload"),
    path("kiosks/identify/", views.identify, name="kiosks-identify"),
]
