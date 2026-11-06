from django.urls import path

from ui.roles import views

urlpatterns = [
    path(
        "settings/roles/<str:name>/password/",
        views.password,
        name="roles-password",
    ),
]
