"""`query=standings` (#37): every ladder of `StandingsOracle.all_standings`,
keyed as `website/inc/standings.inc` keys them.

The unit tests state PHP's semantics. The replays run the steps of
`capture/scenarios/12-standings-quirks.sh` that `http/standings/q7-tie-after-
earlier-trophy.http` and `http/standings/q13-drop-slowest.http` record, and
compare Django's reply with ids normalized as `php_http` describes. The
engine's ladders are compared with every standings transcript in
`racing/engine/tests/test_standings_parity.py`.
"""

import json
import os
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from awards.models import AwardPartition
from core.models import RaceSettings, SiteState
from core.tests.factories import EventFactory
from legacy_api.encoding import encode_php_json
from legacy_api.tests.test_race_control_parity import (
    Scenario,
    _dump,
    _exchange_text,
    active_event,
)
from legacy_api.tests.test_snapshot import GOLDEN as Q13_SNAPSHOT
from legacy_api.tests.test_snapshot import put
from racing.engine.tests.golden import (
    GOLDEN_DIR,
    read_transcript,
    transcript_deviations,
    write_deviation_entries,
)
from racing.models import HeatLane, LaneClosure, RosterEntry, Round
from roster.models import Racer, Subgroup
from roster.tests.factories import (
    GroupConstituentFactory,
    GroupFactory,
    PartitionFactory,
    RacerFactory,
    SubgroupFactory,
)

pytestmark = pytest.mark.django_db

WRITE_SQL = re.compile(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def standings(client):
    response = client.get("/action.php", {"query": "standings"})
    assert response.status_code == 200
    return response, json.loads(response.content)["standings"]


def race(round_, times):
    """One heat per racer, on lane 1, so the round is finished. The standings
    count a result only for a racer on the round's roster."""
    for heat, (racer, time) in enumerate(times.items(), 1):
        RosterEntry.objects.create(event=round_.event, round=round_, racer=racer)
        HeatLane.objects.create(
            event=round_.event,
            round=round_,
            heat=heat,
            lane=1,
            racer=racer,
            finish_time=time,
            completed=timezone.now(),
        )


def checked_in(group, n, subgroup=None):
    subgroup = subgroup or SubgroupFactory(group=group)
    return [
        RacerFactory(group=group, subgroup=subgroup, passed_inspection=True)
        for _ in range(n)
    ]


def test_anyone_reads_an_empty_object_when_nothing_has_raced(client, event):
    # D24: public, like the standings page. No login in this session.
    response, ladders = standings(client)

    assert ladders == {}
    assert b'"standings": {}' in response.content


def test_a_tie_shares_a_rung_and_leaves_empty_rungs_after_it(client, event):
    group = GroupFactory(event=event)
    round_ = Round.objects.create(event=event, group=group, number=1)
    a, b, c, d = checked_in(group, 4)
    race(round_, {a: 3.0, b: 3.0, c: 3.0, d: 3.5})

    _, ladders = standings(client)

    expected = [[a.pk, b.pk, c.pk], [], [], [d.pk]]
    assert ladders[f"st-r{round_.pk}"] == expected
    assert ladders["st-ff"] == expected


def test_the_keys_follow_phps_grammar(client, event):
    RaceSettings.objects.filter(event=event).update(use_subgroups=True)
    group = GroupFactory(event=event)
    lions = SubgroupFactory(group=group)
    tigers = SubgroupFactory(group=group)
    aggregate = GroupFactory(event=event)
    GroupConstituentFactory(aggregate=aggregate, constituent=group)
    round_ = Round.objects.create(event=event, group=group, number=1)
    [lion] = checked_in(group, 1, lions)
    [tiger] = checked_in(group, 1, tigers)
    race(round_, {lion: 3.1, tiger: 3.2})

    _, ladders = standings(client)

    r = round_.pk
    assert ladders == {
        f"st-r{r}": [[lion.pk], [tiger.pk]],
        f"st-rr{r}-{lions.pk}": [[lion.pk]],
        f"st-q{aggregate.pk}": [[lion.pk], [tiger.pk]],
        "st-ff": [[lion.pk], [tiger.pk]],
        f"st-rr{r}-{tigers.pk}": [[tiger.pk]],
    }


def test_an_excluded_or_unchecked_racer_takes_no_place(client, event):
    group = GroupFactory(event=event)
    round_ = Round.objects.create(event=event, group=group, number=1)
    fast, excluded, slow = checked_in(group, 3)
    Racer.objects.filter(pk=excluded.pk).update(exclude=True)
    absent = RacerFactory(group=group, subgroup=fast.subgroup)
    race(round_, {absent: 2.0, fast: 3.0, excluded: 3.1, slow: 3.2})

    _, ladders = standings(client)

    assert ladders[f"st-r{round_.pk}"] == [[fast.pk], [slow.pk]]


def test_only_the_active_events_racers_are_listed(client, event):
    other = EventFactory()
    for each in (other, event):
        group = GroupFactory(event=each)
        round_ = Round.objects.create(event=each, group=group, number=1)
        race(round_, dict.fromkeys(checked_in(group, 2), 3.0))
    SiteState.objects.filter(pk=1).update(active_event=event)

    _, ladders = standings(client)

    listed = {r for ladder in ladders.values() for rung in ladder for r in rung}
    assert listed == set(Racer.objects.filter(event=event).values_list("pk", flat=True))


def test_a_standings_read_writes_nothing(client, event):
    """PHP's standings.inc writes scoring=1 on every read while the legacy
    drop-slowest key is set (Q13, #64 C11). Django's read sends no write."""
    group = GroupFactory(event=event)
    round_ = Round.objects.create(event=event, group=group, number=1)
    race(round_, dict.fromkeys(checked_in(group, 2), 3.0))
    before = client.get("/action.php", {"query": "settings.list"}).content

    with CaptureQueriesContext(connection) as queries:
        standings(client)

    writes = [q["sql"] for q in queries if WRITE_SQL.match(q["sql"])]
    assert writes == []
    assert len(queries) > 0
    assert client.get("/action.php", {"query": "settings.list"}).content == before


# ----- The divergences `query=standings` records --------------------------------


def run(round_, heat, lane, racer, time=None, place=None):
    """One result, with the racer on the round's roster."""
    RosterEntry.objects.get_or_create(event=round_.event, round=round_, racer=racer)
    HeatLane.objects.create(
        event=round_.event,
        round=round_,
        heat=heat,
        lane=lane,
        racer=racer,
        finish_time=time,
        finish_place=place,
        completed=timezone.now(),
    )


def test_q23_a_racer_with_only_a_place_under_time_scoring_takes_no_place(client, event):
    """PHP throws a TypeError or files the racer under rung -1 (Q23)."""
    group = GroupFactory(event=event)
    round_ = Round.objects.create(event=event, group=group, number=1)
    timed, placed = checked_in(group, 2)
    run(round_, 1, 1, timed, time=3.0, place=1)
    run(round_, 1, 2, placed, place=2)

    _, ladders = standings(client)

    assert ladders[f"st-r{round_.pk}"] == [[timed.pk]]


def test_d33_a_racer_not_eligible_for_speed_awards_takes_no_place(client, event):
    """PHP has no such setting, and places every racer not excluded."""
    group = GroupFactory(event=event)
    round_ = Round.objects.create(event=event, group=group, number=1)
    fast, ineligible, slow = checked_in(group, 3)
    Racer.objects.filter(pk=ineligible.pk).update(not_eligible_for_speed_awards=True)
    race(round_, {fast: 3.0, ineligible: 3.1, slow: 3.2})

    _, ladders = standings(client)

    assert ladders[f"st-r{round_.pk}"] == [[fast.pk], [slow.pk]]


def test_d35_a_racer_outside_a_narrowed_partition_list_takes_no_place(client, event):
    """PHP has no partition lists, and places every racer not excluded."""
    group = GroupFactory(event=event)
    subgroup = SubgroupFactory(group=group)
    listed, other = (
        PartitionFactory(subgroup=subgroup),
        PartitionFactory(subgroup=subgroup),
    )
    AwardPartition.objects.create(event=event, group=group, partition=listed)
    round_ = Round.objects.create(event=event, group=group, number=1)
    outsider = RacerFactory(
        group=group, subgroup=subgroup, partition=other, passed_inspection=True
    )
    member = RacerFactory(
        group=group, subgroup=subgroup, partition=listed, passed_inspection=True
    )
    race(round_, {outsider: 3.0, member: 3.1})

    _, ladders = standings(client)

    assert ladders[f"st-r{round_.pk}"] == [[member.pk]]


def test_d37_a_closed_lanes_results_are_dropped(client, event):
    """PHP has no lane closures, and would place the lane-2 car first."""
    group = GroupFactory(event=event)
    round_ = Round.objects.create(event=event, group=group, number=1)
    open_lane, closed_lane = checked_in(group, 2)
    run(round_, 1, 1, open_lane, time=3.0, place=2)
    run(round_, 1, 2, closed_lane, time=2.9, place=1)
    LaneClosure.objects.create(event=event, round=round_, mask=0b10)

    _, ladders = standings(client)

    assert ladders[f"st-r{round_.pk}"] == [[open_lane.pk]]


# ----- capture/scenarios/12-standings-quirks.sh --------------------------------


def post_django(s: Scenario, **fields):
    """A step whose fields hold Django's ids already."""
    reply = json.loads(s.client.post("/action.php", fields).content)
    assert reply["outcome"]["summary"] == "success", (fields, reply)


def race_with(s: Scenario, time_of):
    """`race_round` with `RACE_TIME_EXPR`: `time_of(carnumber, lane)` in s."""
    roundid = s.coordinator()["current-heat"]["roundid"]
    while True:
        poll = s.coordinator()
        heat = poll["current-heat"]
        if not heat["now_racing"] or heat["roundid"] != roundid:
            return
        lanes = "&".join(
            f"lane{r['lane']}={time_of(r['carnumber'], r['lane'])}"
            for r in poll["racers"]
        )
        s.timer("message=STARTED")
        s.timer(f"message=FINISHED&{lanes}")


def start_round(s: Scenario, group: str):
    round_ = Round.objects.get(event=active_event(), group__name=group, number=1)
    post_django(s, action="schedule.generate", roundid=round_.pk)
    post_django(s, action="heat.select", roundid=round_.pk, now_racing=1)
    return round_


def run_standings_quirks(client) -> Scenario:
    s = Scenario(client)

    # Q7: Abbot (Lions) and Baker (Tigers) tie in every heat.
    s.reset()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=2")
    s.import_racer("firstname=Ann&lastname=Abbot&carnumber=301&partition=Lions")
    s.import_racer("firstname=Ben&lastname=Baker&carnumber=302&partition=Tigers")
    s.post_ok("action=partition.apply-rule&rule=one-group")
    lions = Subgroup.objects.get(event=active_event(), name="Lions")
    post_django(s, action="class.add", name="Lions Only", **{f"rankid_{lions.pk}": 1})
    s.post_ok("action=settings.write&one-trophy-per-checkbox=1&one-trophy-per=1")
    s.post_ok("action=settings.write&n-pack-trophies=0&n-den-trophies=1")
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    round_ = start_round(s, "All Racers")
    race_with(s, lambda car, lane: 3)
    # The only round, as PHP numbers it on an empty database.
    s.ids.to_php["round"][round_.pk] = 1

    s.scenario("http/standings/q7-tie-after-earlier-trophy.http")
    s.capture("racers")
    s.capture("classes")
    s.skip("export-json")
    s.capture("standings")
    s.skip("award.list")

    # Q13: two racers whose order depends on dropping the slowest heat.
    s.reset()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=2")
    s.import_racer("firstname=Ida&lastname=Gray&carnumber=401&partition=Snapshot")
    s.import_racer("firstname=Jo&lastname=Hart&carnumber=402&partition=Snapshot")
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    round_ = start_round(s, "Snapshot")
    times = {401: (3, 3.8), 402: (3.2, 3.3)}
    race_with(s, lambda car, lane: times[car][lane - 1])
    s.ids.to_php["round"][round_.pk] = 1

    s.scenario("http/standings/q13-drop-slowest.http")
    s.capture("standings before the snapshot")
    s.skip("snapshot.put")
    response = put(client, Q13_SNAPSHOT.read_bytes())
    assert b"<success/>" in response.content, response.content
    # The snapshot's rows are new Django rows; PHP's ids are the snapshot's.
    event = active_event()
    for car, php_id in ((401, 1), (402, 2)):
        racer = Racer.objects.get(event=event, car_number=car)
        s.ids.to_php["racer"][racer.pk] = php_id
    s.ids.to_php["round"][Round.objects.get(event=event).pk] = 1
    s.q13_settings = {}
    settings_scoring(s, AFTER_SNAPSHOT)
    s.capture("standings after the snapshot")
    settings_scoring(s, AFTER_STANDINGS)
    return s


# The two settings.list exchanges around Q13's second standings read, which
# show whether the read wrote `scoring`. The rest of Django's settings.list
# differs from PHP's throughout (it lists every setting, and the folders
# and ids of this run), so only the settings Q13 touches are compared.
Q13_FIXTURE = "http/standings/q13-drop-slowest.http"
AFTER_SNAPSHOT = "003 settings.list after the snapshot"
AFTER_STANDINGS = "005 settings.list after standings"
Q13_KEYS = ("scoring", "drop-slowest")
Q13_REASON = (
    "Django converts the legacy drop-slowest key to scoring=1 when it imports"
    " the snapshot, and reading the standings writes nothing; PHP lists no"
    " scoring until query=standings includes standings.inc, which writes"
    " scoring=1 (#64 C11). Only the settings Q13 touches are recorded."
)
DEVIATION_CLAIM = {("Q13", Q13_FIXTURE)}


def q13_body(body: bytes) -> bytes:
    """A settings.list body cut down to the settings Q13 touches."""
    settings = json.loads(body)["settings"]
    kept = {k: settings[k] for k in Q13_KEYS if k in settings}
    return encode_php_json({"settings": kept})


def settings_scoring(s: Scenario, name: str):
    """A recorded settings.list, kept as `q13_body` instead of compared whole."""
    s.skip(name.split(" ", 1)[1])
    response = s.client.get("/action.php", {"query": "settings.list"})
    s.q13_settings[name] = q13_body(response.content)


def test_the_standings_quirk_scenarios_match_php(client, event):
    s = run_standings_quirks(client)
    _dump(s)

    assert s.mismatches() == []
    compared = [name for got in s.got.values() for name in got if "standings" in name]
    assert len(compared) == 3


def test_q13_reading_the_standings_writes_no_scoring(client, event):
    """PHP's settings gain scoring=1 between 003 and 005, because its second
    standings read wrote it. Django's hold the scoring=1 its snapshot import
    converted (`transfer.legacy.raceinfo`) on both sides of the read, as
    the Q13 entry of deviations.json records for 003."""
    s = run_standings_quirks(client)
    php = read_transcript(GOLDEN_DIR / Q13_FIXTURE)
    django = s.q13_settings

    assert json.loads(php[AFTER_SNAPSHOT]["body"])["settings"].get("scoring") is None
    assert json.loads(php[AFTER_STANDINGS]["body"])["settings"]["scoring"] == 1
    assert django[AFTER_SNAPSHOT] == django[AFTER_STANDINGS]
    # Django converts drop-slowest on import and does not list it.
    assert json.loads(django[AFTER_STANDINGS]) == {"settings": {"scoring": 1}}
    [(quirk, expected)] = transcript_deviations(Q13_FIXTURE).values()
    assert quirk == "Q13"
    assert expected["body"] == django[AFTER_SNAPSHOT]
    assert expected["body"] != q13_body(php[AFTER_SNAPSHOT]["body"])


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the #37 Q13 entry"
)
def test_write_standings_deviations(client, event):
    s = run_standings_quirks(client)
    php = read_transcript(GOLDEN_DIR / Q13_FIXTURE)
    expected = "deviations/Q13/q13-drop-slowest.http"
    path = GOLDEN_DIR / expected
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        _exchange_text(
            AFTER_SNAPSHOT, php[AFTER_SNAPSHOT], s.q13_settings[AFTER_SNAPSHOT]
        )
    )
    entry = {
        "quirk": "Q13",
        "fixture": Q13_FIXTURE,
        "exchanges": [AFTER_SNAPSHOT],
        "reason": Q13_REASON,
        "expected": expected,
    }
    write_deviation_entries(DEVIATION_CLAIM, [entry])
