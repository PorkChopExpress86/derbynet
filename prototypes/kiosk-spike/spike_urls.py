"""SPIKE #71 ONLY: the skeleton's URLs plus the prototypes under /spike/."""

from django.urls import include, path

from derbynet.urls import urlpatterns as skeleton

urlpatterns = [*skeleton, path("spike/", include("spike.urls"))]
