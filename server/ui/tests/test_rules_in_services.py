"""A `ui` view or page state leaves each rule to the one service that owns it
(#44 retro). The #42, #43 and #44 reviews each found a rule copied into a
view or a page's state; #44's copies were the lane mask, the runs per lane
range and the scheduling method map. Two copies are mechanical enough to
lint in `ui/*/views.py` and `ui/*/state.py`:

- a bit shift: lane masks go through `racing.services.lanes.mask_of` and
  `lanes_of`;
- an ordering comparison (`<`, `<=`, `>`, `>=`) with an upper-case constant
  imported from a service module, such as `MAX_RUNS_PER_LANE`, or from the
  racing engine, such as D42's `DID_NOT_FINISH`: the service checks its own
  limit, and the view catches its failure or reads the service's answer
  (#49's review found the DNF rule copied into the standings state);
- a `range()`, `min()` or `max()` with a literal bound other than 0 or 1,
  such as `min(max(count, 2), 8)` (#50's review found the fake timer's lane
  limits clamped this way in a view, and its retro found the dashboard's
  `range(1, 7)` copy of `MAX_RUNS_PER_LANE`).

The same limits copied into a template are linted in `ui/templates/`: an
`<input>`'s literal `max=`, or a `min=` other than 0 or 1, and the same
arguments to `{% include ... with %}`, such as a stepper's `max=6` (#50's
review found the timer pages' lane bounds there). The view passes the
service's constant instead.

A copied lookup table such as the method map has no fixed shape; the review
still looks for it.
"""

import ast
import re
from pathlib import Path

import pytest

from ui.tests.test_read_then_act import _service_names

SERVER = Path(__file__).resolve().parents[2]
FILES = sorted(SERVER.glob("ui/*/views.py")) + sorted(SERVER.glob("ui/*/state.py"))
ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
BOUND_CALLS = frozenset({"range", "min", "max"})
TRIVIAL_BOUNDS = frozenset({0, 1})
# The components gallery shows each component with sample values.
TEMPLATES = sorted(
    p
    for p in (SERVER / "ui" / "templates").rglob("*.html")
    if p.name != "components_gallery.html"
)
INPUT = re.compile(r"<input\b[^>]*>")
INPUT_BOUND = re.compile(r"\b(?P<attr>min|max)=[\"'](?P<n>-?\d+)[\"']")
INCLUDE_WITH = re.compile(r"{%\s*include\b[^%]*?\bwith\b(?P<args>[^%]*)%}")
INCLUDE_BOUND = re.compile(r"(?<![\w-])(?P<attr>min|max)=(?P<n>-?\d+)(?![\w.])")


def offenders(source: str, filename: str = "views.py") -> list[str]:
    tree = ast.parse(source)
    limits = {
        name for name in _service_names(tree) | _engine_names(tree) if name.isupper()
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.LShift | ast.RShift):
            found.append(
                f"{filename}:{node.lineno} bit shift {ast.unparse(node)}: "
                "use racing.services.lanes"
            )
        if isinstance(node, ast.Compare) and any(
            isinstance(op, ORDERING) for op in node.ops
        ):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            for name in sorted(names & limits):
                found.append(
                    f"{filename}:{node.lineno} {ast.unparse(node)}: "
                    f"the service checks {name}"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in BOUND_CALLS
            and any(_literal_bound(arg) for arg in node.args)
        ):
            found.append(
                f"{filename}:{node.lineno} {ast.unparse(node)}: "
                "a literal limit; use the service's constant"
            )
    return found


def _literal_bound(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and type(node.value) is int
        and node.value not in TRIVIAL_BOUNDS
    )


def template_offenders(source: str, filename: str = "page.html") -> list[str]:
    spans = [(m.start(), m.group(), INPUT_BOUND) for m in INPUT.finditer(source)]
    spans += [
        (m.start(), m["args"], INCLUDE_BOUND) for m in INCLUDE_WITH.finditer(source)
    ]
    found = []
    for start, text, pattern in spans:
        for bound in pattern.finditer(text):
            if bound["attr"] == "max" or int(bound["n"]) not in TRIVIAL_BOUNDS:
                line = source.count("\n", 0, start) + 1
                found.append(
                    f"{filename}:{line} {bound.group()}: a literal limit; "
                    "the view passes the service's constant"
                )
    return found


def _engine_names(tree: ast.Module) -> set[str]:
    return {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("racing.engine")
        for alias in node.names
    }


@pytest.mark.parametrize("path", FILES, ids=[str(p.relative_to(SERVER)) for p in FILES])
def test_views_and_state_leave_rules_to_the_service(path):
    assert offenders(path.read_text(), str(path.relative_to(SERVER))) == []


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=[str(p.relative_to(SERVER)) for p in TEMPLATES]
)
def test_templates_take_limits_from_the_view(path):
    assert template_offenders(path.read_text(), str(path.relative_to(SERVER))) == []


LANE_MASK = """
def lanes_sheet(request):
    open_lanes = [n for n in lanes if not unused & (1 << (n - 1))]
"""

SERVICE_LIMIT = """
from racing.services.playlist import MAX_RUNS_PER_LANE

def _choice(post):
    runs = _int(post.get("runs", ""))
    if runs is None or not 1 <= runs <= MAX_RUNS_PER_LANE:
        raise ValueError("Runs per lane is 1 to 6.")
"""

SERVICE_CONSTANT_SHOWN = """
from racing.services.playlist import MAX_RUNS_PER_LANE, NEXT_ROUND

RUNS_CHOICES = tuple(range(1, MAX_RUNS_PER_LANE + 1))

def _then(then):
    return "next" if then == NEXT_ROUND else "stop"
"""

ENGINE_LIMIT = """
from racing.engine.results import DID_NOT_FINISH

def _dnf(time):
    return time is not None and time >= DID_NOT_FINISH
"""

LITERAL_CLAMP = """
def fake_lanes(request):
    count = min(max(_int(request.POST.get("lanes")), 2), 8)
"""

LITERAL_RANGE = """
RUNS_PER_LANE = range(1, 7)
"""

TRIVIAL_BOUNDS_USED = """
def log(request):
    read = timer.read_log(_event(request), max(seek, 0))
    rows = range(1, max(count, 1) + 1)
"""

LOCAL_PARSE_BOUND = """
MAX_LANES = 31

def scheduling_lanes(request):
    if count is None or not 0 <= count <= MAX_LANES:
        return error()
"""


@pytest.mark.parametrize(
    "source",
    [LANE_MASK, SERVICE_LIMIT, ENGINE_LIMIT, LITERAL_CLAMP, LITERAL_RANGE],
    ids=["lane-mask", "service-limit", "engine-limit", "clamp", "range"],
)
def test_a_copied_rule_is_flagged(source):
    assert offenders(source) != []


@pytest.mark.parametrize(
    "source",
    [SERVICE_CONSTANT_SHOWN, LOCAL_PARSE_BOUND, TRIVIAL_BOUNDS_USED],
    ids=["constant-shown", "local-bound", "trivial-bounds"],
)
def test_a_view_that_shows_a_constant_passes(source):
    assert offenders(source) == []


@pytest.mark.parametrize(
    "source",
    [
        '<input type="number" name="lanes" min="2" max="{{ lanes_max }}">',
        '<input type="number"\n  max="20" name="n-lanes">',
        '{% include "ui/components/stepper.html" with id="r" value=1 min=1 max=6 %}',
        '{% include "ui/components/stepper.html" with id="l" min=2 max=lanes_max %}',
    ],
    ids=["input-min", "input-max-multiline", "stepper-max", "stepper-min"],
)
def test_a_literal_limit_in_a_template_is_flagged(source):
    assert len(template_offenders(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        '<input type="number" min="1" max="{{ lanes_max }}">',
        '<input type="text" maxlength="100">',
        '<progress max="100" :value="item.progress"></progress>',
        '{% include "ui/components/stepper.html" with min=0 max=tree.max_trophies %}',
        '{% include "ui/components/stepper.html" with min=lanes_min max=lanes_max %}',
    ],
    ids=["input-from-view", "maxlength", "progress", "stepper-zero", "stepper-vars"],
)
def test_a_limit_from_the_view_passes(source):
    assert template_offenders(source) == []
