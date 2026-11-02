"""`no_db_writes()`: fails a request that writes the database (#41).

Page views and poll views only read: a poll that wrote race state would
change it every 2 s on every open dashboard, where PHP leaves the change to
an action or a kiosk poll. Wrap the requests a test makes:

    with no_db_writes():
        client.get("/dashboard/live/")

It refuses any INSERT, UPDATE, DELETE or REPLACE, even one a rollback undoes.
Session writes go through `ui.tests.sessions.no_session_writes()`.
"""

import re
from contextlib import contextmanager

from django.db import connections

WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def _refuse_writes(execute, sql, params, many, context):
    if WRITE.match(sql):
        raise AssertionError(f"the request wrote the database: {sql[:200]}")
    return execute(sql, params, many, context)


@contextmanager
def no_db_writes():
    with connections["default"].execute_wrapper(_refuse_writes):
        yield
