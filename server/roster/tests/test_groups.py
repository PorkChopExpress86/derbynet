import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from awards.models import Award, BallotAward
from awards.tests.factories import AwardFactory, BallotAwardFactory
from core.models import RaceSettings
from racing.models import HeatLane, PlaylistEntry, RosterEntry, Round
from racing.tests.factories import HeatLaneFactory, PlaylistEntryFactory
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.groups import Failure, GroupFailure, GroupRef
from roster.tests.php_replay import Replay, read_state

pytestmark = pytest.mark.django_db


@pytest.fixture
def replay():
    return Replay()


def with_dens(replay, dens=("Lions", "Tigers"), per_den=2):
    for den in dens:
        for i in range(per_den):
            replay.import_racer(f"{den}{i}", "Racer", den)
    replay.ids.sync()
    return replay


def failure_of(call, *args, **kwargs):
    with pytest.raises(GroupFailure) as caught:
        call(*args, **kwargs)
    return caught.value.code


def group(replay, name):
    return Group.objects.get(event=replay.event, name=name)


def partition(replay, name):
    return Partition.objects.get(event=replay.event, name=name)


# ----- Failure codes ------------------------------------------------------


def test_apply_rule_refuses_an_unknown_rule(replay):
    assert failure_of(groups.form_groups_by_rule, replay.event, "by-den") == (
        Failure.BAD_RULE
    )


def test_apply_rule_refuses_once_a_result_exists(replay):
    with_dens(replay)
    round = Round.objects.get(group=group(replay, "Lions"))
    HeatLaneFactory(round=round, racer=round.roster.first().racer, finish_place=1)
    assert failure_of(groups.form_groups_by_rule, replay.event, "one-group") == (
        Failure.RESULTS_EXIST
    )


@pytest.mark.parametrize("name, code", [("  ", "no-name"), ("Lions", "already-exists")])
def test_partition_add_failures(replay, name, code):
    with_dens(replay)
    assert failure_of(groups.add_partition, replay.event, name) == code


@pytest.mark.parametrize("name, code", [("", "empty-name"), ("Tigers", "name-taken")])
def test_partition_edit_failures(replay, name, code):
    with_dens(replay)
    lions = partition(replay, "Lions")
    assert failure_of(groups.rename_partition, lions, name) == code


def test_partition_edit_refuses_a_group_name_differing_only_in_case(replay):
    """By partition, the group takes the name too, and group names ignore
    case."""
    with_dens(replay)
    assert failure_of(
        groups.rename_partition, partition(replay, "Lions"), "TIGERS"
    ) == (Failure.NAME_TAKEN)


def test_partition_delete_refuses_a_partition_with_racers(replay):
    with_dens(replay)
    assert failure_of(groups.delete_partition, partition(replay, "Lions")) == (
        Failure.IN_USE
    )


def test_class_add_needs_a_name(replay):
    assert failure_of(groups.add_group, replay.event, "   ") == Failure.NONAME


def test_class_add_refuses_a_name_another_group_holds_in_any_case(replay):
    with_dens(replay)
    assert failure_of(groups.add_group, replay.event, "lions") == (
        Failure.ALREADY_EXISTS
    )


@pytest.mark.parametrize(
    "name, group_id, code", [("", None, "noname"), ("Cubs", -5, "noclass")]
)
def test_rank_add_failures(replay, name, group_id, code):
    with_dens(replay)
    group_id = group_id or group(replay, "Lions").pk
    assert failure_of(groups.add_subgroup, replay.event, group_id, name) == code


def test_rank_add_cannot_reach_another_events_group(replay):
    other = with_dens(Replay())
    assert failure_of(
        groups.add_subgroup, replay.event, group(other, "Lions").pk, "Cubs"
    ) == (Failure.NOCLASS)


def test_rank_delete_refuses_the_last_subgroup_of_a_group(replay):
    with_dens(replay)
    lions = Subgroup.objects.get(group=group(replay, "Lions"))
    assert failure_of(groups.delete_subgroup, lions) == Failure.LAST_RANK


def test_rank_delete_refuses_a_subgroup_with_racers(replay):
    with_dens(replay)
    groups.add_subgroup(replay.event, group(replay, "Lions").pk, "Cubs")
    lions = Subgroup.objects.get(group=group(replay, "Lions"), name="Lions")
    assert failure_of(groups.delete_subgroup, lions) == Failure.EXISTING_RACERS


def test_class_delete_refuses_a_group_with_racers(replay):
    with_dens(replay)
    assert failure_of(groups.delete_group, group(replay, "Lions")) == (
        Failure.EXISTING_RACERS
    )


def test_class_delete_refuses_a_constituent(replay):
    with_dens(replay)
    groups.add_group(replay.event, "Empty")
    groups.create_aggregate_group(replay.event.id, "Finals", [group(replay, "Empty")])
    assert failure_of(groups.delete_group, group(replay, "Empty")) == (
        Failure.CONSTITUENT
    )


# ----- Partition moves ----------------------------------------------------


def test_a_from_filter_naming_another_field_fails_before_any_query():
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValueError):
        GroupRef("classid = 1 OR 1", 1)
    assert len(queries) == 0


def test_a_group_ref_needs_an_integer_id():
    with pytest.raises(ValueError):
        GroupRef("rankid", "1 OR 1=1")


def test_move_partition_to_a_new_group_moves_its_racers_and_rosters(replay):
    with_dens(replay)
    groups.form_groups_by_rule(replay.event, "custom")
    groups.move_partition(partition(replay, "Tigers"), GroupRef("classid", -1))
    state = read_state(replay.event)
    assert [c["name"] for c in state["classes"]] == ["Lions", "Tigers"]
    tigers = group(replay, "Tigers")
    assert set(
        Racer.objects.filter(partition__name="Tigers").values_list("group", flat=True)
    ) == {tigers.pk}


def test_move_partition_with_a_filter_moves_only_matching_racers(replay):
    with_dens(replay)
    groups.form_groups_by_rule(replay.event, "custom")
    lions, tigers = group(replay, "Lions"), group(replay, "Tigers")
    stray = Racer.objects.filter(partition__name="Lions").first()
    Racer.objects.filter(pk=stray.pk).update(
        group=tigers, subgroup=Subgroup.objects.get(group=tigers)
    )
    groups.move_partition(
        partition(replay, "Lions"),
        GroupRef("classid", tigers.pk),
        from_filter=GroupRef("classid", lions.pk),
    )
    lions_racers = Racer.objects.filter(partition__name="Lions")
    assert set(lions_racers.values_list("group", flat=True)) == {tigers.pk}
    stray.refresh_from_db()
    assert stray.subgroup.name == "Tigers"


# ----- Names --------------------------------------------------------------


def test_find_or_create_group_finds_a_name_in_another_case(replay):
    cubs = groups.find_or_create_group(replay.event.id, "Cubs")
    assert groups.find_or_create_group(replay.event.id, "  cubs ") == cubs
    assert Group.objects.filter(event=replay.event).count() == 1


def test_group_names_are_cut_to_75_characters(replay):
    long = groups.find_or_create_group(replay.event.id, "x" * 80)
    assert long.name == "x" * 75
    assert groups.find_or_create_group(replay.event.id, "x" * 90) == long


def test_choose_unused_group_name_counts_up(replay):
    groups.find_or_create_group(replay.event.id, "Den")
    groups.find_or_create_group(replay.event.id, "den-1")
    assert groups.choose_unused_group_name(replay.event.id, "Den") == "Den-2"
    assert groups.choose_unused_group_name(replay.event.id, "Pack") == "Pack"


def test_a_subgroup_whose_partition_name_is_taken_gets_group_dash_subgroup(replay):
    """`find_or_create_rank` with a partition: "Webelos" exists for another
    subgroup, so the new subgroup's partition is "Arrows-Webelos", and the
    next one "Arrows-Webelos-1"."""
    with_dens(replay, dens=("Webelos", "Arrows"))
    arrows = group(replay, "Arrows")
    groups.find_or_create_subgroup(arrows, "Webelos")
    assert partition(replay, "Arrows-Webelos").subgroup.group == arrows

    other = groups.add_group(replay.event, "Other")
    Partition.objects.filter(name="Arrows-Webelos").update(name="Other-Webelos")
    groups.find_or_create_subgroup(other, "Webelos")
    assert Partition.objects.filter(event=replay.event, name="Other-Webelos-1").exists()


def test_find_or_create_subgroup_finds_a_name_in_another_case(replay):
    with_dens(replay)
    lions = group(replay, "Lions")
    found = groups.find_or_create_subgroup(lions, "LIONS")
    assert found == Subgroup.objects.get(group=lions)


# ----- Formation rules ----------------------------------------------------


def test_a_failure_in_the_last_statement_of_form_groups_by_rule_changes_nothing(
    replay, monkeypatch
):
    with_dens(replay)
    before = read_state(replay.event)
    real_save = RaceSettings.save

    def fail_on_use_subgroups(self, *args, **kwargs):
        if kwargs.get("update_fields") == ["use_subgroups"]:
            raise RuntimeError("the last write fails")
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(RaceSettings, "save", fail_on_use_subgroups)
    with pytest.raises(RuntimeError):
        groups.form_groups_by_rule(replay.event, "one-group")
    assert read_state(replay.event) == before
    assert RaceSettings.objects.get(event=replay.event).group_formation_rule == (
        "by-partition"
    )


def test_one_group_drops_charts_and_playlist_and_keeps_racers(replay):
    with_dens(replay)
    lions = Round.objects.get(group=group(replay, "Lions"))
    HeatLaneFactory(round=lions, racer=lions.roster.first().racer)
    groups.form_groups_by_rule(replay.event, "one-group")
    state = read_state(replay.event)
    assert [c["name"] for c in state["classes"]] == ["All Racers"]
    assert [s["name"] for s in state["classes"][0]["subgroups"]] == ["Lions", "Tigers"]
    assert not HeatLane.objects.filter(event=replay.event).exists()
    assert state["rounds"][0]["roster_size"] == 4


def test_clean_up_keeps_the_first_subgroup_of_a_constituent(replay):
    """A subgroup with no partition goes, except the first one of a group an
    aggregate draws from."""
    with_dens(replay)
    lions = group(replay, "Lions")
    groups.create_aggregate_group(replay.event.id, "Finals", [lions])
    tigers = group(replay, "Tigers")
    for g in (lions, tigers):
        groups.add_subgroup(replay.event, g.pk, "Cubs")
        RosterEntry.objects.filter(racer__group=g).delete()
        Racer.objects.filter(group=g).delete()
        Partition.objects.filter(subgroup__group=g).delete()
    groups.clean_up_empty_groups_and_subgroups(replay.event.id)
    assert list(
        Subgroup.objects.filter(group=lions).values_list("name", flat=True)
    ) == ["Lions"]
    assert not Group.objects.filter(pk=tigers.pk).exists()


# ----- Sort orders --------------------------------------------------------


def test_order_groups_makes_shared_sort_orders_unique(replay):
    """PHP's cleanup for this case fails (`class.order` binds a whole row)."""
    with_dens(replay, dens=("A", "B", "C"))
    a, b, c = (group(replay, n) for n in "ABC")
    groups.order_groups(replay.event, [c, a])
    orders = dict(
        Group.objects.filter(event=replay.event).values_list("name", "sort_order")
    )
    assert len(set(orders.values())) == 3
    assert sorted(orders, key=orders.get) == ["C", "A", "B"]


def test_order_partitions_makes_shared_sort_orders_unique(replay):
    """PHP's cleanup for this case calls `$db->execute_once`, which does not
    exist."""
    with_dens(replay, dens=("A", "B", "C"))
    a, b, c = (partition(replay, n) for n in "ABC")
    groups.order_partitions(replay.event, [b, c])
    orders = dict(
        Partition.objects.filter(event=replay.event).values_list("name", "sort_order")
    )
    assert len(set(orders.values())) == 3
    assert sorted(orders, key=orders.get) == ["B", "A", "C"]


# ----- Roster -------------------------------------------------------------


def test_fill_in_missing_roster_entries_is_idempotent(replay):
    with_dens(replay)
    RosterEntry.objects.filter(event=replay.event).delete()
    assert groups.fill_in_missing_roster_entries(replay.event.id) == 4
    assert groups.fill_in_missing_roster_entries(replay.event.id) == 0
    assert RosterEntry.objects.filter(event=replay.event).count() == 4


# ----- Awards, renames and shared subgroups -------------------------------


def _ballot_award(replay, **where):
    award = AwardFactory(event=replay.event, **where)
    BallotAwardFactory(award=award)
    return award


def test_deleting_a_group_deletes_its_awards_on_the_ballot(replay):
    groups.add_group(replay.event, "Empty")
    award = _ballot_award(replay, group=group(replay, "Empty"))
    groups.delete_group(group(replay, "Empty"))
    assert not Award.objects.filter(pk=award.pk).exists()
    assert not BallotAward.objects.filter(award_id=award.pk).exists()


def test_a_rule_change_deletes_group_awards_on_the_ballot(replay):
    with_dens(replay)
    _ballot_award(replay, group=group(replay, "Lions"))
    _ballot_award(replay, subgroup=Subgroup.objects.get(name="Tigers"))
    pack = _ballot_award(replay)
    groups.form_groups_by_rule(replay.event, "one-group")
    assert list(Award.objects.filter(event=replay.event)) == [pack]


def test_rename_group_refuses_a_name_its_two_partitions_would_share(replay):
    """By partition, the partitions take the group's name, and partition
    names are unique."""
    with_dens(replay)
    lions = group(replay, "Lions")
    groups.move_partition(partition(replay, "Tigers"), GroupRef("classid", lions.pk))
    assert Partition.objects.filter(subgroup__group=lions).count() > 1
    assert failure_of(groups.rename_group, lions, "Cubs") == Failure.NAME_TAKEN


def test_deleting_a_partition_keeps_another_partition_of_its_subgroup(replay):
    """The Tigers partition moves into the Lions subgroup without its
    racers, so once the Lions racers go, only the partitions hold it."""
    with_dens(replay)
    groups.form_groups_by_rule(replay.event, "custom")
    lions = Subgroup.objects.get(event=replay.event, name="Lions")
    groups.move_partition(
        partition(replay, "Tigers"),
        GroupRef("rankid", lions.pk),
        from_filter=GroupRef("classid", lions.group_id),
    )
    RosterEntry.objects.filter(racer__partition__name="Lions").delete()
    Racer.objects.filter(partition__name="Lions").delete()
    groups.delete_partition(partition(replay, "Lions"))
    assert partition(replay, "Tigers").subgroup == lions


# ----- Review fixes (#101) --------------------------------------------------


def test_rank_edit_gives_a_subgroup_with_no_partition_a_partitions_name(replay):
    """PHP accepts this: no partition is renamed, so none can collide."""
    with_dens(replay)
    bears = groups.add_group(replay.event, "Bears")
    cubs = groups.add_subgroup(replay.event, bears.pk, "Cubs")
    groups.rename_subgroup(cubs, "Lions")
    cubs.refresh_from_db()
    assert cubs.name == "Lions"
    assert partition(replay, "Lions").subgroup.group == group(replay, "Lions")


def test_by_partition_folds_case_of_names_cut_to_a_group_name(replay):
    """Two partitions whose names differ past the first 75 characters, and
    in case before them, give one group: group names ignore case."""
    names = ["L" * 75 + " one", "l" * 75 + " two"]
    for i, name in enumerate(names):
        replay.import_racer(str(i), "Racer", name)
    groups.form_groups_by_rule(replay.event, "custom")
    groups.form_groups_by_rule(replay.event, "by-partition")
    made = list(Group.objects.filter(event=replay.event))
    assert [g.name for g in made] == ["L" * 75]
    assert set(
        Racer.objects.filter(event=replay.event).values_list("group", flat=True)
    ) == {made[0].pk}


# ----- D31 ------------------------------------------------------------------


def _writes(queries):
    return [
        q["sql"]
        for q in queries
        if q["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}
    ]


@pytest.mark.parametrize("with_result", [False, True])
@pytest.mark.parametrize("rule", list(groups.Rule))
def test_applying_the_rule_in_force_writes_nothing(replay, rule, with_result):
    """A re-applied rule keeps the rounds, results and playlist steps a real
    rule change deletes, and succeeds even once a heat has a result."""
    with_dens(replay)
    groups.form_groups_by_rule(replay.event, "custom")
    groups.form_groups_by_rule(replay.event, rule)
    round = Round.objects.filter(event=replay.event).order_by("id").first()
    Round.objects.create(event=replay.event, group=round.group, number=2)
    step = PlaylistEntryFactory(group=round.group, round_number=2)
    if with_result:
        racer = round.roster.first().racer
        HeatLaneFactory(round=round, racer=racer, finish_place=1)
    before = read_state(replay.event)

    with CaptureQueriesContext(connection) as queries:
        groups.form_groups_by_rule(replay.event, rule)

    assert _writes(queries) == []
    assert read_state(replay.event) == before
    assert PlaylistEntry.objects.filter(pk=step.pk).exists()
    assert RaceSettings.objects.get(event=replay.event).group_formation_rule == rule


# Pairs that differ only in case. SQLite's `lower()` folds the ASCII pair
# only, so each pair here proves both databases refuse the same names.
CASE_PAIRS = [("Lions", "LIONS"), ("École", "éCOLE"), ("ΟΔΟΣ", "οδος")]


@pytest.mark.parametrize("taken, name", CASE_PAIRS)
def test_partition_add_refuses_a_name_another_partition_holds_in_any_case(
    replay, taken, name
):
    with_dens(replay, dens=(taken, "Tigers"))
    assert failure_of(groups.add_partition, replay.event, name) == Failure.NAME_TAKEN
    assert Partition.objects.filter(event=replay.event).count() == 2


@pytest.mark.parametrize("taken, name", CASE_PAIRS)
def test_partition_edit_refuses_a_name_another_partition_holds_in_any_case(
    replay, taken, name
):
    """Under one group no group takes the partition's name, so only the
    partition names collide."""
    with_dens(replay, dens=(taken, "Tigers"))
    groups.form_groups_by_rule(replay.event, "one-group")
    tigers = partition(replay, "Tigers")
    assert failure_of(groups.rename_partition, tigers, name) == Failure.NAME_TAKEN
    assert partition(replay, "Tigers") == tigers


@pytest.mark.parametrize("taken, name", CASE_PAIRS)
def test_rank_edit_refuses_a_partition_name_in_any_case(replay, taken, name):
    with_dens(replay, dens=(taken, "Tigers"))
    groups.form_groups_by_rule(replay.event, "one-group")
    tigers = partition(replay, "Tigers").subgroup
    assert failure_of(groups.rename_subgroup, tigers, name) == Failure.NAME_TAKEN
    assert partition(replay, "Tigers").subgroup == tigers


def test_class_edit_refuses_a_partition_name_in_any_case(replay):
    """By partition, the group's partition takes the group's name."""
    with_dens(replay)
    Group.objects.filter(event=replay.event, name="Lions").update(name="Den A")
    assert failure_of(groups.rename_group, group(replay, "Tigers"), "LIONS") == (
        Failure.NAME_TAKEN
    )


def test_a_partition_takes_its_own_name_in_another_case(replay):
    with_dens(replay)
    groups.rename_partition(partition(replay, "Lions"), "LIONS")
    assert partition(replay, "LIONS").subgroup.group == group(replay, "LIONS")


@pytest.mark.parametrize("taken, name", CASE_PAIRS)
def test_a_racer_imported_into_a_partition_name_in_another_case_joins_it(
    replay, taken, name
):
    """`find_or_create_partition` finds the partition rather than refusing,
    so a racer list that writes `LIONS` puts its racers in `Lions`."""
    with_dens(replay, dens=(taken, "Tigers"))
    found = partition(replay, taken)
    racer = replay.import_racer("New", "Racer", name)
    assert racer.partition == found
    assert Partition.objects.filter(event=replay.event).count() == 2


def test_a_subgroup_partition_name_taken_in_another_case_counts_up(replay):
    with_dens(replay, dens=("Webelos", "Arrows"))
    arrows = group(replay, "Arrows")
    groups.add_partition(replay.event, "ARROWS-WEBELOS")
    groups.find_or_create_subgroup(arrows, "Webelos")
    assert partition(replay, "Arrows-Webelos-1").subgroup.group == arrows


def test_order_partitions_gives_a_group_its_lowest_partition_sort_order(replay):
    """By partition, a group holding two partitions sorts by the first of
    them, whatever their ids."""
    with_dens(replay, dens=("Lions", "Tigers", "Bears"))
    lions = group(replay, "Lions")
    groups.move_partition(partition(replay, "Tigers"), GroupRef("classid", lions.pk))
    groups.order_partitions(
        replay.event,
        [partition(replay, n) for n in ("Tigers", "Bears", "Lions")],
    )
    names = Group.objects.filter(event=replay.event).order_by("sort_order")
    assert [g.name for g in names] == ["Lions", "Bears"]


def test_deleting_a_partition_keeps_its_groups_other_partitions(replay):
    """By partition, PHP deletes the group once no racer is in it, and its
    other partitions with it."""
    groups.add_partition(replay.event, "Wolves")
    groups.add_partition(replay.event, "Cubs")
    wolves = group(replay, "Wolves")
    groups.move_partition(partition(replay, "Cubs"), GroupRef("classid", wolves.pk))
    left = {"Wolves", "Wolves-Cubs"}  # the subgroup Cubs gets in Wolves
    groups.delete_partition(partition(replay, "Cubs"))
    assert (
        set(
            Partition.objects.filter(subgroup__group=wolves).values_list(
                "name", flat=True
            )
        )
        == left
    )
    for name in sorted(left):
        assert Group.objects.filter(pk=wolves.pk).exists()
        groups.delete_partition(partition(replay, name))
    assert not Group.objects.filter(pk=wolves.pk).exists()
