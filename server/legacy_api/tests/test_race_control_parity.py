"""The race-control endpoints and the racing polls against the PHP
captures (#31), with ids normalized as `php_http` describes.

Each scenario runs the steps of its `capture/scenarios/*.sh` in order:
the recorded ones are replayed and compared, the unrecorded ones are posted
through `/action.php` as the scenario posts them. The capture's timer, which
#32 serves, is driven through `racing.services.results` instead. The
exchanges `deviations.json` lists must match their expected Django output
instead, and must differ from PHP's (D25).

`WRITE_DEVIATIONS=1 uv run pytest legacy_api/tests/test_race_control_parity.py`
rewrites the #31 entries and their expected transcripts.
"""

import json
import os
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from django.test import Client
from django.utils import timezone

from core.models import RaceState, SiteState
from legacy_api.tests.php_http import Ids, as_php_body, send
from racing.engine.tests.golden import (
    DEVIATIONS_FILE,
    GOLDEN_DIR,
    deviations,
    read_transcript,
    transcript_deviations,
)
from racing.models import PlaylistEntry, Round, TimerStatus
from racing.services import playlist, results, rounds
from roster.models import Group, Subgroup

pytestmark = pytest.mark.django_db

TESTING = Path(__file__).resolve().parents[3] / "testing"


def _csv_rows(name):
    rows = []
    for line in (TESTING / "data" / name).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        first, last, car, part = (f.replace('"', "") for f in line.split(",", 3))
        rows.append(
            f"firstname={first}&lastname={last}&carnumber={car}&partition={part}"
        )
    return rows


def _race_time(racer: dict, salt: int) -> str:
    """`race_round`'s time for a racer, as `jq` prints it."""
    x = (2000 + (racer["carnumber"] * 37 + racer["lane"] * 11 + salt) % 1000) / 1000
    return str(int(x)) if x == int(x) else repr(x)


def active_event():
    return SiteState.objects.get(pk=1).active_event


class Scenario:
    """One capture scenario, replayed against one Django event."""

    def __init__(self, client: Client):
        self.client = client
        self.ids = Ids()
        self.php_racers = 0
        self.transcripts: dict[str, dict] = {}
        self.got: dict[str, dict[str, bytes]] = {}
        self.fixture = None
        self.names: list[str] = []
        self.status: dict[tuple[str, str], int] = {}
        self.skipped: set[tuple[str, str]] = set()

    # ----- unrecorded steps -----

    def post_ok(self, body: str) -> dict:
        response = send(self.client, "POST action.php", body, self.ids)
        reply = json.loads(response.content)
        assert reply["outcome"]["summary"] == "success", (body, reply)
        return reply

    def post(self, body: str) -> dict:
        return json.loads(send(self.client, "POST action.php", body, self.ids).content)

    def login(self):
        self.post_ok("action=role.login&name=RaceCoordinator&password=doyourbest")

    def reset(self):
        """`reset_database`: an empty database, as PHP's ids start at 1."""
        self.login()
        self.post_ok("action=database.execute&script=schema")
        self.ids = Ids()
        self.php_racers = 0

    def import_racer(self, row: str):
        reply = self.post_ok(f"action=racer.import&{row}")
        self.php_racers += 1
        self.ids.to_php["racer"].setdefault(reply["racerid"], self.php_racers)

    def import_csv(self, name: str):
        for row in _csv_rows(name):
            self.import_racer(row)

    def learn_rows(self):
        """Rows made by unrecorded steps, numbered as an empty PHP database
        numbers them: in the order they were made."""
        event = active_event()
        for kind, model in (("round", Round), ("class", Group), ("rank", Subgroup)):
            known = self.ids.to_php[kind]
            for n, pk in enumerate(
                model.objects.filter(event=event)
                .order_by("pk")
                .values_list("pk", flat=True),
                start=1,
            ):
                known.setdefault(pk, n)

    def timer(self, body: str):
        """A timer message, which #32 serves: a FINISHED is recorded through
        the service its handler calls, and the timer's state is set as PHP
        leaves it, so the polls report a connected timer."""
        fields = dict(parse_qsl(body))
        event = active_event()
        message = fields.get("message")
        if message == "FINISHED":
            results.record_timer_result(event, parse_qsl(body))
        state = TimerStatus.State.CONNECTED
        if message == "STARTED":
            state = TimerStatus.State.RUNNING
        elif message in ("HEARTBEAT", "FINISHED"):
            if RaceState.objects.get(event=event).racing:
                state = TimerStatus.State.STAGING
        TimerStatus.objects.filter(event=event).update(
            state=state, last_contact=timezone.now()
        )

    def coordinator(self) -> dict:
        response = self.client.get("/action.php", {"query": "poll.coordinator"})
        return json.loads(response.content)

    def race_round(self, salt=0, max_heats=None) -> int:
        """`race_round` of `capture-http.sh`: race every heat of the round
        now racing, a car's time a function of its car number and lane."""
        raced = 0
        roundid = self.coordinator()["current-heat"]["roundid"]
        while max_heats is None or raced < max_heats:
            poll = self.coordinator()
            heat = poll["current-heat"]
            if not heat["now_racing"] or heat["roundid"] != roundid:
                break
            lanes = "&".join(
                f"lane{r['lane']}=" + _race_time(r, salt) for r in poll["racers"]
            )
            self.timer("message=STARTED")
            self.timer(f"message=FINISHED&{lanes}")
            raced += 1
        return raced

    def skip(self, label: str):
        """A recorded exchange another ticket serves, left out."""
        name = self.names.pop(0)
        assert name.split(" ", 1)[1] == label, (self.fixture, name, label)
        self.skipped.add((self.fixture, name))

    def skip_rest(self):
        while self.names:
            self.skipped.add((self.fixture, self.names.pop(0)))

    def timer_capture(self, label: str, body: str):
        """A recorded timer message: sent through the stand-in, and left out
        of the comparison (#32)."""
        name = self.names.pop(0)
        assert name.split(" ", 1)[1] == label, (self.fixture, name, label)
        self.skipped.add((self.fixture, name))
        self.timer(body)

    # ----- recorded steps -----

    def scenario(self, fixture: str):
        self.fixture = fixture
        if fixture not in self.transcripts:
            self.transcripts[fixture] = read_transcript(GOLDEN_DIR / fixture)
            self.got[fixture] = {}
            self.names = list(self.transcripts[fixture])

    def capture(self, label: str, client=None):
        name = self.names.pop(0)
        assert name.split(" ", 1)[1] == label, (self.fixture, name, label)
        exchange = self.transcripts[self.fixture][name]
        response = send(
            client or self.client,
            exchange["request"],
            exchange.get("request-body"),
            self.ids,
        )
        self.status[(self.fixture, name)] = response.status_code
        self.got[self.fixture][name] = as_php_body(response, exchange["body"], self.ids)

    def mismatches(self) -> list[tuple[str, str]]:
        """The exchanges that neither match PHP nor, when a deviation lists
        them, match its expected output and differ from PHP's."""
        found = []
        for fixture, got in self.got.items():
            expected = transcript_deviations(fixture)
            php = self.transcripts[fixture]
            skipped = {n for f, n in self.skipped if f == fixture}
            assert set(got) | skipped == set(php), fixture
            for name, body in got.items():
                status = self.status[(fixture, name)]
                if name in expected:
                    ok = _matches_deviation(body, status, expected[name][1])
                    ok = ok and body != php[name]["body"]
                else:
                    ok = body == php[name]["body"] and status == int(
                        php[name]["status"]
                    )
                if not ok:
                    found.append((fixture, name))
        return found


def _matches_deviation(body: bytes, status: int, entry: dict) -> bool:
    """An expected exchange holds Django's whole reply, or, as
    `tools/reschedule_deviations.py` writes them, only what the reschedule
    service computes: the chart and `timing.internal`, or `failure`."""
    want = json.loads(entry["body"])
    if "outcome" in want or "status" in entry:
        # Some entries were written without the final newline.
        return body.rstrip(b"\n") == entry["body"].rstrip(b"\n") and status == int(
            entry.get("status", 200)
        )
    got = json.loads(body)
    if "failure" in want:
        return got["outcome"]["code"] == want["failure"]
    return got.get("chart") == want["chart"] and (
        got["timing"]["internal"] == want["timing"]["internal"]
    )


# ----- capture/scenarios/04-schedules.sh -----------------------------------------


def run_schedules(client) -> Scenario:
    s = Scenario(client)
    s.reset()
    s.import_csv("divided-roster.csv")
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.learn_rows()

    def unschedule_all():
        for r in (1, 2, 3):
            s.post_ok(f"action=schedule.unschedule&roundid={r}")

    def each_round(extra=""):
        for r in (1, 2, 3):
            s.capture(f"generate-round-{r}")
            s.post(f"action=heat.select&roundid={r}&now_racing=0")
            s.capture(f"schedule-round-{r}")

    def variant(name):
        s.scenario(f"http/schedule/{name}.http")
        s.capture("racers")

    variant("normal-4-lanes")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")
    each_round()
    unschedule_all()

    variant("two-runs-per-lane-4-lanes")
    each_round()
    unschedule_all()

    variant("cant-fulfill-4-lanes")
    s.capture("generate-round-3-too-many-runs")
    s.capture("select-round-3-with-no-heats")
    s.capture("schedule-round-3-after-cant-fulfill")
    s.capture("generate-round-3-at-the-limit")
    s.capture("generate-round-99-no-such-round")
    unschedule_all()

    variant("cant-fulfill-over-a-chart-4-lanes")
    s.capture("generate-round-3-at-the-limit")
    s.capture("select-round-3")
    s.capture("schedule-round-3-at-the-limit")
    s.capture("generate-round-3-too-many-runs")
    s.capture("schedule-round-3-after-too-many-runs")
    unschedule_all()

    variant("zero-runs-over-a-chart-4-lanes")
    s.capture("generate-round-3")
    s.capture("select-round-3")
    s.capture("schedule-round-3")
    s.capture("generate-round-3-zero-runs")
    s.capture("schedule-round-3-after-zero-runs")
    unschedule_all()

    variant("rotation-schedule-4-lanes")
    s.post_ok("action=settings.write&rotation-schedule=1")
    each_round()
    unschedule_all()
    s.post_ok("action=settings.write&rotation-schedule=0")

    variant("max-runs-per-car-1-4-lanes")
    s.post_ok("action=settings.write&max-runs-per-car=1")
    each_round()
    unschedule_all()
    s.post_ok("action=settings.write&max-runs-per-car=0")

    variant("unused-lane-mask-33-6-lanes")
    s.post_ok("action=settings.write&unused-lane-mask=33&n-lanes=6")
    each_round()
    unschedule_all()

    variant("two-lanes")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=2")
    each_round()
    unschedule_all()

    variant("seven-lanes")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=7")
    each_round()
    unschedule_all()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")

    s.scenario("http/schedule/more-than-200-cars.http")
    s.reset()
    s.import_csv("extended.csv")
    s.import_racer(
        "firstname=F-1201&lastname=L-1201&carnumber=1201&partition=TheTwoHundred"
    )
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")
    s.learn_rows()
    s.capture("generate-with-201-cars")
    s.capture("rounds-after-201-cars")
    s.post_ok("action=racer.delete&racer=201")
    s.capture("generate-with-200-cars")
    s.capture("rounds-after-200-cars")
    return s


def _dump(s):
    out = os.environ.get("PARITY_DUMP")
    if not out:
        return
    import difflib

    lines = []
    for fixture, name in s.mismatches():
        php = s.transcripts[fixture][name]["body"].decode().splitlines()
        got = s.got[fixture][name].decode().splitlines()
        lines.append(f"===== {fixture} {name}")
        lines += list(difflib.unified_diff(php, got, "php", "django", n=2, lineterm=""))
    with open(out, "a") as f:
        f.write("\n".join(lines) + "\n")


def test_the_schedule_scenario_matches_php(client, event):
    s = run_schedules(client)
    _dump(s)
    assert s.mismatches() == []


# ----- capture/scenarios/05-master-schedule.sh -----------------------------------


def run_master_schedule(client) -> Scenario:
    s = Scenario(client)
    s.reset()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=2")
    s.post_ok("action=settings.write&use-master-sched-checkbox=1&use-master-sched=1")
    for name, count, first in (
        ("Two", 2, 100),
        ("Three", 3, 200),
        ("Five", 5, 300),
        ("Nine", 9, 400),
    ):
        for i in range(1, count + 1):
            s.import_racer(
                f"firstname={name}{i}&lastname=Racer&carnumber={first + i}"
                f"&partition={name}"
            )
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.learn_rows()
    timer = Client()

    s.scenario("http/master-schedule/half-weights.http")
    s.capture("racers")
    s.capture("generate-round-1-two-racers")
    s.capture("generate-round-2-three-racers")
    s.capture("generate-round-3-five-racers")
    s.capture("generate-round-4-nine-racers")
    s.capture("rounds")
    s.capture("ondeck-in-master-order")
    s.capture("login-timer", timer)
    s.post_ok("action=heat.select&heat=next-up&now_racing=1")
    s.timer_capture("heartbeat-first-master-heat", "message=HEARTBEAT")
    s.timer_capture(
        "finished-first-master-heat", "message=FINISHED&lane1=3.100&lane2=3.200"
    )
    s.capture("ondeck-after-one-heat")
    s.capture("regenerate-round-4-after-a-heat-ran")
    s.capture("ondeck-after-regenerating")
    s.post_ok("action=heat.select&roundid=4&heat=9&now_racing=1")
    s.timer("message=STARTED")
    s.timer_capture(
        "finished-last-master-heat-out-of-order",
        "message=FINISHED&lane1=3.300&lane2=3.400",
    )
    s.capture("coordinator-before-results-exist")
    s.capture("ondeck-before-results-exist")
    s.capture("generate-round-1-results-exist")
    s.capture("coordinator-after-results-exist")
    s.capture("ondeck-after-results-exist")
    return s


def test_the_master_schedule_scenario_matches_php(client, event):
    s = run_master_schedule(client)
    _dump(s)
    assert s.mismatches() == []


# ----- capture/scenarios/11-reschedule-quirks.sh ---------------------------------


def _reschedule_case(s: Scenario, racers: int, partition: str, mask=0):
    s.reset()
    s.post_ok(f"action=settings.write&unused-lane-mask={mask}&n-lanes=4")
    for i in range(1, racers + 1):
        s.import_racer(
            f"firstname={partition}{i}&lastname=Racer&carnumber={100 + i}"
            f"&partition={partition}"
        )


def run_reschedule_quirks(client) -> Scenario:
    s = Scenario(client)
    # Q5: a dry run that squeezes out a heat of the round being raced.
    _reschedule_case(s, 11, "Racers")
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.learn_rows()
    s.post_ok("action=schedule.generate&roundid=1")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    s.race_round(max_heats=1)
    s.post_ok("action=racer.pass&racer=10&value=0")
    s.scenario("http/reschedule/dry-run-while-racing.http")
    s.capture("racing-before")
    s.capture("dry-run-squeezes-a-heat")
    s.capture("racing-after")

    # Q6: under the master schedule, a late racer joins the running round.
    _reschedule_case(s, 6, "Racers")
    s.post_ok("action=settings.write&use-master-sched-checkbox=1&use-master-sched=1")
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.learn_rows()
    s.post_ok("action=racer.pass&racer=6&value=0")
    s.post_ok("action=schedule.generate&roundid=1")
    s.post_ok("action=heat.select&heat=next-up&now_racing=1")
    s.race_round(max_heats=2)
    s.post_ok("action=racer.pass&racer=6&value=1")
    s.scenario("http/reschedule/master-schedule.http")
    s.capture("racing-before")
    s.capture("late-racer-in-the-running-round")
    s.capture("racing-after")

    # Q21: a search that runs out of added heats.
    _reschedule_case(s, 2, "Lions", mask=2)
    s.import_racer("firstname=Tiger1&lastname=Racer&carnumber=201&partition=Tigers")
    s.import_racer("firstname=Tiger2&lastname=Racer&carnumber=202&partition=Tigers")
    s.import_racer("firstname=Lions3&lastname=Racer&carnumber=103&partition=Lions")
    for racer in (1, 2, 3, 4):
        s.post_ok(f"action=racer.pass&racer={racer}&value=1")
    s.learn_rows()
    s.post_ok("action=schedule.generate&roundid=1&n_times_per_lane=2")
    s.post_ok("action=schedule.generate&roundid=2")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    s.race_round(max_heats=3)
    s.post_ok("action=heat.select&roundid=2&heat=1")
    s.scenario("http/reschedule/last-added-heat.http")
    s.capture("rounds-before")
    s.capture("dry-run-runs-out-of-added-heats")
    return s


def test_the_reschedule_quirks_match_php(client, event):
    s = run_reschedule_quirks(client)
    _dump(s)
    assert s.mismatches() == []


# ----- capture/scenarios/06-race-day.sh, as far as the polls go ------------------

ALL_VALUES = (
    "current-heat,racers,rounds,classes,partitions,ondeck,heat-results,"
    "timer-state,replay-state,last-heat,schedule,race-structure,precision,"
    "best-times,car-numbers,current-reschedule,timer-trouble"
)


def run_race_day_polls(client) -> Scenario:
    """The poll payloads up to the first result. The playlist steps (#34)
    are made through the playlist service; the kiosk polls (#34) are left
    out."""
    s = Scenario(client)
    s.reset()
    # The schema script keeps the lane count, which 05-master-schedule.sh
    # left at 2.
    s.post_ok("action=settings.write&n-lanes=2")
    s.scenario("http/poll/race-day-polls.http")

    def polls(label):
        s.capture(f"{label} poll.coordinator")
        s.skip(f"{label} poll.kiosk")
        s.capture(f"{label} poll")

    polls("01-empty-database")
    s.skip("01-empty-database poll.kiosk.all")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")
    for i in range(1, 11):
        s.import_racer(
            f"firstname=Scout{i}&lastname=Racer&carnumber={100 + i}&partition=Scouts"
        )
    s.import_csv("outlaw-class.csv")
    s.learn_rows()
    polls("02-roster-imported-nobody-checked-in")
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.capture("03-checked-in racers")
    polls("03-checked-in")

    event = active_event()
    scouts, outlaws = (
        Group.objects.get(event=event, name=name) for name in ("Scouts", "Outlaw")
    )
    for seq, (group, number, top, runs) in enumerate(
        ((scouts, 1, 0, 1), (scouts, 2, 4, 2), (outlaws, 1, 0, 1), (outlaws, 2, 8, 2)),
        start=1,
    ):
        PlaylistEntry.objects.create(
            event=event,
            seq=seq,
            group=group,
            round_number=number,
            bucket_limit=top,
            n_times_per_lane=runs,
        )
    playlist.start_playlist(event, rounds.playlist_finalists())
    polls("04-scheduled-and-racing-heat-1")
    s.skip("timer-hello")
    s.timer("message=HELLO")
    s.skip("timer-identified")
    s.timer("message=IDENTIFIED")
    s.skip("timer-heartbeat-staging")
    s.timer("message=HEARTBEAT")
    s.skip("timer-started")
    s.timer("message=STARTED")
    polls("05-mid-heat")
    s.timer_capture(
        "timer-finished",
        "message=FINISHED&lane1=2.411&lane2=2.522&lane3=2.633&lane4=2.744",
    )
    polls("06-after-a-result")
    s.capture("06-after-a-result poll heat-results")
    return s


def test_the_race_day_polls_match_php(client, event):
    s = run_race_day_polls(client)
    s.skip_rest()  # the rest of the day needs #32, #33 and #34
    _dump(s)
    assert s.mismatches() == []


# ----- capture/scenarios/16-poll-results.sh ---------------------------------------


def run_poll_results(client) -> Scenario:
    """`query=poll.results` before racing, with the current round unraced,
    after two heats (and with `details`), after the next round starts, for a
    named round, and under points."""
    s = Scenario(client)
    s.reset()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")
    for i in range(1, 6):
        s.import_racer(
            f"firstname=Lion{i}&lastname=Racer&carnumber={100 + i}&partition=Lions"
        )
        s.import_racer(
            f"firstname=Tiger{i}&lastname=Racer&carnumber={200 + i}&partition=Tigers"
        )
    s.learn_rows()
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.post_ok("action=schedule.generate&roundid=1")
    s.post_ok("action=schedule.generate&roundid=2")

    s.scenario("http/poll/poll-results.http")
    s.capture("before-racing")
    s.post_ok("action=heat.select&roundid=1&now_racing=1")
    s.capture("current-round-not-raced")
    s.race_round(max_heats=2)
    s.capture("after-two-heats")
    s.capture("after-two-heats-details")
    s.race_round()
    s.post_ok("action=heat.select&roundid=2&now_racing=1")
    s.capture("next-round-keeps-recent-results")
    s.capture("named-round")
    s.race_round(salt=7, max_heats=1)
    s.post_ok("action=settings.write&use-points-checkbox=1&use-points=1")
    s.capture("under-points")
    s.post_ok("action=settings.write&use-points-checkbox=1")
    return s


def test_the_poll_results_scenario_matches_php(client, event):
    s = run_poll_results(client)
    _dump(s)
    assert s.mismatches() == []


# ----- the deviation entries -----------------------------------------------------

SCHEDULE = "http/schedule/{}.http"
SCENARIOS = {
    "schedules": run_schedules,
    "master-schedule": run_master_schedule,
    "reschedule-quirks": run_reschedule_quirks,
    "race-day-polls": run_race_day_polls,
    "poll-results": run_poll_results,
}

# quirk: (reason, scenario, {fixture: [exchange names]}, expected file name)
DEVIATIONS = {
    "R3": (
        "With no current heat after a round stops being current, PHP reports"
        " classid -1 and heat -1, the values set_current_heat(-1, -1) wrote;"
        " Django reports null for both, as before any heat was selected"
        " (#31, for the owner to confirm).",
        "schedules",
        {
            SCHEDULE.format("two-runs-per-lane-4-lanes"): ["002 generate-round-1"],
            SCHEDULE.format("cant-fulfill-4-lanes"): [
                "003 select-round-3-with-no-heats",
                "005 generate-round-3-at-the-limit",
            ],
            SCHEDULE.format("cant-fulfill-over-a-chart-4-lanes"): [
                "002 generate-round-3-at-the-limit"
            ],
            SCHEDULE.format("zero-runs-over-a-chart-4-lanes"): ["002 generate-round-3"],
            SCHEDULE.format("max-runs-per-car-1-4-lanes"): ["002 generate-round-1"],
            SCHEDULE.format("unused-lane-mask-33-6-lanes"): ["002 generate-round-1"],
            SCHEDULE.format("two-lanes"): ["002 generate-round-1"],
        },
        None,
    ),
    "R6": (
        "PHP's schedule.generate under rotation-schedule adds its scheduler's"
        " debug output, nheats, ncars and heats, schedule.generate with trace"
        " adds a `trace` key, and schedule.reschedule with trace adds the"
        " adjuster's trace; Django adds none of them. These replies"
        " also show R3's null classid and heat (#31, for the owner to"
        " confirm).",
        "schedules",
        {
            SCHEDULE.format("rotation-schedule-4-lanes"): [
                "002 generate-round-1",
                "004 generate-round-2",
                "006 generate-round-3",
            ]
        },
        None,
    ),
    "R7": (
        "PHP's schedule.generate appends the coordinator payload before it"
        " numbers the master heats and, under the master schedule, moves to"
        " the next-up heat. Django numbers and moves in the same transaction"
        " as the chart, so its payload shows the state the request left"
        " (#31, for the owner to confirm).",
        "master-schedule",
        {
            "http/master-schedule/half-weights.http": [
                "002 generate-round-1-two-racers",
                "003 generate-round-2-three-racers",
                "004 generate-round-3-five-racers",
                "005 generate-round-4-nine-racers",
                "012 regenerate-round-4-after-a-heat-ran",
            ]
        },
        None,
    ),
    "Q5": (
        "PHP turns racing off on a dry run that would squeeze out a heat;"
        " Django leaves racing on (Q5). PHP's reply also carries the"
        " adjuster's trace, which Django omits (R6).",
        "reschedule-quirks",
        {
            "http/reschedule/dry-run-while-racing.http": [
                "002 dry-run-squeezes-a-heat",
                "003 racing-after",
            ]
        },
        None,
    ),
    "Q19": (
        "A playlist round is scheduled through generate_schedule, so its heats"
        " get master heat numbers (D30); PHP's playlist leaves them at 0.",
        "race-day-polls",
        {
            "http/poll/race-day-polls.http": [
                "014 04-scheduled-and-racing-heat-1 poll",
                "021 05-mid-heat poll",
                "025 06-after-a-result poll",
            ]
        },
        None,
    ),
    "D34": (
        "The poll after a late racer joins under the master schedule shows"
        " the chart Django made, which keeps the running and staged heats"
        " (D34, Q20 on the reschedule before it); PHP's chart changed them.",
        "reschedule-quirks",
        {"http/reschedule/master-schedule.http": ["003 racing-after"]},
        None,
    ),
    "R8": (
        "PHP's query.poll.results.inc writes a space before its JSON; Django's"
        " body starts at `{` (#31, for the owner to confirm).",
        "poll-results",
        {
            "http/poll/poll-results.http": [
                "001 before-racing",
                "002 current-round-not-raced",
                "003 after-two-heats",
                "005 next-round-keeps-recent-results",
                "006 named-round",
                "007 under-points",
            ]
        },
        None,
    ),
    "R9": (
        "With details, PHP's poll.results rows repeat every column under its"
        ' position, "0" to "7", as PDO\'s default fetch mode returns them;'
        " Django gives each column once, by name. The reply also shows R8's"
        " missing leading space (#31, for the owner to confirm).",
        "poll-results",
        {"http/poll/poll-results.http": ["004 after-two-heats-details"]},
        None,
    ),
}


def _exchange_text(name, php, body: bytes) -> bytes:
    lines = [f"### {name}"]
    for key in ("request", "request-body", "status", "content-type", "cache-control"):
        if key in php:
            lines.append(f"{key}: {php[key]}")
    lines += [f"length: {len(body)}", "body:"]
    return "\n".join(lines).encode() + b"\n" + body + b"\n"


def test_every_listed_deviation_is_a_reply_these_scenarios_make():
    for quirk, (_, scenario, fixtures, _) in DEVIATIONS.items():
        assert scenario in SCENARIOS, quirk
        listed = {
            (e["fixture"], tuple(e["exchanges"]))
            for e in deviations()
            if e["quirk"] == quirk
        }
        for fixture, names in fixtures.items():
            assert (fixture, tuple(names)) in listed, (quirk, fixture)


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the #31 entries"
)
def test_write_race_control_deviations(event):
    runs = {name: run(Client()) for name, run in SCENARIOS.items()}
    written = []
    for quirk, (reason, scenario, fixtures, filename) in DEVIATIONS.items():
        run = runs[scenario]
        for fixture, names in fixtures.items():
            php = run.transcripts[fixture]
            expected = f"deviations/{quirk}/{filename or Path(fixture).name}"
            path = GOLDEN_DIR / expected
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                b"".join(_exchange_text(n, php[n], run.got[fixture][n]) for n in names)
            )
            written.append(
                {
                    "quirk": quirk,
                    "fixture": fixture,
                    "exchanges": names,
                    "reason": reason,
                    "expected": expected,
                }
            )
    paths = {e["expected"] for e in written}
    listing = json.loads(DEVIATIONS_FILE.read_text())
    kept = [e for e in listing["deviations"] if e["expected"] not in paths]
    # By quirk number, as `tools/reschedule_deviations.py --check` expects.
    listing["deviations"] = sorted(kept + written, key=lambda e: int(e["quirk"][1:]))
    DEVIATIONS_FILE.write_text(json.dumps(listing, indent=2) + "\n")
