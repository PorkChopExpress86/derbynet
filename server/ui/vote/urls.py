"""The vote page's status poll (#54) and the vote link of a racer's QR code
(#206, D86). The page itself is the registry entry `vote`
(`ui.navigation`), which `/vote.php` reaches with a 301 (D20).

The link takes any text after `vote/code/`, slashes and nothing included, so
every bad code reaches the view's one page."""

from django.urls import path, re_path

from ui.vote import views

urlpatterns = [
    path("ballot/status", views.status, name="ballot-status"),
    re_path(r"^vote/code/(?P<token>.*)$", views.token_link, name="vote-code"),
]
