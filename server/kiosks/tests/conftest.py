import pytest

from core.tests.factories import EventFactory


@pytest.fixture
def event(db):
    return EventFactory()
