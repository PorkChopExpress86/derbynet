"""The award, judging and presentation endpoints (#33): PHP's wire contract
over the award services, with the award eligibility of D33 and D35."""

import json
from urllib.parse import urlencode

import pytest
from django.utils import timezone

from awards.models import Award
from awards.services import set_partition_list
from core.models import RaceSettings
from core.tests.factories import EventFactory
from legacy_api.tests.conftest import log_in, post
from racing.models import HeatLane, Round
from roster.models import Group, Partition, Racer
from roster.tests.factories import GroupFactory, RacerFactory
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db

OTHER, SPEED_GENERAL, DESIGN_TROPHY, AD_HOC = 3, 2, 4, 6


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def act(client, action_name, **fields):
    body = urlencode({"action": action_name, **fields})
    return json.loads(post(client, body).content)


def outcome(reply):
    return reply["outcome"]["code"]


def award_list(client, **fields):
    return json.loads(
        client.get("/action.php", {"query": "award.list", **fields}).content
    )


def named(reply, name):
    [found] = [a for a in reply["awards"] if a["awardname"] == name]
    return found


def test_a_new_award_lists_with_phps_fields(coordinator, event):
    reply = act(
        coordinator,
        "award.edit",
        awardid="new",
        name="Best Paint",
        awardtypeid=DESIGN_TROPHY,
    )

    assert outcome(reply) == "success"
    award = named(reply, "Best Paint")
    assert award == {
        "awardid": award["awardid"],
        "awardname": "Best Paint",
        "awardtype": "Design Trophy",
        "awardtypeid": DESIGN_TROPHY,
        "adhoc": False,
        "classid": 0,
        "eligible-classids": [],
        "eligible-rankids": [],
        "rankid": 0,
        "racerid": 0,
        "firstname": "",
        "lastname": "",
        "carnumber": None,
        # PHP's first award: 1 + max(1, no sort yet).
        "sort": 2,
        "ballot_depth": 0,
        "votes": [],
    }
    assert list(award) == list(named(award_list(coordinator), "Best Paint"))
    assert [t["awardtype"] for t in reply["award-types"]] == [
        "Design General",
        "Speed General",
        "Other",
        "Design Trophy",
        "Speed Trophy",
    ]
    assert {"award-types", "awards", "speed-awards", "classes"} <= set(reply)


# ----- D33 and D35: who may win ------------------------------------------------


@pytest.fixture
def raced(client):
    """Lions, Tigers and Parents, one racer each per time, raced in round 1.
    A Parents racer is the fastest of the pack."""
    replay = Replay()
    event = replay.event
    times = {"Parents": [2.9], "Lions": [3.01, 3.02], "Tigers": [3.015]}
    for den, den_times in times.items():
        for i, _ in enumerate(den_times):
            replay.import_racer(f"{den}{i}", "Racer", den)
    Racer.objects.filter(event=event).update(passed_inspection=True)
    for den, den_times in times.items():
        round_ = Round.objects.get(group__name=den, number=1)
        racers = Racer.objects.filter(group__name=den).order_by("pk")
        for heat, (racer, time) in enumerate(zip(racers, den_times, strict=True), 1):
            HeatLane.objects.create(
                event=event,
                round=round_,
                heat=heat,
                lane=1,
                racer=racer,
                finish_time=time,
                completed=timezone.now(),
            )
    log_in(client, -1, role="RaceCoordinator")
    return event


def racer(first):
    return Racer.objects.get(first_name=first)


def judged(client, name="Best Paint", **fields):
    reply = act(
        client,
        "award.edit",
        awardid="new",
        name=name,
        awardtypeid=DESIGN_TROPHY,
        **fields,
    )
    return named(reply, name)["awardid"]


def winner(client, awardid, first):
    return act(client, "award.winner", awardid=awardid, racerid=racer(first).pk)


def presented(client, key):
    assert outcome(act(client, "award.present", key=key, reveal=1)) == "success"
    reply = json.loads(client.get("/action.php", {"query": "award.current"}).content)
    return reply.get("current", {}).get("recipient")


def test_a_racer_not_eligible_for_speed_awards_can_win_a_judged_award(client, raced):
    """D33."""
    Racer.objects.filter(pk=racer("Parents0").pk).update(
        not_eligible_for_speed_awards=True
    )
    Racer.objects.filter(pk=racer("Lions0").pk).update(exclude=True)
    award = judged(client)

    assert outcome(winner(client, award, "Parents0")) == "success"
    assert outcome(winner(client, award, "Lions0")) == "ineligible"
    # Neither takes a place in the pack's speed awards.
    assert presented(client, "speed-1") == "Tigers0 Racer"
    assert presented(client, f"award-{award}") == "Parents0 Racer"


def test_a_pack_award_at_all_partitions_goes_to_a_racer_the_speed_list_leaves_out(
    client, raced
):
    """D35: the pack's speed-award list leaves out Parents."""
    set_partition_list(
        raced, Partition.objects.filter(event=raced).exclude(name="Parents")
    )
    award = judged(client)

    assert outcome(winner(client, award, "Parents0")) == "success"
    assert presented(client, "speed-1") == "Lions0 Racer"


def test_a_judged_award_whose_list_leaves_out_parents_refuses_a_parents_racer(
    client, raced
):
    """D35."""
    award = judged(client)
    set_partition_list(
        raced,
        Partition.objects.filter(event=raced).exclude(name="Parents"),
        award=Award.objects.get(pk=award),
    )

    assert outcome(winner(client, award, "Parents0")) == "ineligible"
    assert outcome(winner(client, award, "Tigers0")) == "success"


def test_a_group_award_refuses_a_racer_of_another_group(client, raced):
    lions = Group.objects.get(name="Lions")
    award = judged(client, class_and_rank=f"{lions.pk},0")

    assert outcome(winner(client, award, "Tigers0")) == "wrong-class"
    assert outcome(winner(client, award, "Lions1")) == "success"
    reply = act(client, "award.winner", awardid=award, racerid=0)
    assert named(reply, "Best Paint")["racerid"] == 0


def test_the_full_field_rule_names_a_group_of_this_event_only(coordinator, event):
    elsewhere = GroupFactory(event=EventFactory())
    own = GroupFactory(event=event)

    assert (
        outcome(
            act(coordinator, "award.calc-rule", **{"full-field-calc": elsewhere.pk})
        )
        == "not-written"
    )
    assert (
        outcome(act(coordinator, "award.calc-rule", **{"full-field-calc": own.pk}))
        == "success"
    )
    assert RaceSettings.objects.get(event=event).full_field_group_id == own.pk


# ----- numbers past their columns --------------------------------------------


def test_a_ballot_depth_past_its_column_is_kept_at_the_column(coordinator, event):
    reply = act(
        coordinator,
        "award.edit",
        awardid="new",
        name="Paint",
        awardtypeid=DESIGN_TROPHY,
        ballot_depth=40000,
    )

    assert outcome(reply) == "success"
    assert named(reply, "Paint")["ballot_depth"] == 32767


def test_a_new_award_after_the_highest_sort_stays_in_its_column(coordinator, event):
    act(
        coordinator,
        "award.edit",
        awardid="new",
        name="Last",
        awardtypeid=DESIGN_TROPHY,
        sort=2**31 - 1,
    )
    reply = act(
        coordinator, "award.edit", awardid="new", name="Next", awardtypeid=DESIGN_TROPHY
    )

    assert outcome(reply) == "success"
    assert named(reply, "Next")["sort"] == 2**31 - 1


@pytest.mark.parametrize("car", ["abc", "99999999999", ""])
def test_an_import_by_a_car_number_that_is_no_number_names_no_racer(
    coordinator, event, car
):
    RacerFactory(group__event=event, car_number=0)
    reply = act(
        coordinator,
        "award.import",
        awardname="Paint",
        awardtype="Design Trophy",
        carnumber=car,
    )

    assert outcome(reply) == "unknowncarnumber"


def test_an_anonymous_kiosk_reads_the_current_award_but_may_not_present(
    client, event
):
    """kiosk.php grants PRESENT_AWARDS to every visitor; Django does not.
    The award kiosk only reads `award.current` anonymously (D5)."""
    reply = act(client, "award.present", key="speed-1")

    assert outcome(reply) == "notauthorized"
    current = client.get("/action.php", {"query": "award.current"})
    # A query answers a refusal with an outcome, and an answer without one.
    assert "outcome" not in json.loads(current.content)
