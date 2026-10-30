"""The owner's race day through `playlist.start` (#34, owner decision D32).

Scouts cars 101 to 108 pass check-in. The playlist races Scouts round 1,
then a top 4 final. Car 110 passes check-in while round 1 runs: a late
racer, with no heats until `schedule.reschedule` commits. Car 109 passes
check-in after round 1 and is allowed into the final. Car 101 is fastest
and each car is slower than the one before it.
"""

import json

import pytest

from legacy_api.tests.conftest import post
from legacy_api.tests.test_race_control_parity import Scenario, active_event
from racing.models import HeatLane, Round
from racing.services.finals import allow_final_request, request_final_place
from roster.models import Group, Racer

pytestmark = pytest.mark.django_db

ROUND_1 = range(101, 109)
LATE, ALLOWED = 110, 109


def _time(car: int) -> str:
    return f"{2 + (car - 100) / 100:.3f}"


class RaceDay(Scenario):
    """Requests name Django's own ids: nothing here is replayed from PHP."""

    def post_ok(self, body: str) -> dict:
        reply = json.loads(post(self.client, body).content)
        assert reply["outcome"]["summary"] == "success", (body, reply)
        return reply

    def racer(self, car: int) -> Racer:
        return Racer.objects.get(event=active_event(), car_number=car)

    def check_in(self, car: int):
        self.post_ok(f"action=racer.pass&racer={self.racer(car).pk}&value=1")

    def cars_in(self, round: Round) -> set[int]:
        return set(
            HeatLane.objects.filter(round=round).values_list(
                "racer__car_number", flat=True
            )
        )

    def race_current_round(self):
        """Race every heat of the round now racing, through the timer."""
        roundid = self.coordinator()["current-heat"]["roundid"]
        while True:
            poll = self.coordinator()
            heat = poll["current-heat"]
            if not heat["now_racing"] or heat["roundid"] != roundid:
                return
            lanes = "&".join(
                f"lane{r['lane']}={_time(r['carnumber'])}" for r in poll["racers"]
            )
            self.timer("message=STARTED")
            self.timer(f"message=FINISHED&{lanes}")


def test_a_late_racer_waits_for_reschedule_and_the_final_holds_the_allowed_racer(
    client, event
):
    day = RaceDay(client)
    day.reset()
    day.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")
    for car in (*ROUND_1, ALLOWED, LATE):
        day.post_ok(
            f"action=racer.import&firstname=Scout{car}&lastname=Racer"
            f"&partition=Scouts&carnumber={car}"
        )
    for car in ROUND_1:
        day.check_in(car)
    scouts = Group.objects.get(event=active_event(), name="Scouts")
    day.post_ok(f"action=playlist.add&classid={scouts.pk}&round=1&n_times_per_lane=1")
    day.post_ok(
        f"action=playlist.add&classid={scouts.pk}&round=2&top=4&n_times_per_lane=1"
    )

    day.post_ok("action=playlist.start")
    round_1 = Round.objects.get(group=scouts, number=1)
    assert day.cars_in(round_1) == set(ROUND_1)
    assert day.coordinator()["current-heat"]["now_racing"] is True

    day.check_in(LATE)
    assert LATE not in day.cars_in(round_1)
    day.post_ok(
        f"action=schedule.reschedule&roundid={round_1.pk}&racerid={day.racer(LATE).pk}"
    )
    assert LATE in day.cars_in(round_1)

    day.race_current_round()
    final = Round.objects.get(group=scouts, number=2)
    assert day.cars_in(final) == {101, 102, 103, 104}

    day.check_in(ALLOWED)
    allow_final_request(request_final_place(day.racer(ALLOWED)))
    roster = [e.racer.car_number for e in final.roster.order_by("id")]
    assert roster == [101, 102, 103, 104, ALLOWED]
    assert ALLOWED not in day.cars_in(final)
    day.post_ok(
        f"action=schedule.reschedule&roundid={final.pk}&racerid={day.racer(ALLOWED).pk}"
    )
    assert day.cars_in(final) == {101, 102, 103, 104, ALLOWED}
