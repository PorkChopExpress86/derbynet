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
