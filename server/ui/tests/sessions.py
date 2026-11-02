"""`no_session_writes()`: fails a request that saves the session (#15).

Page views and poll views must never write the session, so a poll cannot slide
its expiry or race a login in another tab. Wrap the requests a test makes:

    with no_session_writes():
        client.get("/")
"""

from contextlib import contextmanager
from importlib import import_module
from unittest import mock

from django.conf import settings


def _refuse(*args, **kwargs):
    raise AssertionError("the request wrote the session")


@contextmanager
def no_session_writes():
    store = import_module(settings.SESSION_ENGINE).SessionStore
    with (
        mock.patch.object(store, "save", _refuse),
        mock.patch.object(store, "create", _refuse),
        mock.patch.object(store, "delete", _refuse),
    ):
        yield
