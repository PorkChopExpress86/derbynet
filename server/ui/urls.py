from django.urls import path

from ui import navigation, redirects, views

urlpatterns = [
    *navigation.urlpatterns(),
    *redirects.urlpatterns(),
    path("quickref.pdf", views.quickref, name="quickref"),
    path("components/", views.components, name="components"),
    path(
        "components/demo/<str:outcome>/", views.components_demo, name="components-demo"
    ),
]
