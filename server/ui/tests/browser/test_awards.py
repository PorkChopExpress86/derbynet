"""The award pages in a browser (#45): a judge on a phone assigns and removes
an award by tapping, the wrong-subgroup refusal shows before the tap (C31),
the D35 Parents journey across judging and the awards editor, the D38
"All partitions" choice, the presenter's no-kiosk warning, the parity boxes
of awards-editor.php, judging.php and awards-presentation.php, and the
screenshots."""

import re

import pytest
from playwright.sync_api import expect

from awards import editing
from awards.models import Award, AwardPartition
from awards.services import set_partition_list
from core.models import RaceSettings, RaceState
from kiosks.models import Kiosk
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.racers import insert_new_racer, pass_racer
from ui.tests.browser.conftest import (
    assert_accessible,
    dismiss_toasts,
    htmx_post,
    htmx_settled,
    no_sideways_scroll,
    shoot,
)

DESIGN = 1


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check is at 390x844")


def racer(event, name, partition, car, **flags) -> Racer:
    made = insert_new_racer(
        event,
        first_name=name,
        last_name="Racer",
        car_number=car,
        partition_name=partition,
        **flags,
    ).racer
    pass_racer(made, True)
    return made


def pack(event):
    """One group, each partition a subgroup, subgroups on: two Lions, two
    Tigers and a Parent not eligible for speed awards."""
    for name in ("Lions", "Tigers", "Parents"):
        groups.add_partition(event, name)
    groups.form_groups_by_rule(event, "one-group")
    RaceSettings.objects.filter(event=event).update(use_subgroups=True)
    return {
        "lion": racer(event, "Ava", "Lions", 101),
        "lion2": racer(event, "Ben", "Lions", 102),
        "tiger": racer(event, "Cal", "Tigers", 201),
        "tiger2": racer(event, "Dee", "Tigers", 202),
        "parent": racer(event, "Pat", "Parents", 301, no_speed_awards=True),
        "group": Group.objects.get(event=event),
    }


def partition(event, name) -> Partition:
    return Partition.objects.get(event=event, name=name)


def subgroup(event, name) -> Subgroup:
    return Subgroup.objects.get(event=event, name=name)


def new_award(event, name, *, group=None, subgroup=None, partitions=None, type_id=1):
    return editing.save_award(
        event,
        None,
        name=name,
        award_type_id=type_id,
        group_id=group.pk if group else None,
        subgroup_id=subgroup.pk if subgroup else None,
        partition_ids=partitions,
    )


def winner(award) -> int | None:
    return Award.objects.get(pk=award.pk).racer_id


def sheet(page, name):
    return page.locator(f'[data-sheet="{name}"]')


# True once htmx has processed every element under `root` (and `root`) that
# makes a request: htmx 2 sets `initHash` on an element's internal data when
# it binds the element's `hx-*` attributes.
BOUND = """root => [root, ...root.querySelectorAll('[hx-get], [hx-post]')]
  .filter((e) => e.matches('[hx-get], [hx-post]'))
  .every((e) => (e['htmx-internal-data'] || {}).initHash)"""


def bound(page, locator):
    """Wait until htmx has bound the requests of what `locator` shows. Alpine
    shows swapped-in content at once, but htmx binds its `hx-*` attributes
    only when the swap settles, 20 ms later or more under load, and a
    control used before then sends nothing (#46 CI: the award sheet's "For"
    select never fetched the partition list). No settling class marks every
    such swap, so `htmx_settled()` alone does not cover it."""
    page.wait_for_function(BOUND, arg=locator.element_handle())


def ready(page, body):
    """`body`, a sheet body just loaded, once its controls are bound."""
    expect(body).to_be_visible()
    bound(page, body)
    return body


def tap(page, control):
    """Click a control of a list or board that htmx may have just swapped
    (a poll, a filter, a reply out of band), once htmx has bound it."""
    htmx_settled(page)
    bound(page, control)
    dismiss_toasts(page)
    control.click()


def open_judging(page, query=""):
    page.goto(f"/judging/{query}")
    expect(page.locator("#judging-board [data-section=racers]")).to_be_visible()
    return page


def racer_card(page, racer):
    return page.locator(f'[data-racer-cards] [data-racer="{racer.pk}"] button')


def award_card(page, award):
    return page.locator(f'[data-award-card="{award.pk}"]')


def open_editor(page):
    page.goto("/awards/edit/")
    expect(page.locator("#awards-list [data-section=awards]")).to_be_visible()
    return page


def edit_award(page, award):
    dismiss_toasts(page)
    page.locator(f'[data-award="{award.pk}"] [data-edit-award]').click()
    return ready(page, sheet(page, "awards").locator("[data-award-sheet]"))


# ----- acceptance: a judge on a phone -----


def test_a_judge_on_a_phone_assigns_and_removes_an_award(login, event):
    """At 390x844, as the race crew: tap a racer, tap an award on the
    racer's sheet, then remove the award from its card, and Undo. No drag."""
    page = login("RaceCrew")
    phone_only(page)
    found = pack(event)
    paint = new_award(event, "Best Paint")
    open_judging(page)

    tap(page, racer_card(page, found["tiger"]))
    racer_sheet = ready(
        page,
        sheet(page, "judging").locator(f'[data-racer-sheet="{found["tiger"].pk}"]'),
    )
    with htmx_post(page, f"/judging/award/{paint.pk}/assign/"):
        racer_sheet.locator(f'[data-choice="{paint.pk}"] [data-assign]').click()
    expect(sheet(page, "judging")).to_be_hidden()
    assert winner(paint) == found["tiger"].pk
    expect(award_card(page, paint).locator("[data-winner]")).to_contain_text(
        "201 Cal Racer"
    )
    expect(
        page.locator(f'[data-racer="{found["tiger"].pk}"] [data-has-award]')
    ).to_be_visible()

    dismiss_toasts(page)
    with htmx_post(page, f"/judging/award/{paint.pk}/remove/"):
        award_card(page, paint).get_by_role(
            "button", name=re.compile("^Remove")
        ).click()
    assert winner(paint) is None
    expect(award_card(page, paint).locator("[data-winner]")).to_contain_text(
        "No winner yet"
    )
    with htmx_post(page, f"/judging/award/{paint.pk}/restore/"):
        page.locator("#toasts").get_by_role("button", name="Undo").click()
    assert winner(paint) == found["tiger"].pk
    expect(award_card(page, paint).locator("[data-winner]")).to_contain_text(
        "201 Cal Racer"
    )


def test_the_wrong_subgroup_is_refused_with_a_visible_reason(login, event):
    """C31: the Tigers racer's sheet shows the Lions award closed, with the
    reason, before any tap; the award's sheet does not offer the racer."""
    page = login("RaceCoordinator")
    found = pack(event)
    lions = new_award(
        event, "Lion Paint", group=found["group"], subgroup=subgroup(event, "Lions")
    )
    open_judging(page)
    tap(page, racer_card(page, found["tiger"]))
    choice = sheet(page, "judging").locator(f'[data-choice="{lions.pk}"]')
    expect(choice.locator("[data-reason]")).to_have_text("For Lions only")
    expect(choice.get_by_role("button")).to_be_disabled()
    sheet(page, "judging").get_by_role("button", name="Close").click()
    expect(sheet(page, "judging")).to_be_hidden()

    tap(page, award_card(page, lions).locator("[data-open-award]"))
    eligible = sheet(page, "judging").locator("[data-eligible]")
    expect(eligible.locator("li")).to_have_count(2)
    expect(eligible).not_to_contain_text("Cal Racer")
    assert winner(lions) is None


# ----- acceptance: D35, Parents -----


def test_parents_win_a_pack_judged_award_until_the_editor_leaves_them_out(login, event):
    """At 390x844 (D35): with the pack's speed-award list leaving out
    Parents, the Parents racer can be given a pack-wide judged award whose
    list holds every partition. Unchecking Parents on that award in the
    awards editor saves a list without it, unchecking the last box is
    refused, and judging then lists no Parents racer for that award."""
    page = login("RaceCoordinator")
    phone_only(page)
    found = pack(event)
    set_partition_list(event, [partition(event, "Lions"), partition(event, "Tigers")])
    paint = new_award(event, "Best Paint")

    open_judging(page)
    expect(
        page.locator(f'[data-racer="{found["parent"].pk}"] [data-speed-ineligible]')
    ).to_be_visible()
    tap(page, award_card(page, paint).locator("[data-open-award]"))
    eligible = ready(page, sheet(page, "judging").locator("[data-eligible]"))
    with htmx_post(page, f"/judging/award/{paint.pk}/assign/"):
        eligible.locator(f'[data-racer="{found["parent"].pk}"] button').click()
    assert winner(paint) == found["parent"].pk
    dismiss_toasts(page)
    with htmx_post(page, f"/judging/award/{paint.pk}/remove/"):
        award_card(page, paint).get_by_role(
            "button", name=re.compile("^Remove")
        ).click()

    open_editor(page)
    form = edit_award(page, paint)
    expect(form.locator('[data-scope="all"]')).to_be_checked()
    form.locator('[data-scope="some"]').check()
    form.get_by_label("Parents").uncheck()
    with htmx_post(page, f"/awards/edit/{paint.pk}/"):
        form.get_by_role("button", name="Save").click()
    expect(sheet(page, "awards")).to_be_hidden()
    assert set(
        AwardPartition.objects.filter(award=paint).values_list(
            "partition__name", flat=True
        )
    ) == {"Lions", "Tigers"}
    expect(page.locator(f'[data-award="{paint.pk}"] [data-partitions]')).to_have_text(
        "Lions, Tigers"
    )

    form = edit_award(page, paint)
    expect(form.locator('[data-scope="some"]')).to_be_checked()
    form.get_by_label("Lions").uncheck()
    form.get_by_label("Tigers").uncheck()
    with htmx_post(page, f"/awards/edit/{paint.pk}/"):
        form.get_by_role("button", name="Save").click()
    expect(sheet(page, "awards").locator("[data-error]")).to_have_text(
        "Check at least one, or pick all of them."
    )
    assert AwardPartition.objects.filter(award=paint).count() == 2

    open_judging(page)
    tap(page, award_card(page, paint).locator("[data-open-award]"))
    eligible = sheet(page, "judging").locator("[data-eligible]")
    expect(eligible.locator("li")).to_have_count(4)
    expect(eligible).not_to_contain_text("Pat Racer")


# ----- D38 -----


def test_all_partitions_follows_the_group_and_a_full_list_is_all_again(login, event):
    """D38: a group award left at "All partitions" offers a partition moved
    into the group afterwards, and a narrowed list that comes to cover every
    partition the award draws from shows as "All partitions"."""
    page = login("RaceCoordinator")
    found = pack(event)
    groups.form_groups_by_rule(event, "custom")
    bears = groups.add_group(event, "Bears")
    groups.move_subgroup(subgroup(event, "Tigers"), bears)
    every = new_award(event, "Bear Spirit", group=bears)
    narrowed = new_award(event, "Bear Paint", group=bears)
    groups.move_subgroup(subgroup(event, "Lions"), bears)
    set_partition_list(
        event, [partition(event, "Tigers"), partition(event, "Bears")], award=narrowed
    )
    assert found

    open_editor(page)
    form = edit_award(page, every)
    expect(form.locator('[data-scope="all"]')).to_be_checked()
    # Bears' own partition, and the two moved in.
    expect(form.locator('input[name="partition"]')).to_have_count(3)
    sheet(page, "awards").get_by_role("button", name="Close").click()

    form = edit_award(page, narrowed)
    expect(form.locator('[data-scope="some"]')).to_be_checked()
    sheet(page, "awards").get_by_role("button", name="Close").click()

    # Lions leave Bears, so the list holds every partition Bears draws
    # from; Lions come back, and the list is "All partitions".
    groups.move_subgroup(subgroup(event, "Lions"), found["group"])
    groups.move_subgroup(subgroup(event, "Lions"), bears)
    assert not AwardPartition.objects.filter(award=narrowed).exists()
    open_editor(page)
    form = edit_award(page, narrowed)
    expect(form.locator('[data-scope="all"]')).to_be_checked()
    expect(form.get_by_label("Lions")).to_be_checked()


# ----- acceptance: the no-kiosk warning -----


def test_the_no_kiosk_warning_shows_and_hides(login, event):
    page = login("RaceCoordinator")
    page.goto("/awards/present/")
    warning = page.locator("[data-no-kiosk]")
    expect(warning).to_be_visible()
    expect(warning.get_by_role("link")).to_have_attribute("href", "/kiosks/")
    Kiosk.objects.create(
        event=event, address="tv", page="kiosks/award-presentations.kiosk"
    )
    page.reload()
    expect(page.locator("[data-section=console]")).to_be_visible()
    expect(warning).to_have_count(0)


def test_a_long_award_name_keeps_the_phone_list_one_screen_wide(login, event):
    """A card is no wider than the list, whatever the award's name, so the
    editor never scrolls sideways on a phone. With no phone columns the list's
    one column took the width of the widest name, which `truncate` keeps on
    one line, as the check-in list's did (#58 review finding H3, #58 retro;
    ui/tests/test_phone_grids.py)."""
    award = new_award(event, "Darrell & Darrell Delaughter Memorial Award")
    new_award(event, "Short")
    page = open_editor(login("RaceCoordinator"))
    phone_only(page)
    card = page.locator(f'[data-award-list] [data-award="{award.pk}"]')
    expect(card).to_contain_text("Darrell & Darrell Delaughter Memorial Award")
    no_sideways_scroll(page)
    width = page.evaluate(
        "() => [document.querySelector('[data-award-list]').clientWidth,"
        " document.querySelector('[data-award]').getBoundingClientRect().width]"
    )
    # one read: the list is drawn and nothing redraws it before the read
    assert width[1] <= width[0], width


# ----- parity: awards-editor.php -----


def test_editor_parity_list_reorder_new_edit_delete(login, event):
    """Award list, reorder by the row menu, new, edit, delete; the type
    select leaves out Ad Hoc; the group or subgroup select."""
    page = login("RaceCoordinator")
    found = pack(event)
    first = new_award(event, "Best Paint")
    second = new_award(event, "Lion Spirit", group=found["group"], type_id=4)
    open_editor(page)
    cards = page.locator("[data-award-list] > li")
    expect(cards).to_have_count(2)

    page.locator(f'[data-award="{second.pk}"] [data-row-menu]').click()
    with htmx_post(page, "/awards/edit/order/"):
        page.locator(f'[data-award="{second.pk}"]').get_by_role(
            "menuitem", name="Move up"
        ).click()
    assert list(Award.objects.order_by("sort").values_list("pk", flat=True)) == [
        second.pk,
        first.pk,
    ]

    page.locator('[data-action="new-award"]').click()
    form = ready(page, sheet(page, "awards").locator('[data-award-sheet="new"]'))
    types = form.get_by_label("Type").locator("option")
    expect(types).to_have_text(
        ["Design General", "Design Trophy", "Other", "Speed General", "Speed Trophy"]
    )
    form.get_by_label("Award name").fill("Tiger Stripes")
    form.get_by_label("Type").select_option(label="Other")
    with page.expect_response(lambda r: "/awards/edit/partitions/" in r.url):
        form.get_by_label("For").select_option(
            f"{found['group'].pk},{subgroup(event, 'Tigers').pk}"
        )
    with htmx_post(page, "/awards/edit/new/"):
        form.get_by_role("button", name="Add award").click()
    stripes = Award.objects.get(name="Tiger Stripes")
    assert (stripes.award_type_id, stripes.subgroup.name) == (3, "Tigers")
    shoot(page, "awards-editor")

    form = edit_award(page, stripes)
    shoot(page, "awards-editor-sheet", full_page=False)
    form.get_by_label("Award name").fill("Tiger Style")
    with htmx_post(page, f"/awards/edit/{stripes.pk}/"):
        form.get_by_role("button", name="Save").click()
    expect(page.locator(f'[data-award="{stripes.pk}"] [data-edit-award]')).to_have_text(
        "Tiger Style"
    )

    edit_award(page, stripes)
    dismiss_toasts(page)
    sheet(page, "awards").locator('[data-action="delete"]').click()
    confirm = ready(page, sheet(page, "awards").locator("[data-delete-confirm]"))
    with htmx_post(page, f"/awards/edit/{stripes.pk}/delete/"):
        confirm.get_by_role("button", name="Delete 1 award").click()
    assert not Award.objects.filter(pk=stripes.pk).exists()
    expect(cards).to_have_count(2)
    assert_accessible(page)


# ----- parity: judging.php -----


def test_judging_parity_grid_sort_assign_both_ways_remove_ad_hoc(login, event):
    page = login("RaceCoordinator")
    found = pack(event)
    paint = new_award(event, "Best Paint")
    spirit = new_award(
        event, "Lion Spirit", group=found["group"], subgroup=subgroup(event, "Lions")
    )
    open_judging(page)
    card = racer_card(page, found["lion"])
    expect(card.locator("[data-car]")).to_have_text("101")
    expect(card.locator("[data-name]")).to_have_text("Ava Racer")
    expect(card.locator("[data-subgroup]")).to_have_text("Lions")

    cars = page.locator("[data-racer-cards] [data-car]")
    expect(cars).to_have_text(["101", "102", "201", "202", "301"])
    page.locator("#judging-filters").get_by_text("Name", exact=True).click()
    expect(cars.first).to_have_text("101")
    Racer.objects.filter(pk=found["parent"].pk).update(last_name="Aaron")
    page.locator("#judging-filters").get_by_text("Group", exact=True).click()
    expect(cars.first).to_have_text("301")
    page.locator("#judging-filters").get_by_text("Car", exact=True).click()
    expect(cars.first).to_have_text("101")
    page.get_by_label("Find a racer by name or car number").fill("20")
    expect(cars).to_have_text(["201", "202"])
    find = page.get_by_label("Find a racer by name or car number")
    find.fill("")
    # Leaving the field fires its `change`, which fetches the board again;
    # leave it here, so that swap is not the one under the next tap.
    find.blur()
    expect(cars).to_have_count(5)

    # Award to racer.
    tap(page, award_card(page, spirit).locator("[data-open-award]"))
    award_sheet = ready(
        page, sheet(page, "judging").locator(f'[data-award-sheet="{spirit.pk}"]')
    )
    expect(award_sheet.locator("[data-eligible] li")).to_have_count(2)
    shoot(page, "judging-award-sheet", full_page=False)
    with htmx_post(page, f"/judging/award/{spirit.pk}/assign/"):
        award_sheet.locator(f'[data-racer="{found["lion2"].pk}"] button').click()
    assert winner(spirit) == found["lion2"].pk

    # Racer to award, then the ad hoc field.
    tap(page, racer_card(page, found["lion"]))
    racer_sheet = ready(
        page,
        sheet(page, "judging").locator(f'[data-racer-sheet="{found["lion"].pk}"]'),
    )
    expect(
        racer_sheet.locator(f'[data-choice="{spirit.pk}"] [data-reason]')
    ).to_have_text("Given to 102 Ben Racer")
    with htmx_post(page, f"/judging/award/{paint.pk}/assign/"):
        racer_sheet.locator(f'[data-choice="{paint.pk}"] [data-assign]').click()
    assert winner(paint) == found["lion"].pk

    tap(page, racer_card(page, found["tiger"]))
    racer_sheet = ready(
        page,
        sheet(page, "judging").locator(f'[data-racer-sheet="{found["tiger"].pk}"]'),
    )
    racer_sheet.get_by_label("Ad hoc award").fill("Most Creative")
    with htmx_post(page, f"/judging/racer/{found['tiger'].pk}/adhoc/"):
        racer_sheet.get_by_role("button", name="Save ad hoc award").click()
    expect(
        page.locator(f'[data-racer="{found["tiger"].pk}"] [data-has-adhoc]')
    ).to_be_visible()
    assert Award.objects.get(award_type_id=6).name == "Most Creative"

    # Remove from the racer's sheet.
    dismiss_toasts(page)
    tap(page, racer_card(page, found["lion"]))
    racer_sheet = ready(
        page,
        sheet(page, "judging").locator(f'[data-racer-sheet="{found["lion"].pk}"]'),
    )
    with htmx_post(page, f"/judging/award/{paint.pk}/remove/"):
        racer_sheet.locator(f'[data-choice="{paint.pk}"] [data-remove]').click()
    assert winner(paint) is None
    shoot(page, "judging")
    assert_accessible(page)


# ----- parity: awards-presentation.php -----


def test_presenter_parity_filters_calc_present_reveal_next_clear(login, event):
    page = login("RaceCoordinator")
    found = pack(event)
    paint = new_award(event, "Best Paint")
    spirit = new_award(
        event,
        "Lion Spirit",
        group=found["group"],
        subgroup=subgroup(event, "Lions"),
        type_id=4,
    )
    other = new_award(
        event,
        "Tiger Style",
        group=found["group"],
        subgroup=subgroup(event, "Tigers"),
        type_id=3,
    )
    editing.assign_award(event, paint.pk, found["lion"].pk)
    editing.assign_award(event, spirit.pk, found["lion2"].pk)
    editing.set_ad_hoc_award(event, found["tiger"], "Most Creative")
    page.goto("/awards/present/")
    rows = page.locator("[data-presentation-list] > li")
    expect(rows).to_have_count(4)
    expect(page.locator("[data-no-kiosk]")).to_be_visible()

    chips = page.locator("#presenter-filters")
    chips.locator("[data-type-chips]").get_by_text("Design Trophy", exact=True).click()
    expect(rows.filter(has_text="Lion Spirit")).to_be_visible()
    expect(rows.filter(has_text="Best Paint")).to_be_hidden()
    chips.locator("[data-type-chips]").get_by_text("All", exact=True).click()
    chips.locator("[data-scope-chips]").get_by_text("Tigers", exact=True).click()
    expect(rows.filter(has_text="Tiger Style")).to_be_visible()
    expect(rows.filter(has_text="Most Creative")).to_be_visible()
    expect(rows.filter(has_text="Lion Spirit")).to_be_hidden()
    chips.locator("[data-scope-chips]").get_by_text("Pack", exact=True).click()
    expect(rows.filter(has_text="Best Paint")).to_be_visible()
    expect(rows.filter(has_text="Tiger Style")).to_be_hidden()
    chips.locator("[data-scope-chips]").get_by_text("All", exact=True).click()

    console = page.locator("[data-section=console]")
    with htmx_post(page, "/awards/present/award/"):
        rows.filter(has_text="Best Paint").get_by_role("button").click()
    state = RaceState.objects.get(event=event)
    assert (state.current_award_key, state.reveal_current_award) == (
        f"award-{paint.pk}",
        False,
    )
    expect(console.locator("[data-awardname]")).to_have_text("Best Paint")
    with htmx_post(page, "/awards/present/reveal/"):
        console.get_by_role("button", name="Reveal").click()
    assert RaceState.objects.get(event=event).reveal_current_award is True
    expect(console.locator("[data-reveal-state]")).to_have_text("Showing the winner")
    with htmx_post(page, "/awards/present/next/"):
        console.get_by_role("button", name="Next award").click()
    state = RaceState.objects.get(event=event)
    assert (state.current_award_key, state.reveal_current_award) == (
        f"award-{spirit.pk}",
        False,
    )
    expect(console.locator("[data-awardname]")).to_have_text("Lion Spirit")
    shoot(page, "awards-presentation")
    with htmx_post(page, "/awards/present/clear/"):
        console.get_by_role("button", name="Clear the kiosk").click()
    assert RaceState.objects.get(event=event).current_award_key == ""
    expect(console.locator("[data-nothing]")).to_be_visible()
    assert other

    # The 1 s poll shows a change made elsewhere, as the kiosk dashboard
    # or a second presenter would make it.
    RaceState.objects.filter(event=event).update(current_award_key=f"award-{spirit.pk}")
    expect(console.locator("[data-awardname]")).to_have_text("Lion Spirit")
    assert_accessible(page)


def test_presenter_pack_standings_choice(login, event):
    page = login("RaceCoordinator")
    found = pack(event)
    cubs = groups.add_group(event, "Cubs", [found["group"]])
    page.goto("/awards/present/")
    calc = page.locator("[data-section=calc]")
    expect(calc.get_by_label("Calculate normally")).to_be_checked()
    with page.expect_navigation():
        calc.get_by_label("Use Cubs group").check()
    expect(
        page.locator("[data-section=calc]").get_by_label("Use Cubs group")
    ).to_be_checked()
    settings = RaceSettings.objects.get(event=event)
    assert settings.full_field_group_id == cubs.pk
