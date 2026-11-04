"""Checks every URL of the new UI at once, so a page a later ticket adds is
covered the day its URL exists (#41 retro):

- a GET writes neither the session nor the database (`no_session_writes`,
  `no_db_writes`), so page, sheet and poll views only read;
- no request answers 500, whatever junk its parameters carry. The parameter
  names come from the string constants in the view's module, so a view that
  parses a new parameter is fuzzed with it too;
- a junk POST that changes nothing stored answers no success toast (#47
  retro): #47's Delete said "deleted" for a name that named no photo.
"""

import ast
import importlib
import inspect
import re
import textwrap
from contextlib import contextmanager
from itertools import product

import pytest
from django.db import connections
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

from awards.editing import set_winner
from awards.models import Award
from core.models import RaceState
from racing.models import PlaylistEntry
from roster.models import Partition, Racer
from roster.services.car_numbers import add_range
from ui.tests.db import WRITE, no_db_writes
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
    names = set()
    for source in _sources(view):
        for node in ast.walk(ast.parse(inspect.getsource(source))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if NAME.match(node.value):
                    names.add(node.value)
    # Lane fields the views build as f"lane{n}", f"t{n}" and f"p{n}".
    names |= {f"{prefix}{n}" for prefix in ("lane", "t", "p") for n in range(1, 5)}
    return sorted(names)


def fill(route: str, ids: dict[str, int], words: dict[str, str]) -> str:
    def value(match):
        kind, name = match.group(1), match.group(2)
        return str(ids.get(name, 999999)) if kind == "int" else words[name]

    return "/" + re.sub(r"<(\w+):(\w+)>", value, route)


@pytest.fixture
def race(event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    partition = Partition.objects.filter(event=event).first()
    car_range = add_range(partition, 100, 199)
    step = PlaylistEntry.objects.create(
        event=event, seq=1, group=wolves.group, round_number=2, bucket_limit=4
    )
    racer = Racer.objects.filter(event=event).first()
    award = Award.objects.create(
        event=event, name="Best Paint", award_type_id=1, group=wolves.group
    )
    set_winner(event, award, racer)
    RaceState.objects.filter(event=event).update(current_award_key=f"award-{award.pk}")
    return {
        "award_id": award.pk,
        "entry_id": step.pk,
        "round_id": wolves.pk,
        "racer_id": Racer.objects.filter(event=event).first().pk,
        "lane": 1,
        "group_id": wolves.group_id,
        "subgroup_id": partition.subgroup_id,
        "partition_id": partition.pk,
        "range_id": car_range.pk,
    }


@pytest.fixture
def coordinator(event):
    client = Client(raise_request_exception=False)
    response = client.post(
        "/login/", {"name": "RaceCoordinator", "password": PASSWORDS["RaceCoordinator"]}
    )
    assert response.status_code == 302
    return client


def _sources(view) -> list:
    """The view's module and the `ui` modules it imports from."""
    module = importlib.import_module(view.__module__)
    sources = [module]
    for value in vars(module).values():
        home = inspect.getmodule(value)
        if home and home.__name__.startswith("ui.") and home not in sources:
            sources.append(home)
    return sources


def dispatch_keys(view) -> set[str]:
    """The string keys of the module-level dicts in the view's module and its
    `ui` imports, such as `REPOS = {"head": ..., "car": ...}`: the values a
    view checks a route argument against with `in` (#47 retro, where every
    photos URL was a 404 here)."""
    keys = set()
    for source in _sources(view):
        for node in ast.parse(inspect.getsource(source)).body:
            if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(
                node.value, ast.Dict
            ):
                keys |= {
                    k.value
                    for k in node.value.keys
                    if isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and NAME.match(k.value)
                }
    return keys


def words_for(route: str, view) -> list[dict[str, str]]:
    """Values for a route's string arguments: junk, each string the view
    compares with, and its module's dispatch keys, so the view gets past its
    own dispatch. Every combination, when a route has several."""
    args = re.findall(r"<(?:str|path):(\w+)>", route)
    if not args:
        return [{}]
    # A wrapper a decorator defines inside a function is indented.
    tree = ast.parse(textwrap.dedent(inspect.getsource(view)))
    found = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and NAME.match(node.value)
    }
    words = ["zzz", *sorted(found | dispatch_keys(view))]
    return [
        dict(zip(args, combo, strict=True))
        for combo in product(words, repeat=len(args))
    ]


def _guard(view):
    def guarded(request, kind):
        return view(request, kind)

    return guarded


def test_a_view_wrapped_inside_a_function_is_read():
    """#49's import views returned a wrapper defined inside a decorator,
    whose source is indented: `ast.parse` raised IndentationError."""
    view = _guard(lambda request, kind: None)
    assert {"kind": "zzz"} in words_for("<str:kind>/", view)


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


@contextmanager
def counted_writes():
    """The database writes the requests inside make, the session's left out."""
    writes = []

    def count(execute, sql, params, many, context):
        if WRITE.match(sql) and "django_session" not in sql:
            writes.append(sql)
        return execute(sql, params, many, context)

    with connections["default"].execute_wrapper(count):
        yield writes


OK_TOAST = b'data-toast="ok"'
# route -> what the view changes instead of a stored row.
CHANGES_ELSEWHERE = {
    "dashboard/replay/test/": "it reads no parameter and sends replay.test to"
    " the replay app",
}


@pytest.mark.parametrize(("route", "view"), ROUTES, ids=[r for r, _ in ROUTES])
def test_a_junk_post_that_changes_nothing_claims_no_success(
    coordinator, race, route, view
):
    if route in CHANGES_ELSEWHERE:
        return
    names = module_names(view)
    for word in words_for(route, view):
        url = fill(route, race, word)
        for junk in JUNK:
            with counted_writes() as writes:
                response = coordinator.post(
                    url, dict.fromkeys(names, junk), HTTP_HX_REQUEST="true"
                )
            claims = response.status_code < 300 and OK_TOAST in response.content
            assert not (claims and not writes), (url, junk, response.content[:300])
