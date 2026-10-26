"""The finalists of every `roster.new` and playlist step with a `top` in
the shell suite's round scripts, chosen from the standings by
`new_round_roster` and `playlist_finalists`, against PHP (#26).

`http/suite/<script>.http` records each `roster.new` reply: the finalists,
bucket by bucket and best first, with their scores, and the concerns. The
racing between the calls comes from the export each script leaves, as in
`test_round_scenarios.py`, entered before the call that reads it.
"""

import json
from urllib.parse import parse_qsl

import pytest

from racing.models import HeatLane, PlaylistEntry, Round
from racing.services.playlist import round_ended
from racing.services.rounds import playlist_finalists
from racing.tests.test_round_scenarios import (
    AGGREGATE_ROUNDS_RACED_AFTER,
    Export,
    Rounds,
    aggregate_classes,
    aggregate_rounds,
    basic_racing,
    master_schedule,
    suite,
)

pytestmark = pytest.mark.django_db


def replay_script(replay, fixture, raced_after=()):
    for name, exchange in suite(fixture).items():
        recorded = json.loads(exchange["body"])
        code, roster = replay.roster_change(exchange["request-body"])
        assert code == recorded["outcome"]["code"], name
        if roster is not None:
            finalists = [
                {"racerid": replay.ids.php["racer"][f.racer_id], "score": f.score}
                for f in roster.qualifiers
            ]
            assert finalists == recorded["finalists"], name
            assert list(roster.concerns) == recorded.get("concerns", []), name
        if name in raced_after:
            replay.race(replay.first_round(*raced_after[name]))


def test_new_rounds_on_the_basic_racing_database():
    replay_script(basic_racing(), "new-rounds-basic")


def test_master_schedule_then_new_rounds():
    """`test-master-schedule.sh`, `test-awards.sh master` and
    `test-new-rounds.sh`, which the suite runs one after another on one
    database. The award changes come after the master schedule's final, so
    its cut still counts Carroll Cybulski, whom they exclude."""
    replay = master_schedule()
    replay_script(replay, "master-schedule")
    replay.award_setting_changes()
    assert replay.passed() == replay.export.roster
    replay_script(replay, "new-rounds-master")


def test_aggregate_rounds():
    replay_script(aggregate_rounds(), "aggregate-rounds", AGGREGATE_ROUNDS_RACED_AFTER)


def test_aggregate_classes():
    replay_script(aggregate_classes(), "aggregate-classes")


def test_playlist_final_of_two_groups():
    """`test-playlist.sh`: when Den3's round ends, the playlist's fourth
    step enters the top 3 of Den2 and Den3 in TwoThree. PHP's reply to
    that step is a timer message, which the fixture does not record, so
    the round's roster and chart are compared with the heats the Results
    sheet lists for it."""
    replay = Rounds(Export("playlist"))
    for car, den in [(101, 1), (102, 1), (201, 2), (202, 2), (203, 2), (301, 3)]:
        replay.post(
            f"action=racer.import&firstname=First{car}&lastname=Last{car}"
            f"&partition=Den{den}&carnumber={car}"
        )
    replay.post(
        "action=racer.import&firstname=First302&lastname=Last302"
        "&partition=Den3&carnumber=302"
    )
    den2, den3 = (replay.php("class", replay.group(n)) for n in ("Den2", "Den3"))
    replay.post(f"action=class.add&name=TwoThree&constituent_{den2}&constituent_{den3}")
    replay.post("action=racer.bulk&who=all&what=checkin")
    replay.post("action=settings.write&unused-lane-mask=0&n-lanes=2")
    for seq, exchange in enumerate(suite("playlist").values(), 1):
        values = dict(parse_qsl(exchange["request-body"]))
        PlaylistEntry.objects.create(
            event=replay.event,
            seq=seq,
            group=replay.get("class", values["classid"]),
            round_number=int(values["round"]),
            bucket_limit=int(values.get("top", 0)),
            bucketed=values.get("bucketed", "") not in ("", "0"),
            n_times_per_lane=max(0, int(values.get("n_times_per_lane", -1))),
            continue_racing=values.get("continue_racing", "") not in ("", "0"),
            waits_for_operator=False,
        )

    for den in ("Den1", "Den2", "Den3"):
        replay.race(replay.first_round(den))
    # Den2 heat 2 as first raced. The script reruns it, with 202 at 1.01,
    # after TwoThree has its racers.
    HeatLane.objects.filter(
        round=replay.first_round("Den2"), heat=2, racer=replay.racer(car=202)
    ).update(finish_time=1.0)
    round_ended(replay.first_round("Den3"), playlist_finalists())

    two_three = Round.objects.get(group=replay.group("TwoThree"), number=1)
    php = replay.export.lanes[("TwoThree", 1)]
    assert sorted(e.racer.car_number for e in two_three.roster.all()) == sorted(
        {lane.racer[0] for lane in php}
    )
    chart = HeatLane.objects.filter(round=two_three).order_by("heat", "lane")
    assert [(h.heat, h.lane, h.racer.car_number) for h in chart] == [
        (lane.heat, lane.lane, lane.racer[0]) for lane in php
    ]
