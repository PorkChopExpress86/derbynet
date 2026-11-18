"""The timer protocol against the PHP captures (#32): `capture/scenarios/
03-timer.sh` replayed step by step, every recorded reply compared byte for
byte with Django's ids written as PHP's.

The scenario's transcripts share one race, so they run in order in one test.
`capture/scenarios/13-heat-state.sh`'s Q17 transcripts are replayed the same
way, against the Q17 deviation it writes with `WRITE_DEVIATIONS=1` (#65).
"""

import json
import os
from urllib.parse import parse_qsl, quote, urlencode

import pytest
from django.test import Client

from legacy_api.tests.php_http import as_php_body, translate
from legacy_api.tests.test_race_control_parity import (
    Scenario,
    active_event,
    assert_listed,
    listed_claim,
    write_deviations,
)
from racing.models import Round
from roster.models import Group

pytestmark = pytest.mark.django_db

TIMER_UA = "DerbyNet-Timer/golden-1.0 (jar)"
OTHER_UA = "DerbyNet-Browser-Timer/golden-2.0"


class TimerScenario(Scenario):
    def __init__(self):
        super().__init__(Client())
        self._timer_client = Client()
        self.anon = Client()

    def setup_four_lane_race(self, racers: int, mask: int):
        self.reset()
        for i in range(1, racers + 1):
            self.import_racer(
                f"firstname=First10{i}&lastname=Last10{i}&carnumber=10{i}"
                "&partition=Wolves"
            )
        self.post_ok("action=racer.bulk&who=all&what=checkin")
        self.post_ok(f"action=settings.write&unused-lane-mask={mask}&n-lanes=4")
        self.learn_rows()

    def send_as(self, label: str, client: Client, ua: str | None):
        name = self.names.pop(0)
        assert name.split(" ", 1)[1] == label, (self.fixture, name, label)
        exchange = self.transcripts[self.fixture][name]
        method, target = exchange["request"].split(" ", 1)
        headers = {"User-Agent": ua} if ua else {}
        if method == "GET":
            path, _, query = target.partition("?")
            fields = translate(
                parse_qsl(query, keep_blank_values=True), self.ids.django
            )
            response = client.get(
                f"/{path}?{urlencode(fields, quote_via=quote)}", headers=headers
            )
        else:
            body = exchange.get("request-body") or ""
            fields = translate(parse_qsl(body, keep_blank_values=True), self.ids.django)
            response = client.post(
                "/" + target,
                urlencode(fields, quote_via=quote),
                content_type="application/x-www-form-urlencoded",
                headers=headers,
            )
        self.status[(self.fixture, name)] = response.status_code
        self.got[self.fixture][name] = as_php_body(response, exchange["body"], self.ids)

    def timer_msg(self, label: str, ua: str = TIMER_UA, client=None):
        self.send_as(label, client or self.timer_client, ua)

    def coord(self, label: str):
        self.send_as(label, self.client, None)


def run_timer_scenario() -> TimerScenario:
    s = TimerScenario()
    s.setup_four_lane_race(6, 0)

    s.scenario("http/timer/before-login.http")
    s.timer_msg("hello-before-login", client=s.anon)
    s.timer_msg("heartbeat-before-login", client=s.anon)
    s.timer_msg("finished-before-login", client=s.anon)

    s.scenario("http/timer/handshake-and-idle-heartbeat.http")
    s.timer_msg("login-timer")
    for label in (
        "hello",
        "identified-with-nlanes-as-the-suite-sends-it",
        "identified",
        "heartbeat-idle",
        "heartbeat-unhealthy",
        "heartbeat-healthy-again",
        "message-not-understood",
        "message-missing",
    ):
        s.timer_msg(label)

    s.scenario("http/timer/flags.http")
    s.timer_msg("flags")
    s.coord("timer-settings-as-coordinator")
    s.timer_msg("heartbeat-after-flags")

    s.scenario("http/timer/race-staging-and-results.http")
    s.post_ok("action=schedule.generate&roundid=1")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    for label in (
        "heartbeat-while-racing-is-on",
        "heartbeat-while-staging",
        "started",
        "finished-with-times",
        "started-heat-2",
        "finished-with-places-only",
        "started-heat-3",
        "finished-for-the-wrong-heat",
        "finished-naming-the-right-heat",
        "finished-with-times-and-places",
    ):
        s.timer_msg(label)
    s.coord("poll-after-heats")

    s.scenario("http/timer/finished-all-zeroes-and-not-racing.http")
    for label in (
        "started-heat-5",
        "finished-all-zeroes",
        "finished-while-not-racing",
        "heartbeat-after-racing-stopped",
    ):
        s.timer_msg(label)
    s.coord("poll-after-all-zeroes")

    s.scenario("http/timer/unconfirmed-timer.http")
    s.post_ok("action=heat.select&now_racing=1")
    for label in (
        "identified-unconfirmed",
        "heartbeat-while-unconfirmed",
        "heartbeat-confirmed",
        "heartbeat-after-confirmed",
    ):
        s.timer_msg(label)

    s.scenario("http/timer/malfunction.http")
    for label in (
        "malfunction-detectable-by-default",
        "heartbeat-after-malfunction",
        "malfunction-detectable-1",
        "malfunction-not-detectable",
        "heartbeat-after-undetectable-malfunction",
        "malfunction-without-error-text",
    ):
        s.timer_msg(label)
    s.coord("poll-after-malfunction")
    s.timer_msg("hello-after-malfunction")
    s.timer_msg("identified-after-malfunction")

    s.scenario("http/timer/remote-start.http")
    s.coord("remote-start-before-the-timer-offers-it")
    s.timer_msg("hello-offering-remote-start")
    s.timer_msg("heartbeat-offering-remote-start")
    s.coord("remote-start-requested")
    s.timer_msg("heartbeat-after-remote-start-request")
    s.timer_msg("heartbeat-after-remote-start-delivered")
    s.timer_msg("heartbeat-withdrawing-remote-start")

    s.scenario("http/timer/competing-timers.http")
    s.timer_msg("first-timer-hello")
    s.timer_msg("second-timer-hello-within-a-second", OTHER_UA)
    s.timer_msg("second-timer-heartbeat", OTHER_UA)
    s.timer_msg("first-timer-heartbeat-is-told-it-competes")
    s.timer_msg("second-timer-heartbeat-again", OTHER_UA)

    s.scenario("http/timer/reverse-lanes.http")
    s.setup_four_lane_race(3, 1)
    s.post_ok("action=schedule.generate&roundid=1")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    s._timer_client = Client()
    s.timer_msg("login-timer")
    s.timer_msg("hello")
    s.timer_msg("identified")
    s.timer_msg("heartbeat-lane-mask-as-scheduled")
    s.post_ok("action=heat.select&roundid=1&now_racing=0")
    s.post_ok("action=settings.write&reverse-lanes=1&reverse-lanes-checkbox=1")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    s.timer_msg("heartbeat-lane-mask-reversed")
    s.timer_msg("started")
    s.timer_msg("finished-lanes-reversed")
    s.coord("poll-after-reversed-finish")
    return s


def test_timer_transcripts_match_php(event):
    s = run_timer_scenario()
    assert s.mismatches() == []


# ----- capture/scenarios/13-heat-state.sh, its Q17 cases -----------------------

ZEROES = "http/timer/zeroes-on-the-lanes-in-use.http"
TIMER_TEST = "http/timer/zeroes-in-a-timer-test.http"


def heat_state_case(s: TimerScenario, racers: int = 6, mask: int = 0):
    """`heat_state_case` of the scenario: a fresh event, Wolves checked in,
    round 1 scheduled, and a new Timer login."""
    s.reset()
    s.post_ok(f"action=settings.write&unused-lane-mask={mask}&n-lanes=4")
    for i in range(1, racers + 1):
        s.import_racer(
            f"firstname=First10{i}&lastname=Last10{i}&carnumber=10{i}&partition=Wolves"
        )
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.learn_rows()
    s.post_ok("action=schedule.generate&roundid=1")
    s.log_in_timer_again()


def run_zeroes(client=None) -> TimerScenario:
    """Three Wolves on lanes 2 and 4 (mask 5), each FINISHED from a fresh
    event; then six Wolves and a timer test that leaves lanes 3 and 4 out
    (tt-mask 12)."""
    s = TimerScenario()
    s.scenario(ZEROES)
    for finished, poll in (
        ("finished-zeroes-and-places-on-lanes-2-and-4", "poll-after-zeroes-and-places"),
        ("finished-zeroes-on-lanes-2-and-4", "poll-after-zeroes"),
    ):
        heat_state_case(s, racers=3, mask=5)
        s.post_ok("action=heat.select&roundid=1&now_racing=1")
        s.timer_msg(finished)
        s.coord(poll)
    s.scenario(TIMER_TEST)
    heat_state_case(s)
    s.post_ok("action=timer.test&tt-mask=12")
    s.timer_msg("finished-zeroes-on-the-tested-lanes")
    s.coord("timer-test-after-zeroes")
    return s


SCENARIOS = {"zeroes": run_zeroes}

# quirk: (reason, scenario, {fixture: [exchange names]}, expected file name)
DEVIATIONS = {
    "Q17": (
        "PHP's is_all_zeroes() wants a time on every lane up to the lane count."
        " Django reads only the lanes the heat schedules, or in a timer test the"
        " lanes its mask leaves in (#65, Q17). On lanes 2 and 4, the only lanes"
        " in use, PHP writes zero times as heat 1's result and stages heat 2;"
        " Django stops racing on heat 1 and writes nothing. That is new for zero"
        " times with places (001, 002); without places Django already stopped"
        " on master, through D42's false-start rule (003, 004). In a timer test"
        " that leaves lanes 3 and 4 out, PHP stores zero times on lanes 1 and 2"
        " as the test's result; Django stops the test. That poll's current-heat"
        ' classid is "" in PHP and null in Django: C4, which Q2 fixes. After a'
        " timer-test result, PHP's advance_heat assigns in its condition"
        " (website/inc/autoadvance.inc:138), so it writes ClassID from round"
        ' -100, which has no row, and stores "". Django writes ClassID only on'
        " next-up or a change of round (Q2), and here never advances, since"
        " Q17 stops the test (test_classid_through_a_timer_test, #406).",
        "zeroes",
        {
            ZEROES: [
                "001 finished-zeroes-and-places-on-lanes-2-and-4",
                "002 poll-after-zeroes-and-places",
                "003 finished-zeroes-on-lanes-2-and-4",
                "004 poll-after-zeroes",
            ],
            TIMER_TEST: [
                "001 finished-zeroes-on-the-tested-lanes",
                "002 timer-test-after-zeroes",
            ],
        },
        None,
    ),
}
DEVIATION_CLAIM = listed_claim(DEVIATIONS)


def test_zeroes_on_the_lanes_in_use_give_the_q17_deviation(event):
    s = run_zeroes()
    assert s.mismatches() == []


def test_every_listed_deviation_is_a_reply_these_scenarios_make():
    assert_listed(DEVIATIONS, SCENARIOS)


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the Q17 entry"
)
def test_write_timer_deviations(event):
    write_deviations(DEVIATIONS, SCENARIOS)


# ----- current-heat's classid through a timer test (#406) ------------------------

CURRENT_HEAT_POLLS = (
    "poll.coordinator",
    "poll.timer.test",
    "poll&values=current-heat",
    "poll.results",
)


def current_heats(s: TimerScenario, step: str) -> list[tuple]:
    """`current-heat`'s classid, round, roundid and heat in each poll that
    carries it."""
    rows = []
    for query in CURRENT_HEAT_POLLS:
        heat = json.loads(s.client.get(f"/action.php?query={query}").content)[
            "current-heat"
        ]
        rows.append(
            (step, query, heat["classid"], heat["round"], heat["roundid"], heat["heat"])
        )
    return rows


def timer_test_case(s: TimerScenario):
    """Six Wolves checked in on 4 lanes, round 1 scheduled, and an
    identified timer."""
    s.reset()
    for i in range(1, 7):
        s.import_racer(f"firstname=F{i}&lastname=L{i}&carnumber=10{i}&partition=Wolves")
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")
    s.learn_rows()
    s.post_ok("action=schedule.generate&roundid=1")
    s._timer_client = Client(headers={"User-Agent": TIMER_UA})
    s.post_as(s._timer_client, "action=role.login&name=Timer&password=")
    s.post_as(s._timer_client, "action=timer-message&message=HELLO")
    s.post_as(s._timer_client, "action=timer-message&message=IDENTIFIED&lane_count=4")


def test_classid_through_a_timer_test(event):
    """Q2 fixes C4: a timer-test advance is `next` within round -100, so
    Django leaves ClassID as it was, where PHP's `advance_heat` writes ""
    (`website/inc/autoadvance.inc:138`) and keeps it after the test ends.
    Every poll that carries current-heat sends the classid from before the
    test: the round's class after a heat.select (A), and null with no heat
    ever selected (B). The test heat moves the heat on, so the advance ran."""
    s = TimerScenario()
    got = []

    timer_test_case(s)
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    got += current_heats(s, "A1 heat selected")
    s.post_ok("action=timer.test&tt-mask=0")
    got += current_heats(s, "A2 test started")
    s.timer("message=FINISHED&lane1=3.1&lane2=3.2&lane3=3.3&lane4=3.4")
    got += current_heats(s, "A3 after a test heat")
    s.post_ok("action=timer.test&test-mode=0")
    got += current_heats(s, "A4 test ended")
    s.post_ok("action=heat.select&heat=next")
    got += current_heats(s, "A5 next heat")
    wolves = Group.objects.get(event=active_event(), name="Wolves").pk
    round1 = Round.objects.get(event=active_event()).pk

    timer_test_case(s)
    got += current_heats(s, "B1 no heat selected")
    s.post_ok("action=timer.test&tt-mask=12")
    got += current_heats(s, "B2 test started")
    s.timer("message=FINISHED&lane1=3.1&lane2=3.2")
    got += current_heats(s, "B3 after a test heat")
    s.post_ok("action=timer.test&test-mode=0")
    got += current_heats(s, "B4 test ended")

    steps = [
        ("A1 heat selected", wolves, 1, round1, 1),
        ("A2 test started", wolves, 1, -100, 2),
        ("A3 after a test heat", wolves, 1, -100, 3),
        ("A4 test ended", wolves, 1, round1, 1),
        ("A5 next heat", wolves, 1, round1, 2),
        ("B1 no heat selected", None, None, -1, None),
        ("B2 test started", None, 1, -100, 1),
        ("B3 after a test heat", None, 1, -100, 2),
        ("B4 test ended", None, None, -1, None),
    ]
    assert got == [
        (step, query, classid, rnd, roundid, heat)
        for step, classid, rnd, roundid, heat in steps
        for query in CURRENT_HEAT_POLLS
    ]
