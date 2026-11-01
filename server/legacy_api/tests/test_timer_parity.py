"""The timer protocol against the PHP captures (#32): `capture/scenarios/
03-timer.sh` replayed step by step, every recorded reply compared byte for
byte with Django's ids written as PHP's.

The scenario's transcripts share one race, so they run in order in one test.
"""

from urllib.parse import parse_qsl, quote, urlencode

import pytest
from django.test import Client

from legacy_api.tests.php_http import as_php_body, translate
from legacy_api.tests.test_race_control_parity import Scenario

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
