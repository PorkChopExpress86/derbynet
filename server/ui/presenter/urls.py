"""The awards presentation console's poll and button URLs (#45). The page
itself is the registry entry `awards-presentation` (`ui.navigation`); every
URL here takes the same guard, `requires_page("awards-presentation")`."""

from django.urls import path

from ui.presenter import views

urlpatterns = [
    path("awards/present/current/", views.current, name="presenter-current"),
    path("awards/present/award/", views.present, name="presenter-present"),
    path("awards/present/next/", views.next_award, name="presenter-next"),
    path("awards/present/reveal/", views.reveal, name="presenter-reveal"),
    path("awards/present/clear/", views.clear, name="presenter-clear"),
    path("awards/present/calc-rule/", views.calc_rule, name="presenter-calc-rule"),
]
