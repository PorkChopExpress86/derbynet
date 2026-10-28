"""The race-control endpoints and the racing polls (#31), through
`/action.php`. `test_race_control_parity.py` compares them with the PHP
captures; these tests pin the contract and the fixes."""

import json
from datetime import timedelta
from urllib.parse import parse_qsl

import pytest
from django.test import Client
from django.utils import timezone

from accounts.permissions import Perm
from core.models import RaceState
from legacy_api.registry import REGISTRY
from legacy_api.tests.conftest import log_in, post
from racing.models import HeatLane, Round
from racing.services import polls, results

pytestmark = pytest.mark.django_db

COORDINATOR_KEYS = [
    "current-heat",
    "racers",
    "timer-state",
    "replay-state",
    "last-heat",
    "refused-results",
    "heat-results",
    "classes",
    "rounds",
    "ready-aggregate",
]
# `query.poll.inc`'s value keys.
PHP_POLL_VALUES = {
    "awards",
    "best-times",
    "car-numbers",
    "classes",
    "current-heat",
    "current-reschedule",
    "ondeck",
    "heat-results",
    "last-heat",
    "partitions",
    "precision",
    "race-structure",
    "racers",
    "replay-state",
    "rounds",
    "schedule",
    "timer-state",
    "timer-trouble",
}


def reply(response) -> dict:
    return json.loads(response.content)


def ok(client, body) -> dict:
    found = reply(post(client, body))
    assert found["outcome"]["code"] == "success", found
    return found


def poll(client, **params) -> dict:
    return reply(client.get("/action.php", {"query": "poll", **params}))


def coordinator(client, **params) -> dict:
    return reply(client.get("/action.php", {"query": "poll.coordinator", **params}))


def setup_race(client, racers=(("Lions", 4), ("Tigers", 4))):
    """A coordinator's session, four lanes, every racer checked in, and each
    group's round 1 scheduled. Returns the rounds by group name."""
    log_in(client, -1, role="RaceCoordinator")
    ok(client, "action=settings.write&unused-lane-mask=0&n-lanes=4")
    car = 100
    for partition, count in racers:
        for _ in range(count):
            car += 1
            ok(
                client,
                f"action=racer.import&firstname=F{car}&lastname=L{car}"
                f"&carnumber={car}&partition={partition}",
            )
    ok(client, "action=racer.bulk&who=all&what=checkin")
    rounds = {r.group.name: r for r in Round.objects.select_related("group")}
    for round in rounds.values():
        ok(client, f"action=schedule.generate&roundid={round.pk}")
    return rounds


def finish(event, body="lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4"):
    results.record_timer_result(event, parse_qsl(body))


# ----- the coordinator payload -------------------------------------------------


def test_poll_coordinator_answers_phps_ten_keys(client, event):
    setup_race(client)

    assert list(coordinator(client)) == COORDINATOR_KEYS


def test_poll_coordinator_needs_a_login(client, event):
    """#17: PHP answers anyone, but the payload names racers (D5)."""
    found = coordinator(client)

    assert found["outcome"]["code"] == "notauthorized"
    assert "racers" not in found


def test_heat_results_follow_the_heat_the_page_shows(client, event):
    rounds = setup_race(client)
    lions = rounds["Lions"]
    ok(client, f"action=heat.select&roundid={lions.pk}&now_racing=1")
    finish(event)

    shown = coordinator(client, roundid=lions.pk, heat=1)["heat-results"]

    assert [r["lane"] for r in shown] == [1, 2, 3, 4]
    # 40 feet in 3.1 s, at 25 scale miles per mile.
    assert shown[0] == {"lane": 1, "time": 3.1, "place": 1, "speed": 219.9}
    assert coordinator(client)["heat-results"] == []


# In this order every one succeeds.
ACTIONS = {
    "heat.select": lambda r: f"roundid={r['Lions'].pk}&now_racing=0",
    "heat.rerun": lambda r: "heat=last",
    "heat.reinstate": lambda r: "",
    "result.write": lambda r: "lane1=3.0&lane2=3.1&lane3=3.2&lane4=3.3",
    "schedule.generate": lambda r: f"roundid={r['Tigers'].pk}",
    "schedule.unschedule": lambda r: f"roundid={r['Tigers'].pk}",
    "schedule.reschedule": lambda r: f"roundid={r['Lions'].pk}&dry-run=1",
    "roster.new": lambda r: f"roundid={r['Lions'].pk}&top=2",
    "roster.delete": lambda r: "roundid={final}",
    "result.delete": lambda r: f"roundid={r['Lions'].pk}",
}


def crew():
    """A logged-in RaceCrew, which has no CONTROL_RACE."""
    client = Client()
    ok(client, "action=role.login&name=RaceCrew&password=murphy")
    return client


def test_the_ten_actions_append_the_coordinator_payload(client, event):
    rounds = setup_race(client)
    ok(client, f"action=heat.select&roundid={rounds['Lions'].pk}&now_racing=1")
    finish(event)
    # A reschedule needs a racer to fit in or take out.
    ok(
        client,
        "action=racer.import&firstname=Late&lastname=Comer&carnumber=199"
        "&partition=Lions",
    )
    late = reply(client.get("/action.php", {"query": "racer.list"}))["racers"]
    late = next(r for r in late if r["carnumber"] == 199)
    ok(client, f"action=racer.pass&racer={late['racerid']}&value=1")
    final = None
    for name, fields in ACTIONS.items():
        body = fields(rounds).format(final=final)
        if name == "heat.reinstate":
            ok(client, "action=heat.rerun&heat=last")
        if name == "roster.new":
            ok(client, f"action=heat.select&roundid={rounds['Lions'].pk}&heat=2")
            ok(client, "action=heat.select&now_racing=1")
            while RaceState.objects.get(event=event).racing:
                finish(event)
        if name == "result.write":
            ok(client, f"action=heat.select&roundid={rounds['Lions'].pk}&heat=1")
        found = ok(client, f"action={name}&{body}")
        assert list(found)[2:] == [
            *(["finalists", "new-round"] if name == "roster.new" else []),
            *(["chart", "timing"] if name == "schedule.reschedule" else []),
            *COORDINATOR_KEYS,
        ], name
        if name == "roster.new":
            final = found["new-round"]["roundid"]


def test_a_logged_in_caller_who_cannot_race_gets_the_payload_with_a_refusal(
    client, event
):
    """PHP's heat.select and heat.rerun refuse inside the handler and still
    append the payload, which poll.coordinator shows any logged-in role."""
    setup_race(client)
    judge = crew()

    for name in ("heat.select", "heat.rerun"):
        found = reply(post(judge, f"action={name}&heat=current"))
        assert found["outcome"]["code"] == "notauthorized", name
        assert list(found)[2:] == COORDINATOR_KEYS, name


def test_an_anonymous_rerun_is_refused_without_the_payload(client, event):
    setup_race(client)

    found = reply(post(Client(), "action=heat.rerun&heat=current"))

    assert found["outcome"]["code"] == "notauthorized"
    assert "current-heat" not in found


# ----- the PHP defects ----------------------------------------------------------


def test_roster_new_needs_control_race(client, event):
    """S17: PHP lets anyone build a round or an aggregate group."""
    rounds = setup_race(client)
    judge = crew()
    before = Round.objects.count()

    for caller in (Client(), judge):
        found = reply(post(caller, f"action=roster.new&roundid={rounds['Lions'].pk}"))
        assert found["outcome"]["code"] == "notauthorized"
    assert Round.objects.count() == before
    endpoint = REGISTRY.get("action", "roster.new")
    assert endpoint.permission == Perm.CONTROL_RACE


def test_roster_new_for_an_unknown_round_fails_with_invalid_roundid(client, event):
    """C24: PHP's code is its message, `Invalid roundid`."""
    setup_race(client)

    found = reply(post(client, "action=roster.new&roundid=9999&top=2"))

    assert found["outcome"] == {
        "summary": "failure",
        "code": "invalid-roundid",
        "description": "Invalid roundid",
    }


def test_result_delete_returns_no_sql(client, event):
    """C23: PHP returns the SQL text it ran under `sql`."""
    rounds = setup_race(client)
    ok(client, f"action=heat.select&roundid={rounds['Lions'].pk}&now_racing=1")
    finish(event)

    found = ok(client, "action=result.delete&roundid=all")

    assert "sql" not in found
    assert not HeatLane.objects.filter(finish_time__isnull=False).exists()


def test_result_delete_of_the_current_heat(client, event):
    rounds = setup_race(client)
    lions = rounds["Lions"]
    ok(client, f"action=heat.select&roundid={lions.pk}&now_racing=1")
    finish(event)
    finish(event)
    ok(client, f"action=heat.select&roundid={lions.pk}&heat=1")

    ok(client, "action=result.delete&roundid=current&heat=current")

    raced = HeatLane.objects.filter(round=lions, finish_time__isnull=False)
    assert set(raced.values_list("heat", flat=True)) == {2}


def test_ondeck_lists_every_heat_lane_of_the_event(client, event):
    """C25, kept for parity: outside the master schedule PHP never narrows
    the query, so `ondeck` holds every round's heats (a known cost, #52)."""
    rounds = setup_race(client)
    ok(client, f"action=heat.select&roundid={rounds['Lions'].pk}&now_racing=1")

    chart = poll(client, values="ondeck")["ondeck"]["chart"]

    assert len(chart) == HeatLane.objects.count()
    assert {row["roundid"] for row in chart} == {r.pk for r in rounds.values()}


# ----- query=poll ---------------------------------------------------------------


def test_the_poll_table_holds_phps_value_keys():
    assert set(polls.POLL_VALUES) == PHP_POLL_VALUES


@pytest.mark.parametrize("key", sorted(PHP_POLL_VALUES - {"awards", "timer-trouble"}))
def test_each_poll_value_answers_its_keys_and_no_others(client, event, key):
    setup_race(client)
    expected = {
        "race-structure": [
            "use-subgroups",
            "group-formation-rule",
            "labels",
            "full-field-calc",
        ]
    }.get(key, [key])

    assert list(poll(Client(), values=key)) == expected


def test_awards_writes_nothing_and_timer_trouble_only_with_a_troubled_timer(
    client, event
):
    setup_race(client)

    assert poll(Client(), values="awards") == {}
    # No timer has reported, so it is not connected, which warn-no-timer flags.
    assert poll(Client(), values="timer-trouble") == {"timer-trouble": True}
    ok(client, "action=settings.write&warn-no-timer-checkbox=1")
    assert poll(Client(), values="timer-trouble") == {}


def test_the_values_come_in_the_order_asked(client, event):
    setup_race(client)

    assert list(poll(Client(), values="schedule,current-heat,precision")) == [
        "schedule",
        "current-heat",
        "precision",
    ]


# ----- query=poll.results -------------------------------------------------------


def test_poll_results_lists_the_round_and_the_last_five_minutes(client, event):
    rounds = setup_race(client)
    lions, tigers = rounds["Lions"], rounds["Tigers"]
    ok(client, f"action=heat.select&roundid={tigers.pk}&now_racing=1")
    finish(event)
    finish(event)
    now = timezone.now()
    HeatLane.objects.filter(round=tigers, heat=1).update(
        completed=now - timedelta(seconds=299)
    )
    HeatLane.objects.filter(round=tigers, heat=2).update(
        completed=now - timedelta(seconds=301)
    )
    ok(client, f"action=heat.select&roundid={lions.pk}&now_racing=0")

    found = polls.poll_results(event, now=now)

    listed = {r["resultid"] for r in found["results"]}
    lions_rows = set(HeatLane.objects.filter(round=lions).values_list("id", flat=True))
    recent = set(
        HeatLane.objects.filter(round=tigers, heat=1).values_list("id", flat=True)
    )
    assert listed == lions_rows | recent
    assert found["current-heat"]["roundid"] == lions.pk
    assert found["next-heat"] == {"roundid": lions.pk, "heat": 2}


def test_the_results_window_does_not_depend_on_the_time_zone(event, settings):
    now = timezone.now()
    cutoffs = set()
    for zone in ("UTC", "America/New_York", "Pacific/Kiritimati"):
        settings.TIME_ZONE = zone
        cutoffs.add(polls.results_cutoff(now))
    assert cutoffs == {now - timedelta(seconds=300)}


def test_poll_results_is_anonymous_and_formats_times_as_text(client, event):
    rounds = setup_race(client)
    ok(client, f"action=heat.select&roundid={rounds['Lions'].pk}&now_racing=1")
    finish(event)

    found = reply(Client().get("/action.php", {"query": "poll.results"}))

    outcomes = [r["outcome"] for r in found["results"]]
    assert outcomes[:4] == ["x3.100", "x3.200", "x3.300", "x3.400"]
    assert set(outcomes[4:]) == {"--"}
    assert set(found["schedule-signature"]) == {str(r.pk) for r in rounds.values()}


def test_the_race_state_is_unchanged_by_the_polls(client, event):
    rounds = setup_race(client)
    ok(client, f"action=heat.select&roundid={rounds['Lions'].pk}&now_racing=1")
    before = RaceState.objects.values().get(event=event)

    coordinator(client)
    poll(Client(), values=",".join(polls.POLL_VALUES))
    Client().get("/action.php", {"query": "poll.results", "details": "1"})

    assert RaceState.objects.values().get(event=event) == before
