import pytest

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE, Access
from core.tests.factories import EventFactory
from legacy_api.registry import REGISTRY, Endpoint


@pytest.fixture
def endpoint():
    """Register a test endpoint for one test: `endpoint(kind, name, handler, ...)`.

    The permission defaults to anonymous so a test states only what it checks.
    """
    added = []

    def register(kind, name, handler=lambda ctx: None, **fields):
        fields.setdefault("permission", Access.ANONYMOUS)
        registered = Endpoint(kind, name, handler, **fields)
        REGISTRY.register(registered)
        added.append(registered)
        return registered

    yield register
    for registered in added:
        REGISTRY.unregister(registered)


@pytest.fixture
def event(db):
    return EventFactory()


def log_in(client, permissions, role="Tester"):
    """Store a login in the session, as #16's role.login will."""
    session = client.session
    session[SESSION_ROLE] = role
    session[SESSION_PERMISSIONS] = int(permissions)
    session.save()


def post(client, body, path="/action.php"):
    return client.post(path, body, content_type="application/x-www-form-urlencoded")


def _with_test_endpoints(enabled):
    from django.test import override_settings

    from legacy_api.handlers import testing

    with override_settings(ENABLE_TEST_ENDPOINTS=enabled):
        testing.sync()
        yield
    # Back to the process's own setting, which CI sets to on.
    testing.sync()


@pytest.fixture
def test_endpoints():
    """Turn on the test-only endpoints for one test."""
    yield from _with_test_endpoints(True)


@pytest.fixture
def no_test_endpoints():
    """Turn them off for one test, as a real race runs."""
    yield from _with_test_endpoints(False)
