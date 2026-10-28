"""`query=database.check` (#63, S26).

PHP answers it anonymously. It reports the violations of the invariants that no
database constraint enforces; the other tags of PHP's list are constraints
(#12), so the database refuses them (`core/tests/test_invariants.py`).
"""

import json

import pytest
from django.test import override_settings

from accounts.permissions import Access, Perm
from core.invariants import CHECKS
from core.tests.factories import EventFactory
from core.tests.populated import populated_event
from core.tests.test_invariants import SERVICE_VIOLATIONS
from legacy_api.handlers import testing
from legacy_api.registry import REGISTRY
from legacy_api.tests.conftest import log_in
from roster.models import Racer
from roster.tests.factories import GroupFactory, RacerFactory

pytestmark = pytest.mark.django_db


def check(client):
    return json.loads(client.get("/action.php?query=database.check").content)


@pytest.fixture
def coordinator(client, event):
    """A SET_UP login, and `event` as the active event."""
    log_in(client, Perm.SET_UP)
    return client


def test_a_valid_event_passes(coordinator):
    body = check(coordinator)

    assert body == {
        "outcome": {"summary": "success", "code": "success", "description": ""}
    }


def test_a_populated_event_passes(coordinator):
    populated_event()  # Another event, which is not the active one.

    assert check(coordinator)["outcome"]["code"] == "success"


@pytest.mark.parametrize("tag", sorted(SERVICE_VIOLATIONS))
def test_every_invariant_tag_is_reported_as_corrupt(coordinator, event, tag):
    SERVICE_VIOLATIONS[tag](event)

    body = check(coordinator)

    assert body["outcome"] == {
        "summary": "failure",
        "code": "corrupt",
        "description": "Discrepancies detected",
    }
    assert body[tag]


def test_the_tags_are_the_invariant_checks_plus_cross_event():
    assert set(SERVICE_VIOLATIONS) == set(CHECKS)


def test_a_cross_event_reference_is_reported(coordinator, event):
    racer = RacerFactory(event=event, group__event=event)
    Racer.objects.filter(pk=racer.pk).update(group=GroupFactory(event=EventFactory()))

    body = check(coordinator)

    assert body["outcome"]["code"] == "corrupt"
    assert any(row["model"] == "roster.racer" for row in body["cross-event"])


def test_only_the_tags_with_violations_appear(coordinator, event):
    SERVICE_VIOLATIONS["rounds-sequential"](event)

    body = check(coordinator)

    assert set(body) - {"outcome"} == {"rounds-sequential"}


def test_another_events_violation_is_not_reported(coordinator):
    SERVICE_VIOLATIONS["rounds-sequential"](EventFactory())

    assert check(coordinator)["outcome"]["code"] == "success"


def test_an_anonymous_caller_is_refused_without_the_test_flag(
    client, no_test_endpoints
):
    EventFactory()

    body = check(client)

    assert body == {
        "outcome": {
            "summary": "failure",
            "code": "notauthorized",
            "description": "Not authorized -- please see race coordinator.",
        }
    }


def test_a_caller_without_set_up_is_refused(client, no_test_endpoints):
    EventFactory()
    log_in(client, Perm.CONTROL_RACE)

    assert check(client)["outcome"]["code"] == "notauthorized"


def test_an_anonymous_caller_is_served_with_the_test_flag(client, test_endpoints):
    EventFactory()

    assert check(client)["outcome"]["code"] == "success"


def test_the_flag_sets_the_permission(client):
    with override_settings(ENABLE_TEST_ENDPOINTS=True):
        testing.sync()
        on = REGISTRY.get("query", "database.check").permission
    with override_settings(ENABLE_TEST_ENDPOINTS=False):
        testing.sync()
        off = REGISTRY.get("query", "database.check").permission
    testing.sync()

    assert (on, off) == (Access.ANONYMOUS, Perm.SET_UP)
