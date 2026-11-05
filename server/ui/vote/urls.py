"""The vote page's status poll (#54). The page itself is the registry entry
`vote` (`ui.navigation`), which `/vote.php` reaches with a 301 (D20)."""

from django.urls import path

from ui.vote import views

urlpatterns = [
    path("ballot/status", views.status, name="ballot-status"),
]
