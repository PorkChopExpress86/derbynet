import pytest
from django.test import Client

from core.tests.factories import EventFactory
from ui.tests.roles import PASSWORDS


@pytest.fixture
def event(db):
    return EventFactory()


@pytest.fixture
def login(event):
    """`login(name)` returns a client logged in as that default role, or a
    visitor's client for "anonymous"."""

    def as_role(name: str) -> Client:
        client = Client()
        if name != "anonymous":
            response = client.post(
                "/login/", {"name": name, "password": PASSWORDS[name]}
            )
            assert response.status_code == 302, response.content
        return client

    return as_role
