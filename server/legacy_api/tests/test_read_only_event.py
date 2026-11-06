"""Every legacy write refuses a read-only event, and works again after
Reopen for editing (#212; #9, Past events).

Each registered action is sent to a read-only active event that holds a
race day: it answers `event-read-only` and leaves the event's rows as they
were. After `core.events.reopen`, the same request answers success. A new
action is in `LEAK_REQUESTS` (`test_event_leak.py` enforces it), so this
test sends it too, and fails it until its service refuses.
`READ_ONLY_REQUESTS` replaces a leak request whose ids name rows of another
event, which would answer a not-found failure without reaching the service.

`FIELD_CLIENT_ACTIONS` are what a timer, a kiosk or a login sends to stay
connected; they are not changes a person makes, and a switch must not break
a running timer or kiosk (#212, Risks).
"""

import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE
from awards.models import BallotAward
from awards.tests.factories import AwardFactory
from core import events, race_settings
from core.models import ActionLog, Event, RaceSettings, SiteState, event_scoped_models
from core.services import EventReadOnly
from core.tests.factories import EventFactory
from kiosks.models import Kiosk, Scene
from legacy_api.registry import REGISTRY, Format
from legacy_api.tests.test_event_leak import LEAK_REQUESTS, call, identical_roster
from legacy_api.xml import PROLOG
from racing.models import TimerStatus
from racing.services import rounds as rounds_service
from racing.tests.factories import PlaylistEntryFactory
from roster.models import CarRange, Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.car_numbers import add_range
from transfer import snapshot
from ui.tests.race import add_racers, race_round, race_setup, scheduled

pytestmark = pytest.mark.django_db

# Actions that never refuse a read-only event, and why.
FIELD_CLIENT_ACTIONS = {
    "role.login": "a login is site-wide (ADR-07)",
    "login": "a login is site-wide (ADR-07)",
    "session.write": "the visitor's session, not the event",
    "setup.nodata": "creates or opens an event: the lifecycle itself",
    "file.directory.new.nodata": "a folder under the data folder",
    "timer-message": "the timer's connection; FINISHED refuses its results",
    "message.retrieve": "a kiosk or replay station reading its queue",
    "message.send": "the replay page and its camera signal WebRTC through it",
    "replay.message": "the replay station reporting its state",
    "test.setup": "a test endpoint",
}

# Requests that name rows of the read-only event, by action name. Values in
# braces are the ids `race_day` returns.
READ_ONLY_REQUESTS = {
    "partition.apply-rule": {"rule": "by-partition"},
    "photo.upload": {"repo": "head", "racerid": "{racer}"},
    "photo.assign": {"repo": "head", "racer": "{racer}", "photo": "x.jpg"},
    "scene.add": {"name": "Spotlight"},
    "timer-message": {"message": "HEARTBEAT", "confirmed": "1"},
    "racer.edit": {"racer": "{racer}", "carno": "150", "rankid": "{rank}"},
    "racer.delete": {"racer": "{fresh}"},
    "class.edit": {"classid": "{class2}", "name": "Panthers"},
    "class.delete": {"classid": "{spare_class}"},
    "class.order": {"classid_1": "{class2}", "classid_2": "{class}"},
    "rank.add": {"classid": "{class}", "name": "Den 9"},
    "rank.edit": {"rankid": "{rank}", "name": "Renamed"},
    "rank.delete": {"rankid": "{spare_rank}"},
    "rank.move": {"rankid": "{spare_rank}", "classid": "{class2}"},
    "rank.order": {"rankid_1": "{rank}"},
    "partition.edit": {"partitionid": "{partition}", "name": "Renamed"},
    "partition.delete": {"partitionid": "{spare_partition}"},
    "partition.range-add": {
        "partitionid": "{partition}",
        "first": "500",
        "last": "509",
    },
    "partition.range-remove": {"rangeid": "{range}"},
    "partition.move": {
        "div_id": "{spare_partition}",
        "group_field": "rankid",
        "group_id": "{rank}",
    },
    "partition.order": {
        "partitionid_1": "{partition2}",
        "partitionid_2": "{partition}",
    },
    "heat.select": {"roundid": "{round}", "heat": "1", "now_racing": "0"},
    "result.delete": {"roundid": "{round}", "heat": "1"},
    "schedule.generate": {"roundid": "{round2}"},
    "schedule.unschedule": {"roundid": "{round}"},
    "schedule.reschedule": {"roundid": "{round}", "racerid": "{late}"},
    "roster.new": {"roundid": "{round}", "top": "2"},
    "roster.delete": {"roundid": "{wolves2}"},
    "award.edit": {"awardid": "new", "name": "Paint", "awardtypeid": "4"},
    "award.delete": {"awardid": "{award}"},
    "award.order": {"awardid_1": "{award}"},
    "award.adhoc": {"racerid": "{racer}", "awardname": "Shiny"},
    "award.winner": {"awardid": "{award}", "racerid": "{racer}"},
    "award.present": {"key": "award-{award}", "reveal": "1"},
    "award.xbs": {"racer": "{racer}", "value": "1"},
    "award.import": {
        "awardname": "Paint",
        "awardtype": "Design Trophy",
        "classname": "Wolves",
        "carnumber": "101",
    },
    "ballot.open": {"state": "open"},
    "vote.cast": {"awardid": "{award}", "votes": "[{racer}]"},
    "kiosk.assign": {"address": "K1", "page": "kiosks/flag.kiosk"},
    "kiosk.reload": {"address": "K1"},
    "scene.delete": {"sceneid": "{scene}"},
    "scene.setkiosk": {
        "sceneid": "{scene}",
        "kiosk_name": "Main",
        "page": "kiosks/flag.kiosk",
    },
    "scene.apply": {"sceneid": "{scene}"},
    "playlist.add": {"classid": "{class2}", "round": "1"},
    "playlist.edit": {"queueid": "{queue}", "top": "2"},
    "playlist.delete": {"queueid": "{queue}"},
    "playlist.order": {"queueid_1": "{queue}"},
    "result.import": {
        "class": "Bears",
        "round": "1",
        "heat": "1",
        "lane": "1",
        "firstname": "Bears1",
        "lastname": "Racer",
        "carnumber": "201",
        "finishtime": "3.936",
        "finishplace": "3",
        "completed": "2015-03-08 18:35:44",
    },
    "timer.assign-flag": {"flag": "debug", "value": "true"},
    "timer.assign-device": {"device": "TestTimer"},
    "timer.remote-start": {},
    "racer.import": {"first-last": "New Racer", "partition": "Wolves"},
    "racer.pass": {"barcode": "PWD299"},
}


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(out, "JPEG")
    return out.getvalue()


def uploads(name: str, event) -> dict:
    """The file an upload action posts."""
    if name == "photo.upload":
        return {"photo": SimpleUploadedFile("head.jpg", _jpeg(), "image/jpeg")}
    if name == "video.upload":
        return {"video": SimpleUploadedFile("replay.mkv", b"\x1aE", "video/x-matroska")}
    if name == "preferences.upload":
        return {"prefs": SimpleUploadedFile("p.pref", b"supergroup-label=Troop\n")}
    if name == "snapshot.put":
        body = (PROLOG + snapshot.get(event)).encode()
        return {"snapshot": SimpleUploadedFile("s.xml", body, "text/xml")}
    return {}


def race_day(event) -> dict[str, int]:
    """A race day in `event`: two groups with racers, round 1 of each, the
    first scheduled and one heat run, an award, a scene, a kiosk, a playlist
    step, a car number range, and a group, rank and partition with no
    racers. Returns the ids the requests name."""
    rounds = race_setup(event, groups=(("Wolves", 4), ("Bears", 4)))
    scheduled(rounds["Wolves"])
    race_round(event)
    wolves, bears = (
        Group.objects.get(event=event, name=n) for n in ("Wolves", "Bears")
    )
    award = AwardFactory(event=event, group=wolves, award_type_id=4)
    spare = groups.find_or_create_group(event.pk, "Spare")
    spare_rank = groups.find_or_create_subgroup(wolves, "Den 2", with_partition=False)
    spare_partition = groups.create_partition_in_subgroup(spare_rank, "Spare P")
    partition = Partition.objects.get(event=event, name="Wolves")
    add_range(partition, 300, 309)
    Kiosk.objects.create(event=event, address="K1", name="Main")
    late = add_racers(event, "Wolves", 1, 199)[0]
    fresh = add_racers(event, "Bears", 1, 299, passed=False)[0]
    race_settings.write(event, "balloting", "open")
    BallotAward.objects.create(event=event, award=award, depth=1)
    TimerStatus.objects.filter(event=event).update(remote_start_seen_at=timezone.now())
    folders = RaceSettings.objects.get(event=event)
    Path(folders.video_directory).mkdir(parents=True, exist_ok=True)
    for folder in (folders.photo_directory, folders.car_photo_directory):
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / "x.jpg").write_bytes(_jpeg())
    queue = PlaylistEntryFactory(event=event, group=bears, round_number=1)
    return {
        "late": late.pk,
        "wolves2": rounds_service.make_new_round(wolves, 2).pk,
        "fresh": fresh.pk,
        "racer": Racer.objects.filter(event=event, car_number=101).get().pk,
        "class": wolves.pk,
        "class2": bears.pk,
        "spare_class": spare.pk,
        "rank": Subgroup.objects.filter(group=wolves)
        .exclude(pk=spare_rank.pk)
        .first()
        .pk,
        "spare_rank": spare_rank.pk,
        "partition": partition.pk,
        "partition2": Partition.objects.get(event=event, name="Bears").pk,
        "spare_partition": spare_partition.pk,
        "range": CarRange.objects.filter(event=event).first().pk,
        "round": rounds["Wolves"].pk,
        "round2": rounds["Bears"].pk,
        "award": award.pk,
        "scene": Scene.objects.filter(event=event).order_by("pk").last().pk,
        "queue": queue.pk,
    }


def _outcome(response, fmt) -> str:
    if fmt is Format.XML:
        failure = ET.fromstring(response.content).find(".//failure")
        return "success" if failure is None else failure.get("code")
    return json.loads(response.content)["outcome"]["code"]


def rows(event):
    """Every event-scoped row of `event`, except the action log, which the
    dispatcher writes before the handler runs (`legacy_api.views.dispatch`)."""
    return {
        model._meta.label: list(
            model.objects.filter(event=event).order_by("pk").values()
        )
        for model in event_scoped_models()
        if model is not ActionLog
    }


@pytest.fixture
def day(client):
    first, active = EventFactory(), EventFactory()
    identical_roster(first)
    SiteState.objects.filter(pk=1).update(active_event=active)
    ids = race_day(active)
    Event.objects.filter(pk=active.pk).update(read_only=True)
    session = client.session
    session[SESSION_ROLE] = "RaceCoordinator"
    session[SESSION_PERMISSIONS] = -1
    session.save()
    return Event.objects.get(pk=active.pk), ids


def request_for(name: str, ids: dict) -> dict:
    fields = READ_ONLY_REQUESTS.get(name, LEAK_REQUESTS[("action", name)])
    return {k: v.format(**ids) for k, v in fields.items()}


ACTIONS = [e for e in REGISTRY if e.kind == "action"]


@pytest.mark.parametrize("endpoint", ACTIONS, ids=[e.name for e in ACTIONS])
def test_every_action_refuses_a_read_only_event(client, day, endpoint):
    event, ids = day
    fields = request_for(endpoint.name, ids) | uploads(endpoint.name, event)
    before = rows(event)

    response = call(client, endpoint.kind, endpoint.name, fields)

    if endpoint.name in FIELD_CLIENT_ACTIONS:
        assert _outcome(response, endpoint.fmt) != EventReadOnly.code
        return
    assert (endpoint.name, _outcome(response, endpoint.fmt)) == (
        endpoint.name,
        EventReadOnly.code,
    )
    assert rows(event) == before


@pytest.mark.parametrize("endpoint", ACTIONS, ids=[e.name for e in ACTIONS])
def test_every_action_succeeds_after_reopen_for_editing(client, day, endpoint):
    if endpoint.name in FIELD_CLIENT_ACTIONS:
        pytest.skip("never refused")
    event, ids = day
    events.reopen(event.pk)
    fields = request_for(endpoint.name, ids) | uploads(endpoint.name, event)

    response = call(client, endpoint.kind, endpoint.name, fields)

    assert (endpoint.name, _outcome(response, endpoint.fmt)) == (
        endpoint.name,
        "success",
    )


QUERIES = [e for e in REGISTRY if e.kind == "query"]


@pytest.mark.parametrize("endpoint", QUERIES, ids=[e.name for e in QUERIES])
def test_no_query_refuses_a_read_only_event(client, day, endpoint):
    event, ids = day
    fields = LEAK_REQUESTS[(endpoint.kind, endpoint.name)]

    response = call(client, endpoint.kind, endpoint.name, fields)

    assert response.status_code == 200
    assert EventReadOnly.code.encode() not in response.content


def test_a_timer_keeps_its_connection_and_records_no_result(client, day):
    """A timer talking to a read-only event: its heartbeat is answered, and
    a FINISHED message refuses its times (#212)."""
    event, ids = day
    before = rows(event)["racing.HeatLane"]

    heartbeat = call(
        client, "action", "timer-message", {"message": "HEARTBEAT", "confirmed": "1"}
    )
    finished = call(
        client,
        "action",
        "timer-message",
        {"message": "FINISHED", "lane1": "3.1", "lane2": "3.2", "lane3": "3.3"},
    )

    assert _outcome(heartbeat, Format.XML) == "success"
    assert _outcome(finished, Format.XML) == EventReadOnly.code
    assert rows(event)["racing.HeatLane"] == before
