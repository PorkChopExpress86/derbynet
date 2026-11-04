"""Results by racer's poll (#49). The page is the registry entry
`racer-results` (`ui.navigation`), and the poll takes its guard."""

from django.urls import path

from ui.results import views

urlpatterns = [
    path("results/rounds/", views.rounds, name="results-rounds"),
]
