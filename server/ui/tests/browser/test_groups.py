"""The racing groups editor in a browser (#43): the acceptance flows at
390x844 and 1280x800, the racing-groups.php and partitions-modal.js parity
boxes, and the screenshots."""

import re

import pytest
from playwright.sync_api import expect

from awards.models import Award, AwardPartition
from awards.tests.factories import AwardFactory
from core.models import FullFieldRule, RaceSettings
from racing.models import HeatLane, PlaylistEntry, Round
from racing.tests.factories import PlaylistEntryFactory
from roster.models import Group, Partition, Subgroup
from roster.services import groups
from ui.tests.browser.conftest import (
    assert_accessible,
    htmx_post,
    no_sideways_scroll,
    shoot,
)
from ui.tests.race import add_racers, race_setup, scheduled


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check is at 390x844")


def dens(event, names=("Lions", "Tigers", "Parents")):
    for i, name in enumerate(names, 1):
        add_racers(event, name, 2, 100 * i + 1, passed=False)


def open_groups(page):
    page.goto("/groups/")
    expect(page.locator("#groups-editor [data-section=tree]")).to_be_visible()
    return page


def sheet(page):
    return page.locator('[data-sheet="groups"]')


def dismiss_toasts(page):
    toasts = page.locator("#toasts").get_by_role("button", name="Dismiss")
    while toasts.count():
        toasts.first.click()


def page_of(login):
    return login("RaceCoordinator")


def rule(page, label):
    page.locator("[data-section=rule]").get_by_text(label, exact=True).click()


def row_menu(page, name, kind="subgroup"):
    """The row menu of the subgroup, or with kind "group" or "den", the
    group, named so for the screen reader."""
    page.get_by_role("button", name=f"Actions for {kind} {name}", exact=True).click()


def menu_item(page, name):
    page.get_by_role("menuitem", name=name).locator("visible=true").click()


def saved(page):
    return htmx_post(page, "/groups/")


def order(event, model, **where) -> list[str]:
    return list(
        model.objects.filter(event=event, **where)
        .order_by("sort_order")
        .values_list("name", flat=True)
    )


# ----- D31: the rule change and its warning ----------------------------------------


def test_a_rule_change_warns_what_it_deletes(login, event):
    """At 390x844: the warning names the rounds, schedules, group awards and
    playlist steps a change deletes, and the counts match the rows gone
    afterwards. The rule in force sends nothing."""

    page = page_of(login)
    phone_only(page)
    rounds = race_setup(event, groups=(("Lions", 4), ("Tigers", 4)))
    scheduled(rounds["Lions"], race=False)
    Round.objects.create(event=event, group=rounds["Lions"].group, number=2)
    AwardFactory(event=event, group=rounds["Tigers"].group)
    AwardFactory(event=event)
    PlaylistEntryFactory(group=rounds["Lions"].group, round_number=1)
    open_groups(page)

    requests = []
    page.on("request", lambda r: "/groups/rule/" in r.url and requests.append(r))
    rule(page, "By Den")
    page.wait_for_timeout(300)
    assert requests == []
    expect(sheet(page)).to_be_hidden()

    def rows():
        return {
            "rounds": set(
                Round.objects.filter(event=event).values_list("id", flat=True)
            ),
            "schedules": set(
                HeatLane.objects.filter(event=event).values_list("round_id", flat=True)
            ),
            "group_awards": set(
                Award.objects.filter(event=event)
                .exclude(group__isnull=True, subgroup__isnull=True)
                .values_list("id", flat=True)
            ),
            "playlist_steps": set(
                PlaylistEntry.objects.filter(event=event).values_list("id", flat=True)
            ),
        }

    before = rows()
    rule(page, "One group")
    warning = sheet(page).locator("[data-rule-confirm]")
    expect(warning).to_be_visible()
    shown = {
        kind: int(warning.locator(f'[data-delete="{kind}"] strong').inner_text())
        for kind in before
    }
    shoot(page, "groups-rule-sheet", full_page=False)

    sheet(page).get_by_role("button", name="Delete and change").click()
    expect(sheet(page)).to_be_hidden()
    expect(page.locator("#g-rule-one-group")).to_be_checked()
    after = rows()
    assert shown == {kind: len(before[kind] - after[kind]) for kind in before}
    assert shown == {
        "rounds": 3,
        "schedules": 1,
        "group_awards": 1,
        "playlist_steps": 1,
    }
    assert RaceSettings.objects.get(event=event).group_formation_rule == "one-group"


def test_cancel_puts_the_rule_back(login, event):
    dens(event)
    page = open_groups(page_of(login))
    rule(page, "Custom")
    expect(sheet(page).locator("[data-rule-confirm]")).to_be_visible()
    sheet(page).get_by_role("button", name="Cancel").click()
    expect(sheet(page)).to_be_hidden()
    expect(page.locator("#g-rule-by-partition")).to_be_checked()
    assert RaceSettings.objects.get(event=event).group_formation_rule == "by-partition"


# ----- partitions: name-taken, add, rename, delete, reorder, ranges ----------------


def test_a_partition_named_like_another_shows_name_taken(login, event):
    dens(event)
    page = open_groups(page_of(login))
    page.get_by_role("button", name="Dens", exact=True).click()
    body = sheet(page).locator("[data-partitions-sheet]")
    expect(body).to_be_visible()
    body.get_by_label("New den").fill("lions")
    body.get_by_role("button", name="Add", exact=True).click()
    error = sheet(page).locator('[data-error="name-taken"]')
    expect(error).to_contain_text("lions")
    assert Partition.objects.filter(event=event).count() == 3


def test_check_ins_edit_partitions_link_opens_the_partitions_sheet(login, event):
    dens(event)
    page = page_of(login)
    page.goto("/checkin/")
    page.get_by_role("button", name="New racer").click()
    page.locator('[data-sheet="checkin"]').get_by_role(
        "link", name="Edit partitions"
    ).click()
    page.wait_for_url(re.compile(r"/groups/partitions/$"))
    expect(sheet(page).locator("[data-partitions-sheet]")).to_be_visible()
    expect(sheet(page).locator("[data-partition-row]")).to_have_count(3)
    shoot(page, "groups-partitions-sheet", full_page=False)


def test_partition_add_rename_delete_and_ranges(login, event):
    """Parity with partitions-modal.js, plus the D48 ranges."""
    dens(event)
    page = page_of(login)
    page.goto("/groups/partitions/")
    body = sheet(page)
    body.get_by_label("New den").fill("Bears")
    body.get_by_role("button", name="Add", exact=True).click()
    expect(body.locator("[data-sheet-notice]")).to_have_text("Added Bears.")
    bears = Partition.objects.get(event=event, name="Bears")

    body.locator(f'[data-partition-row="{bears.pk}"] [data-edit-partition]').click()
    body.get_by_label("Den name").fill("Wolves")
    body.get_by_role("button", name="Rename").click()
    expect(body.locator("[data-sheet-notice]")).to_have_text("Renamed to Wolves.")
    assert Partition.objects.get(pk=bears.pk).name == "Wolves"

    body.locator(f'[data-partition-row="{bears.pk}"] [data-edit-partition]').click()
    body.get_by_label("From").fill("500")
    body.get_by_label("To").fill("599")
    body.get_by_role("button", name="Add range").click()
    expect(body.locator('[data-range="500-599"]')).to_be_visible()
    body.get_by_role("button", name="Remove 500 to 599").click()
    expect(body.locator('[data-range="500-599"]')).to_have_count(0)

    body.locator('[data-action="delete"]').click()
    body.get_by_role("button", name="Delete 1 den").click()
    expect(body.locator("[data-sheet-notice]")).to_have_text("Deleted Wolves.")
    assert not Partition.objects.filter(pk=bears.pk).exists()


# ----- D35: the speed-award partition lists ----------------------------------------


def test_the_pack_and_subgroup_partition_lists(login, event):
    """At 390x844: unchecking Parents on the pack's list saves a list
    without it, and a subgroup's list with every box checked stores none."""
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    lions = Partition.objects.get(event=event, name="Lions")
    groups.move_partition(
        Partition.objects.get(event=event, name="Tigers"),
        groups.GroupRef("rankid", lions.subgroup_id),
    )
    page = page_of(login)
    phone_only(page)
    open_groups(page)

    pack = page.locator('[data-partition-list="pack"]')
    pack.get_by_label("Only these").check()
    with saved(page):
        pack.get_by_label("Parents").uncheck()
    expect(page.locator("#pack-some")).to_be_checked()
    stored = AwardPartition.objects.filter(
        event=event, group=None, subgroup=None, award=None
    )
    assert sorted(stored.values_list("partition__name", flat=True)) == [
        "Lions",
        "Tigers",
    ]

    subgroup = Subgroup.objects.get(pk=lions.subgroup_id)
    page.locator(f'[data-subgroup="{subgroup.pk}"] [data-edit-subgroup]').click()
    form = sheet(page).locator("[data-subgroup-sheet]")
    form.get_by_label("Only these").check()
    for box in form.locator('input[name="partition"]').all():
        expect(box).to_be_checked()
    form.get_by_role("button", name="Save").click()
    expect(sheet(page)).to_be_hidden()
    assert not AwardPartition.objects.filter(subgroup=subgroup).exists()


def test_a_partition_moved_into_a_group_joins_its_all_partitions_list(login, event):
    """D38: a group whose list is "All partitions" picks up a partition that
    moves into it, here with its subgroup through "Move to..."."""
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    page = open_groups(page_of(login))
    tigers = Subgroup.objects.get(event=event, name="Tigers")
    row_menu(page, "Tigers")
    menu_item(page, "Move to…")
    with saved(page):
        sheet(page).locator('[data-target-group="new"]').click()
    expect(sheet(page)).to_be_hidden()
    tigers.refresh_from_db()
    new_group = tigers.group

    row_menu(page, "Lions")
    menu_item(page, "Move to…")
    with saved(page):
        sheet(page).locator(f'[data-target-group="{new_group.pk}"]').click()
    expect(sheet(page)).to_be_hidden()

    page.locator(f'[data-group="{new_group.pk}"] [data-edit-group]').first.click()
    form = sheet(page).locator("[data-group-sheet]")
    expect(form.get_by_label("All dens")).to_be_checked()
    expect(form.locator('input[name="partition"]')).to_have_count(2)
    expect(form.get_by_label("Lions", exact=True)).to_be_checked()
    assert not AwardPartition.objects.filter(group=new_group).exists()


# ----- Move to..., reorder ------------------------------------------------------------


def test_a_subgroup_moves_through_the_move_sheet_on_a_phone(login, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    groups.add_group(event, "Bears")
    page = page_of(login)
    phone_only(page)
    open_groups(page)
    row_menu(page, "Lions")
    menu_item(page, "Move to…")
    move = sheet(page).locator("[data-move-sheet]")
    expect(move).to_be_visible()
    expect(move.locator("[data-target-group]")).to_have_text(["Bears", "New group"])
    shoot(page, "groups-move-sheet", full_page=False)
    move.get_by_role("button", name="Bears").click()
    expect(sheet(page)).to_be_hidden()
    bears = Group.objects.get(event=event, name="Bears")
    expect(
        page.locator(f'[data-subgroups-of="{bears.pk}"] [data-label="Lions"]')
    ).to_be_visible()
    assert Subgroup.objects.get(event=event, name="Lions").group == bears


def test_the_move_sheet_on_a_desktop(login, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    page = page_of(login)
    if page.size == "phone":
        pytest.skip("the phone screenshot is in the phone test")
    open_groups(page)
    row_menu(page, "Lions")
    menu_item(page, "Move to…")
    expect(sheet(page).locator("[data-move-sheet]")).to_be_visible()
    shoot(page, "groups-move-sheet", full_page=False)


def test_reorder_groups_subgroups_and_partitions(login, event):
    """By drag handle on a desktop and by the row menu on a phone."""
    dens(event)
    page = open_groups(page_of(login))
    page.wait_for_function("window.Sortable !== undefined")
    if page.size == "desktop":
        with saved(page):
            page.get_by_role("button", name="Drag to reorder Parents").drag_to(
                page.get_by_role("button", name="Drag to reorder Lions")
            )
    else:
        for _ in range(2):
            row_menu(page, "Parents", "den")
            with saved(page):
                menu_item(page, "Move up")
    expect(page.locator("[data-groups-list] > li").first).to_have_attribute(
        "data-label", "Parents"
    )
    assert order(event, Group)[0] == "Parents"

    page.get_by_role("button", name="Dens", exact=True).click()
    body = sheet(page).locator("[data-partitions-sheet]")
    expect(body).to_be_visible()
    first = order(event, Partition)[0]
    last = order(event, Partition)[-1]
    if page.size == "desktop":
        with saved(page):
            body.get_by_role("button", name=f"Drag to reorder {last}").drag_to(
                body.get_by_role("button", name=f"Drag to reorder {first}")
            )
    else:
        for _ in range(2):
            body.get_by_role("button", name=f"Actions for {last}").click()
            with saved(page):
                body.get_by_role("menuitem", name="Move up").click()
    assert order(event, Partition)[0] == last
    body.get_by_role("button", name="Done").click()
    expect(sheet(page)).to_be_hidden()

    groups.form_groups_by_rule(event, "one-group")
    open_groups(page)
    page.wait_for_function("window.Sortable !== undefined")
    names = order(event, Subgroup)
    if page.size == "desktop":
        with saved(page):
            page.get_by_role("button", name=f"Drag to reorder {names[-1]}").drag_to(
                page.get_by_role("button", name=f"Drag to reorder {names[0]}")
            )
    else:
        for _ in range(2):
            row_menu(page, names[-1], "den")
            with saved(page):
                menu_item(page, "Move up")
    assert order(event, Subgroup)[0] == names[-1]


# ----- parity with racing-groups.php ------------------------------------------------


def test_each_rule_forms_the_groups(login, event):
    dens(event)
    page = open_groups(page_of(login))
    rule(page, "One group")
    sheet(page).get_by_role("button", name=re.compile("change", re.I)).click()
    expect(page.locator("#g-rule-one-group")).to_be_checked()
    expect(page.locator("[data-groups-list] > li")).to_have_count(1)
    expect(page.locator("[data-subgroups-of] > li")).to_have_count(3)
    dismiss_toasts(page)

    rule(page, "Custom")
    sheet(page).get_by_role("button", name="Change").click()
    expect(page.locator("#g-rule-custom")).to_be_checked()
    expect(page.get_by_role("button", name="Group", exact=True)).to_be_visible()
    dismiss_toasts(page)

    rule(page, "By Den")
    sheet(page).get_by_role("button", name=re.compile("change", re.I)).click()
    expect(page.locator("#g-rule-by-partition")).to_be_checked()
    expect(page.locator("[data-groups-list] > li")).to_have_count(3)
    expect(page.locator("[data-subgroups-of]")).to_have_count(0)
    assert sorted(Group.objects.filter(event=event).values_list("name", flat=True)) == [
        "Lions",
        "Parents",
        "Tigers",
    ]


def test_labels_switch_and_trophy_counts(login, event):
    dens(event)
    page = open_groups(page_of(login))
    switch = page.get_by_role("switch", name="Use subgroups")
    with saved(page):
        switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    assert RaceSettings.objects.get(event=event).use_subgroups

    page.get_by_label("A sub-division is a").fill("Patrol")
    with saved(page):
        page.get_by_label("A sub-division is a").press("Enter")
    expect(page.get_by_role("button", name="Patrols", exact=True)).to_be_visible()
    page.get_by_label("The whole roster is a").fill("Troop")
    with saved(page):
        page.get_by_label("The whole roster is a").press("Tab")
    expect(page.locator("[data-calc] legend")).to_have_text("Troop standings")
    with saved(page):
        page.get_by_role("button", name="More: Per patrol").click()
    with saved(page):
        page.get_by_role("button", name="Fewer: Troop trophies").click()
    with saved(page):
        page.get_by_role("button", name="More: Per subgroup").click()
    found = RaceSettings.objects.get(event=event)
    assert (found.partition_label, found.supergroup_label) == ("Patrol", "Troop")
    assert (found.n_den_trophies, found.n_pack_trophies, found.n_rank_trophies) == (
        4,
        2,
        1,
    )


def test_the_pack_standings_choice(login, event):
    dens(event)
    RaceSettings.objects.filter(event=event).update(n_pack_trophies=0)
    page = open_groups(page_of(login))
    with saved(page):
        page.get_by_label("Don't calculate").check()
    assert RaceSettings.objects.get(event=event).full_field_rule == FullFieldRule.NONE
    with saved(page):
        page.get_by_role("button", name="More: Pack trophies").click()
    expect(page.get_by_label(re.compile("Don't calculate"))).to_be_disabled()
    expect(page.get_by_label("Calculate normally")).to_be_checked()
    assert (
        RaceSettings.objects.get(event=event).full_field_rule == FullFieldRule.STANDARD
    )


def test_edit_and_delete_groups_and_subgroups(login, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    all_racers = Group.objects.get(event=event, name="All Racers")
    cubs = groups.add_subgroup(event, all_racers.pk, "Cubs")
    empty = groups.add_group(event, "Empty")
    page = open_groups(page_of(login))

    page.locator(f'[data-group="{all_racers.pk}"] [data-edit-group]').first.click()
    form = sheet(page).locator("[data-group-sheet]")
    form.get_by_label("Group name").fill("Everyone")
    form.get_by_label("Speed trophies").select_option("4")
    form.get_by_role("button", name="Save").click()
    expect(sheet(page)).to_be_hidden()
    all_racers.refresh_from_db()
    assert (all_racers.name, all_racers.n_trophies) == ("Everyone", 4)

    page.locator(f'[data-subgroup="{cubs.pk}"] [data-edit-subgroup]').click()
    form = sheet(page).locator("[data-subgroup-sheet]")
    form.get_by_label("Speed trophies").select_option("2")
    form.get_by_role("button", name="Save").click()
    expect(sheet(page)).to_be_hidden()
    cubs.refresh_from_db()
    assert cubs.n_trophies == 2

    page.locator(f'[data-subgroup="{cubs.pk}"] [data-edit-subgroup]').click()
    sheet(page).locator('[data-action="delete"]').click()
    sheet(page).get_by_role("button", name="Delete 1 subgroup").click()
    expect(sheet(page)).to_be_hidden()
    assert not Subgroup.objects.filter(pk=cubs.pk).exists()

    page.locator(f'[data-group="{empty.pk}"] [data-edit-group]').first.click()
    sheet(page).locator('[data-action="delete"]').click()
    sheet(page).get_by_role("button", name="Delete 1 group").click()
    expect(sheet(page)).to_be_hidden()
    assert not Group.objects.filter(pk=empty.pk).exists()


def test_add_an_aggregate(login, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    RaceSettings.objects.filter(event=event).update(use_subgroups=True)
    page = open_groups(page_of(login))
    page.get_by_role("button", name="Aggregate", exact=True).click()
    form = sheet(page).locator("[data-aggregate-sheet]")
    form.get_by_label("Aggregate name").fill("Cats")
    form.get_by_text("Subgroup", exact=True).click()
    form.locator('[data-constituents="subgroup"]').get_by_label("Lions").check()
    form.locator('[data-constituents="subgroup"]').get_by_label("Tigers").check()
    form.get_by_label("Speed trophies").select_option("2")
    awards = form.locator('[data-partition-list="ag"]')
    awards.get_by_label("Only these").check()
    expect(awards.locator('input[name="partition"]:visible')).to_have_count(2)
    awards.get_by_label("Tigers").uncheck()
    shoot(page, "groups-aggregate-sheet", full_page=False)
    form.get_by_role("button", name="Add aggregate").click()
    expect(sheet(page)).to_be_hidden()
    cats = Group.objects.get(event=event, name="Cats")
    assert groups.is_aggregate(cats.pk) and cats.n_trophies == 2
    listed = AwardPartition.objects.filter(group=cats)
    assert list(listed.values_list("partition__name", flat=True)) == ["Lions"]
    expect(
        page.locator(f'[data-aggregate="{cats.pk}"] [data-constituents]')
    ).to_have_text("Lions, Tigers")


# ----- screenshots, layout, accessibility ------------------------------------------


def test_the_tree_fits_and_is_accessible(login, event):
    dens(event)
    groups.form_groups_by_rule(event, "one-group")
    groups.form_groups_by_rule(event, "custom")
    lions = Group.objects.get(event=event, name="All Racers")
    groups.add_group(event, "Cats", constituents=[lions])
    page = open_groups(page_of(login))
    no_sideways_scroll(page)
    shoot(page, "groups-tree")
    assert_accessible(page)
