"""The set-up page's actions (#48). The page itself is the registry entry
`setup`, and the fake roster sheet the entry `fakeroster` (`ui.navigation`)."""

from django.urls import path

from ui.setup import views

urlpatterns = [
    path("setup/event/new/", views.event_new, name="setup-event-new"),
    path("setup/event/open/", views.event_open, name="setup-event-open"),
    path("setup/migrate/", views.migrate, name="setup-migrate"),
    path("setup/login/", views.login_post_setup, name="setup-login"),
    path("setup/purge/", views.purge, name="setup-purge"),
    path("setup/preferences/", views.preferences_upload, name="setup-preferences"),
    path("setup/snapshot/", views.snapshot_import, name="setup-snapshot-import"),
    path("setup/snapshots/<str:name>", views.snapshot_file, name="setup-snapshot"),
]
