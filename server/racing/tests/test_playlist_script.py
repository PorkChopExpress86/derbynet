"""`testing/test-playlist.sh` replayed through the heat, result and playlist
services. At each `poll.coordinator` and `poll.kiosk` the script makes, the
current heat, racing, the cars by lane, every round's counts, the last heat
and the kiosk's page equal what PHP showed (`http/suite/playlist-states.http`).
The pending Awards scene is checked against its 10 s delay.

PHP has no operator gate, so the steps here do not wait (D12 keeps PHP's
behavior for a step that does not wait).
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import RaceSettings
from kiosks.models import Kiosk, Scene
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from racing.models import HeatLane, PlaylistEntry, Round
from racing.services.heats import LastHeat, select_heat
from racing.services.playlist import apply_due_scene
from racing.services.results import Outcome, delete_results, record_timer_result
from racing.services.rounds import round_counts
from racing.services.schedule import generate_schedule
from racing.services.tests.race import current, fields, state
from roster.models import Group
from roster.tests.php_replay import Replay

pytestmark = pytest.mark.django_db

KIOSK = "FAKE-KIOSK1"


def page(event):
    return Kiosk.objects.get(event=event, address=KIOSK).page


class Script:
    def __init__(self):
        self.replay = Replay()
        self.event = self.replay.event
        self.seen = []
        for car, den in (
            (101, "Den1"),
            (102, "Den1"),
            (201, "Den2"),
            (202, "Den2"),
            (203, "Den2"),
            (301, "Den3"),
            (302, "Den3"),
        ):
            self.replay.post(
                f"action=racer.import&firstname=First{car}&lastname=Last{car}"
                f"&partition={den}&carnumber={car}"
            )
        den2, den3 = (
            self.replay.ids.of("class", self.group(d)) for d in ("Den2", "Den3")
        )
        self.replay.post(
            f"action=class.add&name=TwoThree&constituent_{den2}&constituent_{den3}"
        )
        self.replay.post("action=racer.bulk&who=all&what=checkin")
        Kiosk.objects.create(
            event=self.event, address=KIOSK, name="Main", page="kiosks/welcome.kiosk"
        )
        self.replay.post("action=settings.write&unused-lane-mask=0&n-lanes=2")
        RaceSettings.objects.filter(event=self.event).update(
            racing_scene=self.scene("Racing")
        )
        generate_schedule(self.round("Den1"), 1)
        awards = self.scene("Awards")
        for seq, (den, extra) in enumerate(
            (
                ("Den1", {"scene_at_finish": awards}),
                ("Den2", {"continue_racing": True}),
                ("Den3", {"continue_racing": True}),
                ("TwoThree", {"bucket_limit": 3, "n_times_per_lane": 2}),
            ),
            start=1,
        ):
            PlaylistEntry.objects.create(
                event=self.event,
                seq=seq,
                group=self.group(den),
                round_number=1,
                waits_for_operator=False,
                **extra,
            )

    def group(self, name):
        return Group.objects.get(event=self.event, name=name)

    def round(self, name):
        return Round.objects.get(event=self.event, group__name=name, number=1)

    def scene(self, name):
        return Scene.objects.get(event=self.event, name=name)

    def poll(self):
        """What `poll.coordinator` shows of the race: the current heat, its
        cars by lane, each round's counts, and the last heat."""
        self.replay.ids.sync()
        now = state(self.event)
        cars = list(
            HeatLane.objects.filter(
                round_id=now.current_round_id, heat=now.current_heat
            )
            .order_by("lane")
            .values_list("lane", "racer__car_number")
        )
        rounds = [
            (c.round.group.name, c.round.number, c.roster_size, c.heats_scheduled)
            + (c.heats_run,)
            for c in round_counts(self.event)
        ]
        round_id = self.replay.ids.of("round", now.current_round)
        self.seen.append(
            (round_id, now.current_heat, now.racing, cars, rounds)
            + (LastHeat.kind(now.last_heat),)
        )

    def kiosk(self):
        self.seen.append(page(self.event))

    def run_heat(self, den, heat, *lanes, expect=Outcome.RECORDED):
        """`run_heat` of `testing/common.sh`: racing on the heat, the named
        car in each lane, then the timer's `FINISHED`."""
        self.poll()
        round = self.round(den)
        assert current(self.event) == (round.id, heat, True), (den, heat)
        cars = dict(
            HeatLane.objects.filter(round=round, heat=heat).values_list(
                "lane", "racer__car_number"
            )
        )
        posted = []
        for lane, entry in enumerate(lanes, start=1):
            car, time = entry.split(":")
            assert cars[lane] == int(car), (den, heat, lane)
            posted.append(f"lane{lane}={time}")
        recorded = record_timer_result(self.event, fields("&".join(posted)))
        assert recorded.outcome is expect, (den, heat)


def test_the_playlist_script():
    script = Script()
    event = script.event
    den1 = script.round("Den1")

    select_heat(event, round_id=den1.id, now_racing=True)
    script.kiosk()

    script.run_heat("Den1", 1, "101:0.00", "102:0.00", expect=Outcome.ALL_ZERO)
    script.poll()
    assert current(event) == (den1.id, 1, False), "an all-zero heat stops racing"
    select_heat(event, round_id=den1.id, now_racing=True)

    finished = timezone.now()
    script.run_heat("Den1", 1, "101:1.00", "102:2.00")
    script.run_heat("Den1", 2, "102:1.00", "101:2.00")

    den2 = script.round("Den2")
    assert current(event) == (den2.id, 1, False)
    due = state(event).playlist_scene_at
    assert state(event).playlist_scene_id == script.scene("Awards").id
    assert finished + timedelta(seconds=10) <= due
    assert due <= timezone.now() + timedelta(seconds=10)
    assert not apply_due_scene(event, due)
    assert apply_due_scene(event, due + timedelta(seconds=1))
    script.kiosk()
    script.poll()

    Kiosk.objects.filter(address=KIOSK).update(page="kiosks/flag.kiosk")
    select_heat(event, now_racing=True)
    script.kiosk()

    script.run_heat("Den2", 1, "201:1.00", "203:1.20")
    script.run_heat("Den2", 2, "202:1.00", "201:1.30")
    script.run_heat("Den2", 3, "203:1.00", "202:1.40")
    script.kiosk()
    script.poll()
    den3 = script.round("Den3")
    assert current(event) == (den3.id, 1, True)

    script.run_heat("Den3", 1, "301:1.00", "302:1.20")
    script.run_heat("Den3", 2, "302:1.00", "301:1.40")
    script.kiosk()
    script.poll()
    two_three = script.round("TwoThree")
    assert current(event) == (two_three.id, 1, True)

    script.run_heat("TwoThree", 1, "203:1.00", "201:1.20")
    script.run_heat("TwoThree", 2, "302:1.00", "203:1.20")
    script.run_heat("TwoThree", 3, "201:1.00", "302:1.20")

    delete_results(event, den2, 2)
    delete_results(event, two_three, 3)
    select_heat(event, round_id=den2.id, heat=2, now_racing=True)
    script.run_heat("Den2", 2, "202:1.01", "201:1.30")
    script.poll()
    assert current(event) == (two_three.id, 3, True)
    assert script.seen == php_states()


def php_states():
    """The same reads on PHP, from `http/suite/playlist-states.http`. The
    first two kiosk polls, before the kiosk is assigned, are left out."""
    states = []
    exchanges = read_transcript(GOLDEN_DIR / "http/suite/playlist-states.http")
    for exchange in list(exchanges.values())[2:]:
        body = json.loads(exchange["body"])
        if "kiosk-setting" in body:
            states.append(body["kiosk-setting"]["page"])
            continue
        heat = body["current-heat"]
        cars = [(r["lane"], r["carnumber"]) for r in body["racers"]]
        rounds = [
            (r["class"], r["round"], r["roster_size"], r["heats_scheduled"])
            + (r["heats_run"],)
            for r in body["rounds"]
        ]
        states.append(
            (heat["roundid"], heat["heat"], heat["now_racing"], cars, rounds)
            + (body["last-heat"],)
        )
    return states
