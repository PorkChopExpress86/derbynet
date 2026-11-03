"""The award services of the judging page and the awards editor (#45):
tap to assign and remove, with PHP's group, subgroup and award-setting
checks (C31), the partition lists (D33, D35, D38), the editor's sheet in one
transaction, and the presentation console's Next, Reveal and Clear."""

import pytest

from awards import editing, presentation
from awards.editing import AwardFailure
from awards.listing import AD_HOC
from awards.models import Award, AwardPartition
from awards.services import award_partition_choice, partition_list
from core.models import ActionLog, RaceState
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.racers import insert_new_racer, pass_racer

pytestmark = pytest.mark.django_db

DESIGN = 1


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
    return Racer.objects.get(pk=made.pk)


@pytest.fixture
def pack(event):
    """One group, each partition a subgroup: Lions, Tigers and Parents."""
    for name in ("Lions", "Tigers", "Parents"):
        groups.add_partition(event, name)
    groups.form_groups_by_rule(event, "one-group")
    return {
        "lion": racer(event, "Lion", "Lions", 101),
        "tiger": racer(event, "Tiger", "Tigers", 201),
        "parent": racer(event, "Parent", "Parents", 301, no_speed_awards=True),
        "group": Group.objects.get(event=event),
    }


def subgroup(event, name) -> Subgroup:
    return Subgroup.objects.get(event=event, name=name)


def partition(event, name) -> Partition:
    return Partition.objects.get(event=event, name=name)


def award(event, name="Best Paint", **scope) -> Award:
    return Award.objects.create(event=event, name=name, award_type_id=DESIGN, **scope)


def winner(found: Award):
    return Award.objects.get(pk=found.pk).racer_id


# ----- tap to assign -----


def test_a_racer_from_the_wrong_subgroup_is_refused_with_the_reason(event, pack):
    lions = award(event, group=pack["group"], subgroup=subgroup(event, "Lions"))
    with pytest.raises(AwardFailure) as refused:
        editing.assign_award(event, lions.pk, pack["tiger"].pk)
    assert refused.value.code == "wrong-subgroup"
    assert refused.value.description == "For Lions only"
    assert winner(lions) is None


def test_the_legacy_award_winner_refuses_the_same_racer(event, pack):
    lions = award(event, group=pack["group"], subgroup=subgroup(event, "Lions"))
    with pytest.raises(AwardFailure) as refused:
        editing.set_winner(event, lions, pack["tiger"])
    assert refused.value.code == "wrong-subgroup"


def test_a_racer_in_the_right_subgroup_wins_and_a_repeat_changes_nothing(event, pack):
    lions = award(event, group=pack["group"], subgroup=subgroup(event, "Lions"))
    editing.assign_award(event, lions.pk, pack["lion"].pk)
    editing.assign_award(event, lions.pk, pack["lion"].pk)
    assert winner(lions) == pack["lion"].pk


def test_an_award_with_another_winner_is_refused(event, pack):
    paint = award(event)
    editing.assign_award(event, paint.pk, pack["lion"].pk)
    with pytest.raises(AwardFailure) as refused:
        editing.assign_award(event, paint.pk, pack["tiger"].pk)
    assert refused.value.code == "filled"
    assert winner(paint) == pack["lion"].pk


def test_a_racer_not_eligible_for_any_award_is_refused(event, pack):
    lion = pack["lion"]
    Racer.objects.filter(pk=lion.pk).update(exclude=True)
    with pytest.raises(AwardFailure) as refused:
        editing.assign_award(event, award(event).pk, lion.pk)
    assert refused.value.code == "ineligible"
    assert refused.value.description == "Not eligible for any award"


def test_a_racer_not_eligible_for_speed_awards_can_win_a_judged_award(event, pack):
    """D33: the Parents racer has no speed awards, and wins a judged one."""
    paint = award(event)
    editing.assign_award(event, paint.pk, pack["parent"].pk)
    assert winner(paint) == pack["parent"].pk


def test_a_partition_the_award_leaves_out_is_refused(event, pack):
    """D35: "best paint job" with Parents unchecked never goes to a Parents
    racer; the legacy `award.winner` answers `ineligible`."""
    paint = editing.save_award(
        event,
        None,
        name="Best Paint",
        award_type_id=DESIGN,
        group_id=None,
        subgroup_id=None,
        partition_ids=[partition(event, "Lions").pk, partition(event, "Tigers").pk],
    )
    with pytest.raises(AwardFailure) as refused:
        editing.assign_award(event, paint.pk, pack["parent"].pk)
    assert (refused.value.code, refused.value.description) == (
        "not-in-list",
        "Not open to Parents",
    )
    with pytest.raises(AwardFailure) as legacy:
        editing.set_winner(event, paint, pack["parent"])
    assert legacy.value.code == "ineligible"


def test_an_ad_hoc_award_is_not_assigned_on_its_own(event, pack):
    editing.set_ad_hoc_award(event, pack["lion"], "Most Creative")
    ad_hoc = Award.objects.get(award_type_id=AD_HOC)
    with pytest.raises(AwardFailure):
        editing.assign_award(event, ad_hoc.pk, pack["tiger"].pk)
    with pytest.raises(AwardFailure):
        editing.remove_winner(event, ad_hoc.pk)


def test_an_award_of_another_event_is_gone(event, pack):
    from core.tests.factories import EventFactory

    other = award(EventFactory())
    with pytest.raises(AwardFailure) as refused:
        editing.assign_award(event, other.pk, pack["lion"].pk)
    assert refused.value.code == "no-such-award"


# ----- remove, Undo, ad hoc -----


def test_remove_returns_the_racer_for_undo_and_a_repeat_changes_nothing(event, pack):
    paint = award(event)
    editing.assign_award(event, paint.pk, pack["lion"].pk)
    _, removed = editing.remove_winner(event, paint.pk)
    assert removed.pk == pack["lion"].pk
    _, again = editing.remove_winner(event, paint.pk)
    assert again is None
    # Undo is a new assignment: checked again, and repeated changes nothing.
    editing.assign_award(event, paint.pk, removed.pk)
    editing.assign_award(event, paint.pk, removed.pk)
    assert winner(paint) == pack["lion"].pk


def test_undo_after_another_judge_assigned_the_award_is_refused(event, pack):
    paint = award(event)
    editing.assign_award(event, paint.pk, pack["lion"].pk)
    _, removed = editing.remove_winner(event, paint.pk)
    editing.assign_award(event, paint.pk, pack["tiger"].pk)
    with pytest.raises(AwardFailure):
        editing.assign_award(event, paint.pk, removed.pk)
    assert winner(paint) == pack["tiger"].pk


def test_the_ad_hoc_field_gives_replaces_and_takes_away(event, pack):
    lion = pack["lion"]
    editing.set_ad_hoc_award_by_id(event, lion.pk, " Most Creative ")
    editing.set_ad_hoc_award_by_id(event, lion.pk, "Shiniest")
    assert list(
        Award.objects.filter(award_type_id=AD_HOC).values_list("name", "racer")
    ) == [("Shiniest", lion.pk)]
    editing.set_ad_hoc_award_by_id(event, lion.pk, "")
    assert not Award.objects.filter(award_type_id=AD_HOC).exists()
    with pytest.raises(AwardFailure):
        editing.set_ad_hoc_award_by_id(event, lion.pk, "x" * 101)


# ----- the editor's sheet -----


def save(event, award_id=None, **fields):
    values = {
        "name": "Best Paint",
        "award_type_id": DESIGN,
        "group_id": None,
        "subgroup_id": None,
        "partition_ids": None,
    }
    return editing.save_award(event, award_id, **{**values, **fields})


def test_a_new_award_with_all_partitions_stores_no_list(event, pack):
    paint = save(event)
    assert not AwardPartition.objects.filter(award=paint).exists()
    groups.add_partition(event, "Wolves")
    assert "Wolves" in [p.name for p in partition_list(event, award=paint)]


def test_a_narrowed_list_saves_and_keeps_out_partitions_added_later(event, pack):
    paint = save(event, partition_ids=[partition(event, "Lions").pk])
    groups.add_partition(event, "Wolves")
    assert [p.name for p in partition_list(event, award=paint)] == ["Lions"]


def test_every_box_checked_is_all_partitions(event, pack):
    every = list(Partition.objects.filter(event=event).values_list("pk", flat=True))
    paint = save(event, partition_ids=every)
    assert not AwardPartition.objects.filter(award=paint).exists()


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"partition_ids": []}, "nopartitions"),
        ({"name": "  "}, "emptyaward"),
        ({"name": "x" * 101}, "toolong"),
        ({"award_type_id": AD_HOC}, "adhoc"),
        ({"award_type_id": 99}, "unknownawardtype"),
        ({"group_id": 999999}, "unknownclass"),
        ({"partition_ids": [999999]}, "nopartitions"),
    ],
)
def test_the_sheet_refuses_and_stores_nothing(event, pack, fields, code):
    with pytest.raises(AwardFailure) as refused:
        save(event, **fields)
    assert refused.value.code == code
    assert not Award.objects.filter(event=event).exists()


def test_a_subgroup_of_another_group_is_refused(event, pack):
    other = groups.add_group(event, "Others")
    with pytest.raises(AwardFailure) as refused:
        save(event, group_id=other.pk, subgroup_id=subgroup(event, "Lions").pk)
    assert refused.value.code == "wrongclass"


def test_a_partition_the_new_scope_does_not_draw_from_rolls_back_the_edit(event, pack):
    paint = save(event)
    lions = subgroup(event, "Lions")
    with pytest.raises(AwardFailure):
        save(
            event,
            paint.pk,
            name="Lion Paint",
            group_id=pack["group"].pk,
            subgroup_id=lions.pk,
            partition_ids=[partition(event, "Tigers").pk],
        )
    paint.refresh_from_db()
    assert (paint.name, paint.subgroup_id) == ("Best Paint", None)


def test_an_edit_changes_scope_and_list_together(event, pack):
    paint = save(event, partition_ids=[partition(event, "Lions").pk])
    save(
        event,
        paint.pk,
        name="Lion Paint",
        group_id=pack["group"].pk,
        subgroup_id=subgroup(event, "Lions").pk,
        partition_ids=None,
    )
    paint.refresh_from_db()
    assert (paint.name, paint.subgroup.name) == ("Lion Paint", "Lions")
    assert not AwardPartition.objects.filter(award=paint).exists()


def test_the_sheet_offers_the_partitions_a_group_award_draws_from(event):
    """D38: a group award offers its group's partitions, and "all" picks up
    a partition moved into the group later."""
    for name in ("Lions", "Tigers"):
        groups.add_partition(event, name)
    lions_group = partition(event, "Lions").subgroup.group
    paint = save(event, group_id=lions_group.pk)
    choice = award_partition_choice(event, lions_group.pk, None, paint)
    assert ([p.name for p in choice.offered], choice.narrowed) == (["Lions"], None)


def test_reorder_needs_every_award_once(event, pack):
    a, b = save(event, name="A"), save(event, name="B")
    editing.reorder_awards(event, [b.pk, a.pk])
    assert list(Award.objects.order_by("sort").values_list("name", flat=True)) == [
        "B",
        "A",
    ]
    for wrong in ([a.pk], [a.pk, a.pk], [a.pk, b.pk, 999999]):
        with pytest.raises(AwardFailure):
            editing.reorder_awards(event, wrong)


def test_delete_logs_for_history_and_a_second_delete_is_refused(event, pack):
    paint = save(event)
    assert editing.delete_award_by_id(event, paint.pk) == "Best Paint"
    assert ActionLog.objects.filter(request__action="award.*delete").count() == 1
    with pytest.raises(AwardFailure):
        editing.delete_award_by_id(event, paint.pk)


# ----- the presentation console -----


def current(event):
    state = RaceState.objects.get(event=event)
    return state.current_award_key, state.reveal_current_award


def test_next_walks_the_filtered_list_and_stops_at_its_end(event, pack):
    lions = save(
        event,
        name="Lion Paint",
        group_id=pack["group"].pk,
        subgroup_id=subgroup(event, "Lions").pk,
    )
    paint = save(event, name="Best Paint")
    found = presentation.present_next(event, None, presentation.ALL)
    keys = [a.key for a in presentation.presentation(event).awards]
    assert keys == [f"award-{lions.pk}", f"award-{paint.pk}"]
    assert found.key == keys[0] and current(event) == (keys[0], False)
    presentation.reveal(event, True)
    assert current(event) == (keys[0], True)
    presentation.present_next(event, None, presentation.ALL)
    assert current(event) == (keys[1], False)
    with pytest.raises(AwardFailure):
        presentation.present_next(event, None, presentation.ALL)
    presentation.clear(event)
    assert presentation.present_next(event, None, presentation.PACK).key == keys[1]
    assert presentation.present_next(event, DESIGN, f"g{pack['group'].pk}") is not None
    assert current(event) == (keys[0], False)


def test_a_key_no_longer_listed_is_refused(event, pack):
    paint = save(event)
    presentation.present_award(event, f"award-{paint.pk}")
    editing.delete_award_by_id(event, paint.pk)
    with pytest.raises(AwardFailure):
        presentation.present_award(event, f"award-{paint.pk}")
    presentation.clear(event)
    assert current(event) == ("", False)


@pytest.fixture
def event():
    from core.tests.factories import EventFactory

    return EventFactory()
