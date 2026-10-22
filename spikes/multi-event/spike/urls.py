from django.conf import settings
from django.urls import path

if settings.MODE == "a":
    from spike import views_a as views
else:
    from spike import views_b as views

urlpatterns = [
    path("kiosk/poll", views.kiosk_poll),
    path("coord/poll", views.coord_poll),
    path("timer/heartbeat", views.timer_heartbeat),
    path("switch", views.switch),
]
