from django.urls import include, path

from ui import navigation, redirects, views

urlpatterns = [
    *navigation.urlpatterns(),
    path("", include("ui.dashboard.urls")),
    path("", include("ui.checkin.urls")),
    path("", include("ui.groups.urls")),
    path("", include("ui.playlist.urls")),
    path("", include("ui.awards.urls")),
    path("", include("ui.judging.urls")),
    path("", include("ui.presenter.urls")),
    path("", include("ui.kiosks.urls")),
    path("", include("ui.scenes.urls")),
    path("", include("ui.photos.urls")),
    path("", include("ui.results.urls")),
    path("", include("ui.standings.urls")),
    path("", include("ui.exports.urls")),
    path("", include("ui.imports.urls")),
    *redirects.urlpatterns(),
    path("quickref.pdf", views.quickref, name="quickref"),
    path("components/", views.components, name="components"),
    path(
        "components/demo/<str:outcome>/", views.components_demo, name="components-demo"
    ),
]
