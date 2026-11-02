"""Checks every URL of the new UI at once, so a page a later ticket adds is
covered the day its URL exists (#41 retro):

- a GET writes neither the session nor the database (`no_session_writes`,
  `no_db_writes`), so page, sheet and poll views only read;
- no request answers 500, whatever junk its parameters carry. The parameter
  names come from the string constants in the view's module, so a view that
  parses a new parameter is fuzzed with it too.
"""

import ast
import importlib
import inspect
import re

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

from core.models import RaceState
from roster.models import Racer
from ui.tests.db import no_db_writes
from ui.tests.race import race_setup, scheduled
from ui.tests.roles import PASSWORDS
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db

JUNK = ["zzz", "", "nan", "-1", "1e999"]
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


def ui_patterns(patterns=None, prefix=""):
    """(route, view) for every URL whose view lives in the `ui` app."""
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(p, URLResolver):
            yield from ui_patterns(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            view = inspect.unwrap(p.callback)
            if getattr(view, "__module__", "").split(".")[0] == "ui":
                yield prefix + str(p.pattern), view


ROUTES = sorted({route: view for route, view in ui_patterns()}.items())


def module_names(view) -> list[str]:
    """Every short lowercase string constant in the view's module, and in
    the modules it imports from `ui`: the parameter names it can read."""
    module = importlib.import_module(view.__module__)
    sources = [module]
    for value in vars(module).values():
        home = inspect.getmodule(value)
        if home and home.__name__.startswith("ui.") and home not in sources:
            sources.append(home)
    names = set()
    for source in sources:
        for node in ast.walk(ast.parse(inspect.getsource(source))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if NAME.match(node.value):
                    names.add(node.value)
    # Lane fields the views build as f"lane{n}", f"t{n}" and f"p{n}".
    names |= {f"{prefix}{n}" for prefix in ("lane", "t", "p") for n in range(1, 5)}
    return sorted(names)


def fill(route: str, ids: dict[str, int], word: str) -> str:
    def value(match):
        kind, name = match.group(1), match.group(2)
        return str(ids.get(name, 999999)) if kind == "int" else word

    return "/" + re.sub(r"<(\w+):(\w+)>", value, route)


@pytest.fixture
def race(event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    return {
        "round_id": wolves.pk,
        "racer_id": Racer.objects.filter(event=event).first().pk,
        "lane": 1,
    }


@pytest.fixture
def coordinator(event):
    client = Client(raise_request_exception=False)
    response = client.post(
        "/login/", {"name": "RaceCoordinator", "password": PASSWORDS["RaceCoordinator"]}
    )
    assert response.status_code == 302
    return client


def words_for(route: str, view) -> list[str]:
    """Values for a route's string arguments: junk, and each string the view
    compares with, so the view gets past its own dispatch."""
    if "<str:" not in route:
        return ["zzz"]
    tree = ast.parse(inspect.getsource(view))
    found = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and NAME.match(node.value)
    }
    return ["zzz", *sorted(found)]


@pytest.mark.parametrize(("route", "view"), ROUTES, ids=[r for r, _ in ROUTES])
def test_a_get_writes_nothing(coordinator, race, event, route, view):
    """A due playlist scene tempts a poll to apply it."""
    from datetime import timedelta

    from django.utils import timezone

    RaceState.objects.filter(event=event).update(
        playlist_scene_at=timezone.now() - timedelta(seconds=5)
    )
    for word in words_for(route, view):
        url = fill(route, race, word)
        with no_session_writes(), no_db_writes():
            response = coordinator.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code < 500, (url, response.status_code)


@pytest.mark.parametrize(("route", "view"), ROUTES, ids=[r for r, _ in ROUTES])
def test_junk_parameters_never_answer_500(coordinator, race, route, view):
    names = module_names(view)
    for word in words_for(route, view):
        url = fill(route, race, word)
        for junk in JUNK:
            data = dict.fromkeys(names, junk)
            for method in (coordinator.get, coordinator.post):
                response = method(url, data, HTTP_HX_REQUEST="true")
                assert response.status_code < 500, (
                    method.__name__,
                    url,
                    junk,
                    response.content[:300],
                )
