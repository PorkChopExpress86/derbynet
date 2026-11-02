"""The racing groups editor's sheet and action URLs (#43). The page itself
is the registry entry `racing-groups` (`ui.navigation`); every URL here takes
the same guard, `requires_page("racing-groups")`."""

from django.urls import path

from ui.groups import views

urlpatterns = [
    path("groups/rule/", views.rule, name="groups-rule"),
    path("groups/setting/", views.setting, name="groups-setting"),
    path("groups/calc-rule/", views.calc_rule, name="groups-calc-rule"),
    path("groups/pack-awards/", views.pack_awards, name="groups-pack-awards"),
    path("groups/order/groups/", views.order_groups, name="groups-order-groups"),
    path(
        "groups/order/subgroups/",
        views.order_subgroups,
        name="groups-order-subgroups",
    ),
    path(
        "groups/order/partitions/",
        views.order_partitions,
        name="groups-order-partitions",
    ),
    path("groups/group/new/", views.group_new, name="groups-group-new"),
    path("groups/group/<int:group_id>/", views.group_edit, name="groups-group"),
    path(
        "groups/group/<int:group_id>/delete/",
        views.group_delete,
        name="groups-group-delete",
    ),
    path("groups/aggregate/new/", views.aggregate_new, name="groups-aggregate-new"),
    path(
        "groups/subgroup/<int:subgroup_id>/",
        views.subgroup_edit,
        name="groups-subgroup",
    ),
    path(
        "groups/subgroup/<int:subgroup_id>/delete/",
        views.subgroup_delete,
        name="groups-subgroup-delete",
    ),
    path(
        "groups/subgroup/<int:subgroup_id>/move/",
        views.subgroup_move,
        name="groups-subgroup-move",
    ),
    path("groups/partitions/", views.partitions, name="groups-partitions"),
    path("groups/partitions/add/", views.partition_add, name="groups-partition-add"),
    path(
        "groups/partition/<int:partition_id>/",
        views.partition_edit,
        name="groups-partition",
    ),
    path(
        "groups/partition/<int:partition_id>/delete/",
        views.partition_delete,
        name="groups-partition-delete",
    ),
    path(
        "groups/partition/<int:partition_id>/range/",
        views.range_add,
        name="groups-range-add",
    ),
    path(
        "groups/range/<int:range_id>/remove/",
        views.range_remove,
        name="groups-range-remove",
    ),
]
