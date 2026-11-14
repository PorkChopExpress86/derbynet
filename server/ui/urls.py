from django.urls import include, path

from ui import navigation, redirects, views
from ui.docs import views as docs_views

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
    path("", include("ui.timer.urls")),
    path("", include("ui.kiosk_pages.urls")),
    path("", include("ui.vote.urls")),
    path("", include("ui.settings_page.urls")),
    path("", include("ui.roles.urls")),
    path("", include("ui.setup.urls")),
    path("", include("ui.events.urls")),
    *redirects.urlpatterns(),
    path("docs/img/<path:name>", docs_views.image, name="docs-image"),
    path("components/", views.components, name="components"),
    path(
        "components/demo/<str:outcome>/", views.components_demo, name="components-demo"
    ),
]
