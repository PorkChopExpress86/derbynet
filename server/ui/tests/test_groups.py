"""The racing groups editor's views (#43): the guard and the redirect, the
formation rule and its warning (D31), the labels and trophy counts, the pack
choices, the speed-award partition lists (D35, D38), ordering, moving a
subgroup, groups, subgroups, aggregates, partitions and their car number
ranges (D48), and the re-rendered editor every change returns."""

import re

import pytest

from awards.models import AwardPartition
from awards.tests.factories import AwardFactory
from core.models import FullFieldRule, RaceSettings
from racing.models import PlaylistEntry, Round
from racing.tests.factories import PlaylistEntryFactory
from roster.models import CarRange, Group, GroupConstituentSubgroup, Partition, Subgroup
from roster.services import groups
from ui.tests.db import no_db_writes
from ui.tests.race import add_racers, race_setup, run_heat, scheduled
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


def post(client, url, data=None, target="toasts"):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target)


def sheet_post(client, url, data=None):
    return post(client, url, data, target="groups-sheet-body")


def get(client, url, data=None):
    return client.get(url, data or {}, HTTP_HX_REQUEST="true")


def html(response) -> str:
    return response.content.decode()


def dens(event, names=("Lions", "Tigers", "Parents")):
    for i, name in enumerate(names, 1):
        add_racers(event, name, 2, 100 * i + 1, passed=False)


def group(event, name) -> Group:
    return Group.objects.get(event=event, name=name)


def subgroup(event, name) -> Subgroup:
    return Subgroup.objects.get(event=event, name=name)


def partition(event, name) -> Partition:
    return Partition.objects.get(event=event, name=name)


def editor(response) -> str:
    """The re-rendered editor a change sends out of band."""
    found = re.search(
        r'<div id="groups-editor" hx-swap-oob="innerHTML">(.*)</div>\s*$',
        html(response),
        re.S,
    )
    assert found, html(response)[:500]
    return found.group(1)


def settings(event) -> RaceSettings:
    return RaceSettings.objects.get(event=event)


def names_in_order(model, event) -> list[str]:
    return list(
        model.objects.filter(event=event)
        .order_by("sort_order")
        .values_list("name", flat=True)
    )


# ----- the page, the guard, the redirect -----


def test_the_page_needs_set_up(login, event):
    assert login("RaceCoordinator").get("/groups/").status_code == 200
    assert login("RaceCrew").get("/groups/").status_code in (302, 403)
    refused = post(login("RaceCrew"), "/groups/partitions/add/", {"name": "X"})
    assert refused.status_code == 403


@pytest.mark.parametrize("old", ["/racing-groups.php", "/derbynet/racing-groups.php"])
def test_racing_groups_php_moves_to_the_editor(client, event, old):
    response = client.get(old)
    assert response.status_code == 301
    assert response["Location"] == "/groups/"


def test_the_page_shows_the_tree_and_writes_nothing(coordinator, event):
    dens(event)
    with no_session_writes(), no_db_writes():
        page = html(coordinator.get("/groups/"))
    for name in ("Lions", "Tigers", "Parents"):
        assert f'data-label="{name}"' in page
    assert 'id="g-rule-by-partition" name="rule" value="by-partition" checked' in page


def test_the_partitions_url_opens_the_editor_with_the_sheet(coordinator, event):
    dens(event)
    page = html(coordinator.get("/groups/partitions/"))
    assert 'data-open-sheet="/groups/partitions/"' in page
    body = html(get(coordinator, "/groups/partitions/"))
    assert "data-partitions-sheet" in body and "<html" not in body


# ----- the formation rule (D31) -----


def test_the_rule_in_force_opens_no_sheet_and_changes_nothing(coordinator, event):
    dens(event)
    with no_session_writes(), no_db_writes():
        body = html(get(coordinator, "/groups/rule/", {"rule": "by-partition"}))
    assert "$store.ui.close()" in body and "data-rule-confirm" not in body


def test_the_warning_counts_what_a_rule_change_deletes(coordinator, event):
    rounds = race_setup(event, groups=(("Lions", 4), ("Tigers", 4)))
    scheduled(rounds["Lions"], race=False)
    Round.objects.create(event=event, group=group(event, "Lions"), number=2)
    AwardFactory(event=event, group=group(event, "Tigers"))
    PlaylistEntryFactory(group=group(event, "Lions"), round_number=1)

    body = html(get(coordinator, "/groups/rule/", {"rule": "one-group"}))
    counts = dict(re.findall(r'data-delete="(\w+)"><strong[^>]*>(\d+)</strong>', body))
    assert counts == {
        "rounds": "3",
        "schedules": "1",
        "group_awards": "1",
        "playlist_steps": "1",
    }

    old_rounds = set(Round.objects.filter(event=event).values_list("id", flat=True))
    response = sheet_post(coordinator, "/groups/rule/", {"rule": "one-group"})
    assert response.status_code == 200
    assert settings(event).group_formation_rule == "one-group"
    assert not Round.objects.filter(id__in=old_rounds).exists()
    assert not PlaylistEntry.objects.filter(event=event).exists()
    assert 'value="one-group" checked' in editor(response)


def test_custom_deletes_nothing(coordinator, event):
    dens(event)
    body = html(get(coordinator, "/groups/rule/", {"rule": "custom"}))
    assert "data-deletes-nothing" in body
    sheet_post(coordinator, "/groups/rule/", {"rule": "custom"})
    assert settings(event).group_formation_rule == "custom"


def test_a_rule_change_after_a_result_is_refused(coordinator, event):
    rounds = race_setup(event, groups=(("Lions", 4),))
    scheduled(rounds["Lions"])
    run_heat(event)
    response = sheet_post(coordinator, "/groups/rule/", {"rule": "one-group"})
    assert response.status_code == 422
    assert "Racing results already exist." in html(response)
    assert settings(event).group_formation_rule == "by-partition"


def test_an_unknown_rule_is_refused(coordinator, event):
    assert get(coordinator, "/groups/rule/", {"rule": "by-den"}).status_code == 422
    assert post(coordinator, "/groups/rule/", {"rule": "by-den"}).status_code == 422


# ----- labels, trophies, the pack's choices -----


def test_labels_and_trophy_counts_save_one_field_at_a_time(coordinator, event):
    dens(event)
    response = post(coordinator, "/groups/setting/", {"partition-label": "Patrol"})
    assert response.status_code == 200
    assert "Patrols" in editor(response)
    post(coordinator, "/groups/setting/", {"supergroup-label": "Troop"})
    post(coordinator, "/groups/setting/", {"n-den-trophies": "4"})
    post(coordinator, "/groups/setting/", {"n-rank-trophies": "2"})
    found = settings(event)
    assert (found.partition_label, found.supergroup_label) == ("Patrol", "Troop")
    assert (found.n_den_trophies, found.n_rank_trophies) == (4, 2)


def test_the_use_subgroups_switch(coordinator, event):
    post(coordinator, "/groups/setting/", {"do-use-subgroups": "1"})
    assert settings(event).use_subgroups is True
    post(coordinator, "/groups/setting/", {"do-use-subgroups": "0"})
    assert settings(event).use_subgroups is False


def test_a_bad_trophy_count_is_refused(coordinator, event):
    bad = post(coordinator, "/groups/setting/", {"n-pack-trophies": "x"})
    assert bad.status_code == 422
    assert post(coordinator, "/groups/setting/", {}).status_code == 422


def test_pack_trophies_need_the_pack_standings(coordinator, event):
    post(coordinator, "/groups/setting/", {"n-pack-trophies": "0"})
    post(coordinator, "/groups/calc-rule/", {"full-field-calc": "-1"})
    assert settings(event).full_field_rule == FullFieldRule.NONE

    post(coordinator, "/groups/setting/", {"n-pack-trophies": "3"})
    assert settings(event).full_field_rule == FullFieldRule.STANDARD
    refused = post(coordinator, "/groups/calc-rule/", {"full-field-calc": "-1"})
    assert refused.status_code == 422


def test_the_pack_can_use_an_aggregates_standings(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "custom")
    both = groups.add_group(
        event, "Cats", constituents=[group(event, "Lions"), group(event, "Tigers")]
    )
    page = html(coordinator.get("/groups/"))
    assert f'value="{both.pk}"' in page and "Use standings from Cats" in page
    post(coordinator, "/groups/calc-rule/", {"full-field-calc": str(both.pk)})
    found = settings(event)
    assert (found.full_field_rule, found.full_field_group_id) == ("group", both.pk)
    bad = post(coordinator, "/groups/calc-rule/", {"full-field-calc": "999"})
    assert bad.status_code == 422


# ----- speed-award partition lists (D35, D38) -----


def stored(event, **unit) -> set[str]:
    rows = AwardPartition.objects.filter(event=event, **unit)
    return {r.partition.name for r in rows}


PACK = {"group": None, "subgroup": None, "award": None}


def test_unchecking_parents_on_the_pack_list_saves_a_list_without_it(
    coordinator, event
):
    dens(event)
    keep = [partition(event, n).pk for n in ("Lions", "Tigers")]
    response = post(
        coordinator, "/groups/pack-awards/", {"scope": "some", "partition": keep}
    )
    assert response.status_code == 200
    assert stored(event, **PACK) == {"Lions", "Tigers"}
    assert 'id="pack-some" name="scope" value="some"' in editor(response)
    assert re.search(r'id="pack-some"[^>]* checked', editor(response))

    post(coordinator, "/groups/pack-awards/", {"scope": "all"})
    assert stored(event, **PACK) == set()


def test_a_narrowed_list_needs_a_partition_the_award_draws_from(coordinator, event):
    dens(event)
    none = post(coordinator, "/groups/pack-awards/", {"scope": "some"})
    assert none.status_code == 422
    other = post(
        coordinator, "/groups/pack-awards/", {"scope": "some", "partition": "999"}
    )
    assert other.status_code == 422


def test_a_subgroup_list_with_every_box_checked_stores_no_list(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    lions = partition(event, "Lions")
    groups.move_partition(
        partition(event, "Tigers"), groups.GroupRef("rankid", lions.subgroup_id)
    )
    sub = Subgroup.objects.get(pk=lions.subgroup_id)
    every = [lions.pk, partition(event, "Tigers").pk]
    response = sheet_post(
        coordinator,
        f"/groups/subgroup/{sub.pk}/",
        {"name": sub.name, "ntrophies": "-1", "scope": "some", "partition": every},
    )
    assert response.status_code == 200, html(response)
    assert "$store.ui.close()" in html(response)
    assert stored(event, subgroup=sub) == set()

    sheet_post(
        coordinator,
        f"/groups/subgroup/{sub.pk}/",
        {"name": sub.name, "ntrophies": "2", "scope": "some", "partition": [lions.pk]},
    )
    assert stored(event, subgroup=sub) == {"Lions"}
    sub.refresh_from_db()
    assert sub.n_trophies == 2


def test_a_group_sheet_offers_only_the_groups_partitions(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "custom")
    lions = group(event, "Lions")
    groups.move_partition(
        partition(event, "Tigers"), groups.GroupRef("classid", lions.pk)
    )
    body = html(get(coordinator, f"/groups/group/{lions.pk}/"))
    offered = re.findall(r'name="partition" value="(\d+)"', body)
    in_lions = Partition.objects.filter(subgroup__group=lions)
    assert sorted(map(int, offered)) == sorted(in_lions.values_list("pk", flat=True))
    assert partition(event, "Tigers").pk in map(int, offered)
    assert partition(event, "Parents").pk not in map(int, offered)


# ----- groups, subgroups, aggregates -----


def test_a_group_sheet_saves_its_name_and_trophies(coordinator, event):
    dens(event)
    lions = group(event, "Lions")
    response = sheet_post(
        coordinator, f"/groups/group/{lions.pk}/", {"name": "Cubs", "ntrophies": "5"}
    )
    lions.refresh_from_db()
    assert (lions.name, lions.n_trophies) == ("Cubs", 5)
    assert 'data-label="Cubs"' in editor(response)
    assert "$store.ui.close()" in html(response)

    sheet_post(
        coordinator, f"/groups/group/{lions.pk}/", {"name": "Cubs", "ntrophies": "-1"}
    )
    lions.refresh_from_db()
    assert lions.n_trophies is None


def test_a_taken_name_stays_in_the_sheet(coordinator, event):
    dens(event)
    lions = group(event, "Lions")
    response = sheet_post(
        coordinator, f"/groups/group/{lions.pk}/", {"name": "tigers", "ntrophies": "-1"}
    )
    assert "already in use" in html(response)
    assert "data-group-sheet" in html(response)


def test_a_bad_trophy_choice_stays_in_the_sheet(coordinator, event):
    dens(event)
    lions = group(event, "Lions")
    response = sheet_post(
        coordinator, f"/groups/group/{lions.pk}/", {"name": "Lions", "ntrophies": "99"}
    )
    assert "Pick a number of trophies." in html(response)


def test_a_group_with_racers_offers_no_delete(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "custom")
    lions = group(event, "Lions")
    sheet = html(get(coordinator, f"/groups/group/{lions.pk}/"))
    assert 'data-action="delete"' not in sheet
    empty = groups.add_group(event, "Empty")
    sheet = html(get(coordinator, f"/groups/group/{empty.pk}/"))
    assert 'data-action="delete"' in sheet
    sheet_post(coordinator, f"/groups/group/{empty.pk}/delete/")
    assert not Group.objects.filter(pk=empty.pk).exists()
    refused = sheet_post(coordinator, f"/groups/group/{lions.pk}/delete/")
    assert refused.status_code == 422


def test_a_subgroup_delete(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "custom")
    lions = group(event, "Lions")
    cubs = groups.add_subgroup(event, lions.pk, "Cubs")
    assert 'data-action="delete"' in html(
        get(coordinator, f"/groups/subgroup/{cubs.pk}/")
    )
    sheet_post(coordinator, f"/groups/subgroup/{cubs.pk}/delete/")
    assert not Subgroup.objects.filter(pk=cubs.pk).exists()
    refused = sheet_post(
        coordinator, f"/groups/subgroup/{subgroup(event, 'Lions').pk}/delete/"
    )
    assert refused.status_code == 422


def test_d49_a_subgroup_rename_under_by_partition_renames_the_group(coordinator, event):
    dens(event)
    sub = subgroup(event, "Lions")
    response = sheet_post(
        coordinator, f"/groups/subgroup/{sub.pk}/", {"name": "Cubs", "ntrophies": "-1"}
    )
    assert response.status_code == 200
    assert Group.objects.filter(event=event, name="Cubs").exists()
    assert partition(event, "Cubs").pk
    assert 'data-label="Cubs"' in editor(response)


def test_a_subgroup_moves_through_move_to(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    lions = subgroup(event, "Lions")
    sheet = html(get(coordinator, f"/groups/subgroup/{lions.pk}/move/"))
    assert 'data-target-group="new"' in sheet

    url = f"/groups/subgroup/{lions.pk}/move/"
    response = sheet_post(coordinator, url, {"group": "new"})
    lions.refresh_from_db()
    assert lions.group.name == "Lions"
    assert f'data-subgroups-of="{lions.group_id}"' in editor(response)

    all_racers = group(event, "All Racers")
    sheet_post(coordinator, url, {"group": all_racers.pk})
    lions.refresh_from_db()
    assert lions.group == all_racers
    assert sheet_post(coordinator, url, {"group": "x"}).status_code == 422


def test_a_new_group_and_an_aggregate(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "custom")
    sheet_post(coordinator, "/groups/group/new/", {"name": "Bears", "ntrophies": "2"})
    assert group(event, "Bears").n_trophies == 2

    lions, tigers = group(event, "Lions").pk, group(event, "Tigers").pk
    one = sheet_post(
        coordinator,
        "/groups/aggregate/new/",
        {"name": "Cats", "by": "group", "group": [lions]},
    )
    assert "Pick two or more" in html(one)
    response = sheet_post(
        coordinator,
        "/groups/aggregate/new/",
        {"name": "Cats", "by": "group", "group": [lions, tigers], "ntrophies": "1"},
    )
    cats = group(event, "Cats")
    assert groups.is_aggregate(cats.pk) and cats.n_trophies == 1
    assert f'data-aggregate="{cats.pk}"' in editor(response)


def test_an_aggregate_by_subgroup(coordinator, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    picked = [subgroup(event, "Lions").pk, subgroup(event, "Tigers").pk]
    sheet_post(
        coordinator,
        "/groups/aggregate/new/",
        {"name": "Cats", "by": "subgroup", "subgroup": picked},
    )
    links = GroupConstituentSubgroup.objects.filter(aggregate=group(event, "Cats"))
    assert set(links.values_list("subgroup_id", flat=True)) == set(picked)


# ----- ordering -----


def test_groups_subgroups_and_partitions_reorder(coordinator, event):
    dens(event)
    names = ["Parents", "Lions", "Tigers"]
    ids = [group(event, n).pk for n in names]
    response = post(
        coordinator, "/groups/order/groups/", {"order": ",".join(map(str, ids))}
    )
    assert response.status_code == 200
    assert names_in_order(Group, event) == names
    assert re.findall(r'data-group="(\d+)"', editor(response)) == list(map(str, ids))

    order = ["Tigers", "Parents", "Lions"]
    pids = [partition(event, n).pk for n in order]
    post(coordinator, "/groups/order/partitions/", {"order": ",".join(map(str, pids))})
    assert names_in_order(Partition, event) == order

    groups.form_groups_by_rule(event, "one-group")
    order = ["Parents", "Lions", "Tigers"]
    sids = [subgroup(event, n).pk for n in order]
    post(coordinator, "/groups/order/subgroups/", {"order": ",".join(map(str, sids))})
    assert names_in_order(Subgroup, event) == order


def test_an_order_naming_nothing_is_refused(coordinator, event):
    for kind in ("groups", "subgroups", "partitions"):
        refused = post(coordinator, f"/groups/order/{kind}/", {"order": "999"})
        assert refused.status_code == 422


# ----- partitions and car number ranges -----


def test_a_partition_name_taken_with_case_ignored_shows_in_the_sheet(
    coordinator, event
):
    dens(event)
    response = sheet_post(coordinator, "/groups/partitions/add/", {"name": "lions"})
    assert response.status_code == 200
    assert 'data-error="name-taken"' in html(response)
    assert Partition.objects.filter(event=event).count() == 3


def test_partition_add_rename_and_delete(coordinator, event):
    dens(event)
    added = sheet_post(coordinator, "/groups/partitions/add/", {"name": "Bears"})
    assert "data-partitions-sheet" in html(added)
    assert 'data-label="Bears"' in editor(added)
    bears = partition(event, "Bears")
    rounds = set(Round.objects.filter(event=event).values_list("id", flat=True))
    sheet_post(coordinator, f"/groups/partition/{bears.pk}/", {"name": "Wolves"})
    assert partition(event, "Wolves").pk == bears.pk
    assert set(Round.objects.filter(event=event).values_list("id", flat=True)) == rounds
    sheet_post(coordinator, f"/groups/partition/{bears.pk}/delete/")
    assert not Partition.objects.filter(pk=bears.pk).exists()
    lions = partition(event, "Lions")
    refused = sheet_post(coordinator, f"/groups/partition/{lions.pk}/delete/")
    assert refused.status_code == 422


def test_car_number_ranges(coordinator, event):
    dens(event)
    lions = partition(event, "Lions")
    body = html(
        sheet_post(
            coordinator,
            f"/groups/partition/{lions.pk}/range/",
            {"range_first": "100", "range_last": "199"},
        )
    )
    assert 'data-range="100-199"' in body
    tigers = partition(event, "Tigers")
    clash = html(
        sheet_post(
            coordinator,
            f"/groups/partition/{tigers.pk}/range/",
            {"range_first": "150", "range_last": "250"},
        )
    )
    assert "overlaps 100 to 199" in clash
    car_range = CarRange.objects.get(partition=lions)
    sheet_post(coordinator, f"/groups/range/{car_range.pk}/remove/")
    assert not CarRange.objects.filter(partition=lions).exists()


# ----- D50 -----


def test_the_partition_layer_shows_only_outside_by_partition(coordinator, event):
    dens(event)
    assert "data-partitions-of" not in html(coordinator.get("/groups/"))
    groups.form_groups_by_rule(event, "one-group")
    assert "data-partitions-of" in html(coordinator.get("/groups/"))
