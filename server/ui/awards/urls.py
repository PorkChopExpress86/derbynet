"""The awards editor's sheet, partial and action URLs (#45). The page itself
is the registry entry `awards-editor` (`ui.navigation`); every URL here
takes the same guard, `requires_page("awards-editor")`."""

from django.urls import path

from ui.awards import views

urlpatterns = [
    path("awards/edit/new/", views.award_new, name="awards-editor-new"),
    path("awards/edit/<int:award_id>/", views.award_edit, name="awards-editor-award"),
    path(
        "awards/edit/<int:award_id>/delete/",
        views.award_delete,
        name="awards-editor-delete",
    ),
    path("awards/edit/partitions/", views.partitions, name="awards-editor-partitions"),
    path("awards/edit/order/", views.order, name="awards-editor-order"),
]
