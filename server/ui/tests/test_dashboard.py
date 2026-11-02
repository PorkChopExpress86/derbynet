"""The race dashboard's views (#41): the guard, the poll and its 204, and
every control over the racing services."""

import re

import pytest

from core.models import RaceSettings, RaceState
from kiosks.models import ReplayStatus
from racing.models import FinalRequest, HeatLane, LaneClosure, Round
from racing.services import finals, heats, rounds, timer
from racing.services.rounds import FollowOn
from ui.tests.race import (
    add_racers,
    chart,
    current,
    heat_cars,
    race_setup,
    run_heat,
    scheduled,
)
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


@pytest.fixture
def wolves(event):
    return race_setup(event)["Wolves"]


def signature(html: str) -> str:
    return re.search(r'name="sig" value="([^"]+)"', html).group(1)


def live_of(response) -> str:
    html = response.content.decode()
    assert 'id="live" hx-swap-oob="true"' in html, html[:500]
    return html


def post(client, url, data=None, **extra):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", **extra)


# ----- the page, the guard, the poll ----------------------------------------------


def test_the_page_needs_control_race(login, event):
    assert login("RaceCoordinator").get("/dashboard/").status_code == 200
    crew = login("RaceCrew").get("/dashboard/")
    assert crew.status_code == 302 and "/login/" in crew["Location"]
    assert login("RaceCrew").get("/dashboard/live/").status_code == 302
    refused = post(login("RaceCrew"), "/dashboard/racing/", {"now_racing": "1"})
    assert refused.status_code == 403


def test_coordinator_php_moves_to_the_dashboard(client, event):
    response = client.get("/coordinator.php?x=1")
    assert response.status_code == 301
    assert response["Location"] == "/dashboard/?x=1"


def test_the_poll_answers_204_while_nothing_changed(coordinator, event, wolves):
    scheduled(wolves)
    html = coordinator.get("/dashboard/").content.decode()
    shown = {"round": wolves.pk, "heat": 1, "hold": 0, "sig": signature(html)}

    with no_session_writes():
        assert coordinator.get("/dashboard/live/", shown).status_code == 204
        heats.set_racing(event, False)
        changed = coordinator.get("/dashboard/live/", shown)
    assert changed.status_code == 200
    assert 'id="live"' in changed.content.decode()
    assert signature(changed.content.decode()) != shown["sig"]


def test_the_timer_age_alone_changes_no_signature(coordinator, event, wolves):
    """The page counts the seconds since the timer called; only a new call
    changes what it shows."""
    from django.utils import timezone

    from racing.models import TimerStatus

    TimerStatus.objects.filter(event=event).update(
        state=TimerStatus.State.CONNECTED, last_contact=timezone.now()
    )
    html = coordinator.get("/dashboard/").content.decode()
    assert "Timer: CONNECTED" in html and "sec.)" not in html
    shown = {"sig": signature(html), "round": "", "heat": "", "hold": 0}
    assert coordinator.get("/dashboard/live/", shown).status_code == 204


def test_a_finished_heat_holds_on_screen_for_10_seconds(
    coordinator, event, wolves, monkeypatch
):
    scheduled(wolves)
    first = heat_cars(wolves, 1)
    run_heat(event, (2.1, 2.2, 2.3, 2.4))  # Racing moves to heat 2.
    clock = [1_000_000]
    monkeypatch.setattr("ui.dashboard.state._now_ms", lambda: clock[0])

    held = coordinator.get(
        "/dashboard/live/", {"round": wolves.pk, "heat": 1, "hold": 0}
    ).content.decode()
    assert "data-holding" in held and "Results of heat 1" in held
    assert 'name="hold" value="1010000"' in held
    assert f">{first[0]:03d}<" in held
    assert "2.100" in held

    clock[0] += 10_001
    moved = coordinator.get(
        "/dashboard/live/", {"round": wolves.pk, "heat": 1, "hold": 1010000}
    ).content.decode()
    assert "data-holding" not in moved and "Heat 2 of" in moved


# ----- the current heat ------------------------------------------------------------


def test_the_racing_switch_and_heat_buttons(coordinator, event, wolves):
    scheduled(wolves)
    live_of(post(coordinator, "/dashboard/racing/", {"now_racing": "0"}))
    assert current(event) == (wolves.pk, 1, False)
    live_of(post(coordinator, "/dashboard/heat/next/"))
    assert current(event)[:2] == (wolves.pk, 2)
    live_of(post(coordinator, "/dashboard/heat/prev/"))
    assert current(event)[:2] == (wolves.pk, 1)


def test_manual_results_go_to_the_heat_on_screen(coordinator, event, wolves):
    scheduled(wolves)
    sheet = coordinator.get(
        "/dashboard/results/sheet/", {"round": wolves.pk, "heat": 1}
    ).content.decode()
    assert 'name="lane1"' in sheet and "data-replay-trigger" not in sheet

    reply = post(
        coordinator,
        "/dashboard/results/",
        {"round": wolves.pk, "heat": 1, "lane1": "2.5", "lane2": "2.6"},
    )
    assert "Results saved." in live_of(reply)
    lanes = HeatLane.objects.filter(round=wolves, heat=1).order_by("lane")
    assert [float(x.finish_time) for x in lanes[:2]] == [2.5, 2.6]
    assert current(event)[:2] == (wolves.pk, 2)

    stale = post(
        coordinator,
        "/dashboard/results/",
        {"round": wolves.pk, "heat": 1, "lane1": "3"},
    )
    assert stale.status_code == 422


def test_the_results_sheet_triggers_a_replay_without_a_timer(
    coordinator, event, wolves
):
    scheduled(wolves)
    RaceSettings.objects.filter(event=event).update(warn_no_timer=False)
    sheet = coordinator.get(
        "/dashboard/results/sheet/", {"round": wolves.pk, "heat": 1}
    ).content.decode()
    assert "data-replay-trigger" in sheet
    assert post(coordinator, "/dashboard/replay/trigger/").status_code == 204
    run_heat(event)
    again = coordinator.get(
        "/dashboard/results/sheet/", {"round": wolves.pk, "heat": 1}
    ).content.decode()
    assert "data-replay-trigger" not in again and "Discard results" in again


def test_discard_reaches_only_the_heat_on_screen(coordinator, event, wolves):
    """The current heat moved after the page last polled: the discard still
    clears the heat the page showed, and Undo brings its results back."""
    scheduled(wolves)
    run_heat(event, (2.1, 2.2, 2.3, 2.4))
    run_heat(event, (3.1, 3.2, 3.3, 3.4))
    assert current(event)[:2] == (wolves.pk, 3)

    reply = post(
        coordinator, "/dashboard/results/discard/", {"round": wolves.pk, "heat": 1}
    )
    html = live_of(reply)
    times = dict(
        HeatLane.objects.filter(round=wolves).values_list("heat", "finish_time")
    )
    assert times[1] is None and times[2] is not None
    undo = re.search(r'hx-post="([^"]*results/restore[^"]*)"', html).group(1)

    live_of(post(coordinator, undo.replace("&amp;", "&")))
    restored = HeatLane.objects.filter(round=wolves, heat=1).order_by("lane")
    assert [float(x.finish_time) for x in restored] == [2.1, 2.2, 2.3, 2.4]
    assert current(event)[:2] == (wolves.pk, 3)


@pytest.mark.parametrize(
    ("setup", "state", "label"),
    [
        ("fresh", "none", "Re-Run"),
        ("raced", "available", "Re-Run Previous"),
        ("results-on-current", "current", "Re-Run This Heat"),
        ("rerun", "recoverable", "Reinstate Heat"),
    ],
)
def test_rerun_has_four_states(coordinator, event, wolves, setup, state, label):
    scheduled(wolves)
    if setup == "raced":
        run_heat(event)
    elif setup == "results-on-current":
        heats.set_racing(event, False)
        run_heat(event)
    elif setup == "rerun":
        run_heat(event)
        heats.rerun(event, "last")
    html = coordinator.get("/dashboard/").content.decode()
    assert f'data-rerun="{state}"' in html and f">{label}</button>" in html
    if state != "none":
        live_of(post(coordinator, "/dashboard/rerun/", {"rerun": state}))


def test_rerun_then_reinstate(coordinator, event, wolves):
    scheduled(wolves)
    run_heat(event)
    live_of(post(coordinator, "/dashboard/rerun/", {"rerun": "available"}))
    assert not HeatLane.objects.filter(round=wolves, heat=1, finish_time__isnull=False)
    live_of(post(coordinator, "/dashboard/rerun/", {"rerun": "recoverable"}))
    assert HeatLane.objects.filter(round=wolves, heat=1, finish_time__isnull=False)


def test_start_race_shows_when_the_timer_offers_remote_start(
    coordinator, event, wolves
):
    from django.utils import timezone

    from racing.models import TimerStatus

    scheduled(wolves)
    assert "Start Race" not in coordinator.get("/dashboard/").content.decode()
    TimerStatus.objects.filter(event=event).update(
        remote_start_seen_at=timezone.now(),
        last_contact=timezone.now(),
        state=TimerStatus.State.STAGING,
    )
    assert "Start Race" in coordinator.get("/dashboard/").content.decode()
    live_of(post(coordinator, "/dashboard/remote-start/"))
    assert TimerStatus.objects.get(event=event).remote_start_requested


def test_not_monitoring_the_timer(coordinator, event):
    RaceSettings.objects.filter(event=event).update(warn_no_timer=False)
    html = coordinator.get("/dashboard/").content.decode()
    assert "Not monitoring timer state" in html and 'data-action="timer"' not in html


def test_replay_trigger_and_settings(coordinator, event):
    from django.utils import timezone

    ReplayStatus.objects.filter(event=event).update(
        state=ReplayStatus.State.CONNECTED, last_contact=timezone.now()
    )
    html = coordinator.get("/dashboard/").content.decode()
    assert "Trigger Replay" in html
    live_of(post(coordinator, "/dashboard/replay/test/"))
    sheet = coordinator.get("/dashboard/replay/settings/sheet/").content.decode()
    assert 'value="4000" selected' in sheet
    live_of(
        post(
            coordinator,
            "/dashboard/replay/settings/",
            {
                "replay-skipback": "2500",
                "replay-num-showings": "3",
                "replay-rate": "25",
            },
        )
    )
    settings = RaceSettings.objects.get(event=event)
    assert (settings.replay_skipback_ms, settings.replay_num_showings) == (2500, 3)
    assert settings.replay_rate == 25


def test_simulated_racing_shows_a_herald_with_stop(coordinator, event):
    timer.set_test_mode(event, True)
    heats.set_racing(event, True)
    html = coordinator.get("/dashboard/").content.decode()
    assert "Simulated racing in progress" in html
    live_of(post(coordinator, "/dashboard/timer-test/stop/"))
    assert not RaceState.objects.get(event=event).timer_test


def test_unexpected_results_count(coordinator, event):
    RaceState.objects.filter(event=event).update(refused_results=2)
    html = coordinator.get("/dashboard/").content.decode()
    assert "2 unexpected results from the timer have been received." in html


# ----- rounds ----------------------------------------------------------------------


def test_schedule_and_race(coordinator, event):
    bears = race_setup(event, groups=(("Wolves", 6), ("Bears", 5)))["Bears"]
    sheet = coordinator.get(f"/dashboard/round/{bears.pk}/sheet/schedule/").content
    assert b"Schedule + Race" in sheet and b"Schedule Only" in sheet
    live_of(
        post(
            coordinator,
            f"/dashboard/round/{bears.pk}/schedule/",
            {"runs": "2", "race": "1"},
        )
    )
    assert current(event) == (bears.pk, 1, True)
    # 5 racers twice in each of 4 lanes: 40 runs, 4 to a heat.
    assert HeatLane.objects.filter(round=bears).count() == 40


def test_schedule_only_then_race_and_make_changes(coordinator, event):
    found = race_setup(event, groups=(("Wolves", 6), ("Bears", 5)))
    wolves, bears = found["Wolves"], found["Bears"]
    live_of(
        post(
            coordinator,
            f"/dashboard/round/{bears.pk}/schedule/",
            {"runs": "1", "race": "0"},
        )
    )
    assert HeatLane.objects.filter(round=bears).exists()
    assert current(event)[2] is False
    live_of(post(coordinator, f"/dashboard/round/{bears.pk}/race/"))
    assert current(event) == (bears.pk, 1, True)
    scheduled(wolves, race=False)
    run_heat(event)
    live_of(post(coordinator, f"/dashboard/round/{wolves.pk}/changes/"))
    assert current(event) == (wolves.pk, 1, False)


def test_unschedule_and_delete_round(coordinator, event, wolves):
    scheduled(wolves, race=False)
    sheet = coordinator.get(f"/dashboard/round/{wolves.pk}/sheet/unschedule/").content
    assert b"Unschedule 8 heats" in sheet
    live_of(post(coordinator, f"/dashboard/round/{wolves.pk}/unschedule/"))
    assert not HeatLane.objects.filter(round=wolves).exists()

    finished(event, wolves)
    built = rounds.new_round_roster(FollowOn(wolves), 4)
    sheet = coordinator.get(f"/dashboard/round/{built.round.pk}/sheet/delete/").content
    assert b"Delete Wolves, Round 2" in sheet
    live_of(post(coordinator, f"/dashboard/round/{built.round.pk}/delete/"))
    assert not Round.objects.filter(pk=built.round.pk).exists()


def test_repeat_round_purges_results_after_a_confirm(coordinator, event, wolves):
    scheduled(wolves)
    run_heat(event)
    sheet = coordinator.get(f"/dashboard/round/{wolves.pk}/sheet/purge/").content
    assert b"Purge 1 heat of results" in sheet
    live_of(post(coordinator, f"/dashboard/round/{wolves.pk}/purge/"))
    assert not HeatLane.objects.filter(round=wolves, finish_time__isnull=False)


def test_adjust_schedule_fits_in_late_racers(coordinator, event, wolves):
    scheduled(wolves)
    run_heat(event)
    late = add_racers(event, "Wolves", 1, 150)[0]
    html = coordinator.get("/dashboard/").content.decode()
    assert "1 not in schedule" in html and 'data-button="adjust"' in html
    sheet = coordinator.get(f"/dashboard/round/{wolves.pk}/sheet/adjust/").content
    assert b"150 Wolves1 Racer is fitted in." in sheet
    live_of(post(coordinator, f"/dashboard/round/{wolves.pk}/adjust/"))
    assert HeatLane.objects.filter(round=wolves, racer=late).exists()


def test_master_schedule_next_up(coordinator, event):
    found = race_setup(event, groups=(("Wolves", 6), ("Bears", 6)))
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    for r in found.values():
        scheduled(r, race=False)
    html = coordinator.get("/dashboard/").content.decode()
    assert "Interleaved Schedule" in html and "Next Up" in html
    assert "data-master-heats" in html
    assert "Rounds Playlist" not in html
    live_of(post(coordinator, "/dashboard/heat/next-up/"))
    assert current(event)[1] == 1


# ----- new rounds ------------------------------------------------------------------


def finished(event, round):
    scheduled(round)
    while current(event)[2]:
        run_heat(event)


def test_the_new_round_stepper_makes_a_follow_on_round(coordinator, event, wolves):
    finished(event, wolves)
    html = coordinator.get("/dashboard/").content.decode()
    assert "Add New Rounds" in html
    sheet = coordinator.get("/dashboard/new-round/sheet/").content.decode()
    assert "Round 2 of Wolves" in sheet
    reply = post(
        coordinator,
        "/dashboard/new-round/",
        {"kind": "follow-on", "roundid": wolves.pk, "top": "3", "bucketed": "0"},
    )
    assert "Wolves, Round 2 made with 3 racers." in live_of(reply)
    assert Round.objects.get(group=wolves.group, number=2).roster.count() == 3


def test_the_stepper_always_asks_how_many(coordinator, event, wolves):
    finished(event, wolves)
    reply = post(
        coordinator,
        "/dashboard/new-round/",
        {"kind": "follow-on", "roundid": wolves.pk},
    )
    assert reply.status_code == 422


def test_a_new_aggregate_round_with_concerns(coordinator, event):
    found = race_setup(event, groups=(("Wolves", 4), ("Bears", 4)))
    for r in found.values():
        finished(event, r)
    sheet = coordinator.get("/dashboard/new-round/sheet/").content.decode()
    assert "Aggregate Round" in sheet
    data = {
        "kind": "new-aggregate",
        "classname": "Grand Finals",
        "top": "5",
        "bucketed": "1",
        f"classid_{found['Wolves'].group_id}": "1",
        f"classid_{found['Bears'].group_id}": "1",
    }
    html = live_of(post(coordinator, "/dashboard/new-round/", data))
    assert "Grand Finals, Round 1 made" in html
    assert "data-notice" in html and "There are only 4 eligible" in html


# ----- late racers, finals, lanes --------------------------------------------------


def test_a_late_racer_previews_then_joins(coordinator, event, wolves):
    scheduled(wolves)
    run_heat(event)
    late = add_racers(event, "Wolves", 1, 150)[0]
    before = chart(wolves)
    html = coordinator.get("/dashboard/").content.decode()
    assert f'data-not-added-row="{late.pk}"' in html

    preview = post(coordinator, f"/dashboard/late/{wolves.pk}/{late.pk}/preview/")
    text = preview.content.decode()
    assert "Add to schedule" in text and "150" in text
    assert chart(wolves) == before

    live_of(post(coordinator, f"/dashboard/late/{wolves.pk}/{late.pk}/add/"))
    assert chart(wolves) != before
    assert any(150 in heat for heat in chart(wolves))


def test_award_settings_for_a_late_racer(coordinator, event, wolves):
    scheduled(wolves)
    late = add_racers(event, "Wolves", 1, 150)[0]
    live_of(
        post(
            coordinator,
            f"/dashboard/racer/{late.pk}/awards/?setting=exclude",
            {"on": "1"},
        )
    )
    live_of(
        post(
            coordinator,
            f"/dashboard/racer/{late.pk}/awards/?setting=no-speed",
            {"on": "1"},
        )
    )
    late.refresh_from_db()
    assert late.exclude and late.not_eligible_for_speed_awards


def late_for_round_1(event, wolves):
    finished(event, wolves)
    racer = add_racers(event, "Wolves", 1, 150)[0]
    return finals.request_final_place(racer)


def test_a_final_request_is_allowed(coordinator, event, wolves):
    request = late_for_round_1(event, wolves)
    built = rounds.new_round_roster(FollowOn(wolves), 3)
    html = coordinator.get("/dashboard/").content.decode()
    assert f'data-final-request="{request.pk}"' in html
    live_of(post(coordinator, f"/dashboard/final-request/{request.pk}/allow/"))
    roster = set(built.round.roster.values_list("racer_id", flat=True))
    assert request.racer_id in roster and len(roster) == 4


def test_a_final_request_is_declined(coordinator, event, wolves):
    request = late_for_round_1(event, wolves)
    built = rounds.new_round_roster(FollowOn(wolves), 3)
    live_of(post(coordinator, f"/dashboard/final-request/{request.pk}/decline/"))
    assert FinalRequest.objects.get(pk=request.pk).state == "declined"
    assert built.round.roster.count() == 3


def test_closing_a_lane_names_the_rounds_and_reopens(coordinator, event, wolves):
    scheduled(wolves)
    run_heat(event)
    sheet = coordinator.get("/dashboard/lanes/sheet/").content.decode()
    assert "Wolves, Round 1" in sheet and 'value="0" checked' in sheet
    live_of(post(coordinator, "/dashboard/lanes/close/", {"lane": "3", "scoring": "1"}))
    assert LaneClosure.objects.filter(event=event).exists()
    html = coordinator.get("/dashboard/").content.decode()
    assert "Lane 3 closed · Drop the slowest heat" in html
    assert "Reopen lane 3" in html
    live_of(post(coordinator, "/dashboard/lanes/3/reopen/"))
    assert "Reopen lane 3" not in coordinator.get("/dashboard/").content.decode()


def test_a_service_refusal_is_a_422_toast(coordinator, event, wolves):
    reply = post(coordinator, "/dashboard/lanes/close/", {"lane": "9"})
    assert reply.status_code == 422 and b'role="alert"' in reply.content


def test_a_round_that_is_not_found_is_a_404(coordinator, event, wolves):
    assert post(coordinator, "/dashboard/round/999999/race/").status_code == 404
