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
