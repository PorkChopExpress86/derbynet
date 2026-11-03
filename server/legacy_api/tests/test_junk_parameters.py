"""Every legacy endpoint, posted junk in every parameter it can read, answers
a refusal or a success, never `server-error` (#43 retro). The parameter names
are the string constants of the handler's module, so a handler that reads a
new parameter is fuzzed with it the day it exists, as
`ui/tests/test_every_view.py` does for the pages."""

import ast
import inspect
import re
import sys
from functools import reduce
from operator import or_
from urllib.parse import urlencode

import pytest

from accounts.permissions import Perm
from legacy_api.registry import REGISTRY
from legacy_api.tests.conftest import log_in, post

pytestmark = pytest.mark.django_db

# `1e999` overflows a float, which `int()` and a JSON column both refuse.
JUNK = ["zzz", "", "nan", "-1", "1e999"]
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
ENDPOINTS = sorted(REGISTRY, key=lambda e: (e.kind, e.name))


def parameter_names(endpoint) -> list[str]:
    source = inspect.getsource(sys.modules[endpoint.handler.__module__])
    return sorted(
        {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and NAME.match(node.value)
        }
    )


@pytest.mark.parametrize(
    "endpoint", ENDPOINTS, ids=[f"{e.kind}-{e.name}" for e in ENDPOINTS]
)
def test_junk_parameters_never_answer_server_error(client, event, endpoint):
    log_in(client, reduce(or_, Perm))
    for junk in JUNK:
        data = {
            **dict.fromkeys(parameter_names(endpoint), junk),
            endpoint.kind: endpoint.name,
        }
        if endpoint.kind == "query":
            response = client.get("/action.php", data)
        else:
            response = post(client, urlencode(data))
        assert response.status_code < 500, (junk, response.status_code)
        assert b"server-error" not in response.content, (junk, response.content[:300])
