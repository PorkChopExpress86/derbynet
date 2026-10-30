"""The #9 leak test: every legacy endpoint answers from the active event only.

Two events get identical rosters and the second one is active. A query that
forgets the event filter returns correct-looking rows while the first event is
active, so only a later active event shows the leak. After each call,
`check_invariants()` must find no row pointing into another event.

Each ported endpoint adds a request to LEAK_REQUESTS, which the last test
enforces.
"""

import json
import xml.etree.ElementTree as ET

import pytest

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE
from core.invariants import check_invariants
from core.models import SiteState
from core.tests.factories import EventFactory
from legacy_api.registry import REGISTRY, Format
from racing.models import Round
from racing.services.polls import POLL_VALUES
from racing.tests.factories import RosterEntryFactory, RoundFactory
from roster.models import Group, Racer, Subgroup
from roster.tests.factories import GroupFactory, RacerFactory, SubgroupFactory

pytestmark = pytest.mark.django_db

# (kind, name) to the form fields of a call that reads or writes event rows.
LEAK_REQUESTS: dict[tuple[str, str], dict] = {
    ("action", "role.login"): {"name": "RaceCoordinator", "password": "doyourbest"},
    ("query", "role.list"): {},
    ("action", "login"): {"name": "RaceCoordinator", "password": "doyourbest"},
    ("query", "roles"): {},
    ("action", "session.write"): {"session_barcode-action": "checkin"},
    ("query", "settings.list"): {},
    ("query", "stats"): {},
    ("action", "database.purge"): {"purge": "racers"},
    ("action", "preferences.upload"): {},
    ("query", "snapshot.get"): {},
    ("query", "file.directory.nodata"): {"path": "/"},
    ("query", "file.stat"): {"path": "/"},
    ("action", "setup.nodata"): {},
    ("action", "file.directory.new.nodata"): {"directory": "/nonexistent/x"},
    ("action", "snapshot.put"): {},
    ("action", "result.import"): {
        "class": "Tiger",
        "round": "1",
        "heat": "1",
        "lane": "1",
        "firstname": "Matthew",
        "lastname": "Glennon",
        "carnumber": "105",
        "finishtime": "3.936",
        "finishplace": "3",
        "completed": "2015-03-08 18:35:44",
    },
    ("action", "settings.write"): {"partition-label": "Patrol"},
    ("action", "database.execute"): {"script": "schema"},
    ("query", "database.check"): {},
    ("action", "racer.add"): {"firstname": "New", "lastname": "Racer", "carno": "101"},
    ("action", "racer.edit"): {"racer": "1", "carno": "102", "rankid": "1"},
    ("action", "racer.delete"): {"racer": "1"},
    ("action", "racer.bulk"): {"who": "all", "what": "number", "auto": "1"},
    ("action", "racer.pass"): {"barcode": "PWDwi777"},
    ("action", "racer.import"): {"first-last": "New Racer", "partition": "P101"},
    ("action", "racer.fake"): {"ngroups": "1", "racers_per_group": "3"},
    ("query", "racer.list"): {"barcode": "PWD101", "order": "class"},
    ("query", "class.list"): {},
    ("action", "class.add"): {"name": "Webelos"},
    ("action", "class.edit"): {"classid": "1", "name": "Renamed"},
    ("action", "class.delete"): {"classid": "1"},
    ("action", "class.order"): {"classid_1": "1", "classid_2": "2"},
    ("action", "rank.add"): {"classid": "1", "name": "Den 2"},
    ("action", "rank.edit"): {"rankid": "1", "name": "Renamed"},
    ("action", "rank.delete"): {"rankid": "1"},
    ("action", "rank.move"): {"rankid": "1", "classid": "-1"},
    ("action", "rank.order"): {"rankid_1": "1", "rankid_2": "2"},
    ("action", "partition.add"): {"name": "Walk-ins"},
    ("action", "partition.edit"): {"partitionid": "1", "name": "Renamed"},
    ("action", "partition.delete"): {"partitionid": "1"},
    ("action", "partition.range-add"): {"partitionid": "1", "first": "1", "last": "9"},
    ("action", "partition.range-remove"): {"rangeid": "1"},
    ("query", "partition.range-list"): {},
    ("action", "partition.move"): {
        "div_id": "1",
        "group_field": "classid",
        "group_id": "-1",
    },
    ("action", "partition.order"): {"partitionid_1": "1", "partitionid_2": "2"},
    ("action", "partition.apply-rule"): {"rule": "one-group"},
    # The ids name rows of the first event, which is not active.
    ("action", "heat.select"): {"roundid": "1", "now_racing": "1"},
    ("action", "heat.rerun"): {"heat": "current"},
    ("action", "heat.reinstate"): {},
    ("action", "result.write"): {"lane1": "3.1", "lane2": "3.2"},
    ("action", "result.delete"): {"roundid": "1", "heat": "1"},
    ("action", "schedule.generate"): {"roundid": "1"},
    ("action", "schedule.unschedule"): {"roundid": "1"},
    ("action", "schedule.reschedule"): {"roundid": "1", "racerid": "1"},
    ("action", "roster.new"): {"roundid": "1", "top": "2"},
    ("action", "roster.delete"): {"roundid": "1"},
    ("query", "poll.coordinator"): {"roundid": "1", "heat": "1"},
    ("query", "poll"): {
        "values": ",".join(POLL_VALUES),
        "roundid": "1",
        "heat": "1",
        "row-height": "40",
    },
    ("query", "poll.results"): {"roundid": "1", "details": "1"},
    ("action", "timer-message"): {
        "message": "FINISHED",
        "roundid": "1",
        "heat": "1",
        "lane1": "3.1",
    },
    ("action", "timer.assign-flag"): {"flag": "debug", "value": "true"},
    ("action", "timer.assign-port"): {"port": "COM1"},
    ("action", "timer.assign-device"): {"device": "TestTimer"},
    ("action", "timer.remote-start"): {},
    ("action", "timer.test"): {"tt-mask": "1"},
    ("query", "timer.settings"): {},
    ("query", "timer.log"): {"seek": "0"},
    ("query", "poll.timer.test"): {},
    ("action", "award.edit"): {
        "awardid": "new",
        "name": "Paint",
        "awardtypeid": "4",
        "class_and_rank": "1,1",
    },
    ("action", "award.delete"): {"awardid": "1"},
    ("action", "award.order"): {"awardid_1": "1"},
    ("action", "award.adhoc"): {"racerid": "1", "awardname": "Shiny"},
    ("action", "award.winner"): {"awardid": "1", "racerid": "1"},
    ("action", "award.import"): {
        "awardname": "Paint",
        "awardtype": "Design Trophy",
        "classname": "Cubs",
        "carnumber": "101",
    },
    ("action", "award.present"): {"key": "award-1", "reveal": "1"},
    ("action", "award.calc-rule"): {"full-field-calc": "0"},
    ("action", "award.xbs"): {"racer": "1", "value": "1"},
    ("query", "award.current"): {},
    ("query", "award.list"): {},
    ("query", "poll.kiosk"): {"address": "K1"},
    ("query", "poll.kiosk.all"): {},
    ("action", "kiosk.assign"): {"address": "K1", "page": "kiosks/flag.kiosk"},
    ("action", "kiosk.reload"): {"address": "K1"},
    ("query", "scene.list"): {},
    ("action", "scene.add"): {"name": "Welcome"},
    ("action", "scene.delete"): {"sceneid": "1"},
    ("action", "scene.setkiosk"): {"sceneid": "1", "kiosk_name": "Main"},
    ("action", "scene.apply"): {"sceneid": "1"},
    # Registered only with DERBYNET_ENABLE_TEST_ENDPOINTS, so it is listed here
    # for a run that sets it.
    ("action", "test.setup"): {"slide": "s.png"},
}

# Response keys that carry a row id, and the model the id belongs to.
ID_KEYS = {"racerid": Racer, "roundid": Round, "classid": Group, "rankid": Subgroup}


def identical_roster(event):
    group = GroupFactory(event=event, name="Cubs", sort_order=1)
    subgroup = SubgroupFactory(group=group, name="Den 1", sort_order=1)
    round_ = RoundFactory(group=group)
    for car in (101, 102, 103):
        racer = RacerFactory(
            group=group,
            subgroup=subgroup,
            partition__name=f"P{car}",
            car_number=car,
            first_name=f"First{car}",
            last_name=f"Last{car}",
        )
        RosterEntryFactory(round=round_, racer=racer)


@pytest.fixture
def two_events(client):
    first, active = EventFactory(), EventFactory()
    identical_roster(first)
    identical_roster(active)
    SiteState.objects.filter(pk=1).update(active_event=active)
    session = client.session
    session[SESSION_ROLE] = "RaceCoordinator"
    session[SESSION_PERMISSIONS] = -1
    session.save()
    return first, active


def _ids_in(response, fmt):
    if fmt is Format.XML:
        # The root's attributes echo the request.
        for element in list(ET.fromstring(response.content).iter())[1:]:
            yield from (
                (key, value) for key, value in element.attrib.items() if key in ID_KEYS
            )
        return
    body = json.loads(response.content)
    body.pop("action", None)
    stack = [body]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ID_KEYS and not isinstance(item, (dict, list)):
                    yield key, item
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)


def leaked(response, fmt, first):
    first_ids = {
        key: set(model.objects.filter(event=first).values_list("pk", flat=True))
        for key, model in ID_KEYS.items()
    }
    return [
        (key, value)
        for key, value in _ids_in(response, fmt)
        if str(value).isdigit() and int(value) in first_ids[key]
    ]


def call(client, kind, name, fields):
    if kind == "action":
        return client.post("/action.php", {"action": name, **fields})
    return client.get("/action.php", {"query": name, **fields})


def assert_no_leak(client, kind, name, fields, fmt, events):
    first, active = events
    response = call(client, kind, name, fields)

    assert response.status_code == 200
    assert leaked(response, fmt, first) == []
    for event in events:
        assert "cross-event" not in check_invariants(event)


@pytest.mark.parametrize(
    "endpoint", list(REGISTRY), ids=[f"{e.kind}-{e.name}" for e in REGISTRY]
)
def test_every_endpoint_answers_from_the_active_event_only(
    client, two_events, endpoint
):
    fields = LEAK_REQUESTS[(endpoint.kind, endpoint.name)]
    assert_no_leak(
        client, endpoint.kind, endpoint.name, fields, endpoint.fmt, two_events
    )


def test_every_endpoint_has_a_leak_request():
    registered = {(e.kind, e.name) for e in REGISTRY}

    assert registered <= set(LEAK_REQUESTS)


def _list_racers(rows):
    def handler(ctx):
        ctx.put("racers", [{"racerid": r.pk} for r in rows(ctx)])

    return handler


def test_the_check_passes_a_handler_that_filters_by_event(client, two_events, endpoint):
    endpoint(
        "query",
        "probe.racers",
        _list_racers(lambda ctx: Racer.objects.filter(event=ctx.event)),
    )

    assert_no_leak(client, "query", "probe.racers", {}, Format.JSON, two_events)


def test_the_check_catches_a_handler_that_forgets_the_event(
    client, two_events, endpoint
):
    endpoint("query", "probe.racers", _list_racers(lambda ctx: Racer.objects.all()))

    with pytest.raises(AssertionError, match="racerid"):
        assert_no_leak(client, "query", "probe.racers", {}, Format.JSON, two_events)


def test_the_check_catches_a_write_into_the_other_event(client, two_events, endpoint):
    first, active = two_events

    def handler(ctx):
        racer = Racer.objects.get(pk=ctx.args["racerid"])
        RosterEntryFactory(
            round=Round.objects.filter(event=ctx.event).get(), racer=racer
        )

    endpoint("action", "probe.enter", handler)
    stranger = Racer.objects.filter(event=first).first()

    with pytest.raises(AssertionError, match="cross-event"):
        assert_no_leak(
            client,
            "action",
            "probe.enter",
            {"racerid": stranger.pk},
            Format.JSON,
            two_events,
        )
