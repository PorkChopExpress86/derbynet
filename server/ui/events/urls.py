"""The events page's actions (#212). The page itself is the registry entry
`events` (`ui.navigation`)."""

from django.urls import path

from ui.events import views

urlpatterns = [
    path("setup/events/new/", views.create, name="events-create"),
    path("setup/events/<int:pk>/switch/", views.switch, name="events-switch"),
    path("setup/events/<int:pk>/archive/", views.archive, name="events-archive"),
    path("setup/events/<int:pk>/reopen/", views.reopen, name="events-reopen"),
    path("setup/events/<int:pk>/export.zip", views.export, name="events-export"),
]
