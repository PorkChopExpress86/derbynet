"""The import flow's Preview and Import (#49). The pages are the registry
entries `import-roster`, `import-awards` and `import-results`
(`ui.navigation`); each URL takes the guard of its kind's page."""

from django.urls import path

from ui.imports import views

urlpatterns = [
    path("import/<str:kind>/preview/", views.preview, name="import-preview"),
    path("import/<str:kind>/run/", views.run, name="import-run"),
]
