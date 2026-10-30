"""The replay command queue against the PHP capture (#34):
`capture/scenarios/09-replay.sh` replayed step by step, as
`test_race_control_parity` runs its scenarios.

The capture's replay client polls with no login, which D16 refuses, so here
it polls from a session logged in as the race coordinator; the bodies are
the same. `test_replay_messages` shows the refusal. The scenario sleeps 11 s
before its last poll; here the last contact moves back 11 s instead.
"""

from datetime import timedelta

import pytest
from django.test import Client

from kiosks.models import ReplayStatus
from legacy_api.tests.test_race_control_parity import Scenario, _dump, active_event

pytestmark = pytest.mark.django_db

REPLAY = "http/replay/command-sequence.http"


def run_replay(client) -> Scenario:
    s = Scenario(client)
    s.reset()
    replay = Client()
    s.post_as(replay, "action=role.login&name=RaceCoordinator&password=doyourbest")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")
    for i in range(1, 6):
        s.import_racer(
            f"firstname=Replay{i}&lastname=Racer&carnumber=20{i}&partition=Wolves"
        )
    s.learn_rows()
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.post_ok("action=schedule.generate&roundid=1")

    def poll(label):
        s.capture(label, replay)

    s.scenario(REPLAY)
    poll("replay-first-poll-is-greeted")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    poll("replay-after-heat-select-sees-cancel-and-start")
    s.post_ok("action=heat.select&roundid=1&now_racing=0")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    poll("replay-after-racing-off-and-on")
    s.timer_capture("timer-started")
    poll("replay-after-gate-opens")
    s.timer_capture("timer-finished")
    poll("replay-after-finish-playing")
    poll("replay-after-playback-finished")
    s.capture("rerun-last-heat")
    poll("replay-after-rerun")
    s.capture("replay-trigger")
    poll("replay-after-trigger")
    s.capture("replay-test")
    poll("replay-after-test")
    s.capture("coordinator-poll-with-replay-connected")
    s.post_ok(
        "action=settings.write&replay-skipback=2500&replay-num-showings=3"
        "&replay-rate=25"
    )
    s.capture("replay-trigger-with-other-settings")
    poll("replay-after-trigger-with-other-settings")
    poll("replay-poll-with-no-status")
    status = ReplayStatus.objects.get(event=active_event())
    ReplayStatus.objects.filter(pk=status.pk).update(
        last_contact=status.last_contact - timedelta(seconds=11)
    )
    poll("replay-greeted-again-after-silence")
    return s


def test_the_replay_scenario_matches_php(client, event):
    s = run_replay(client)
    _dump(s)
    assert s.mismatches() == []
