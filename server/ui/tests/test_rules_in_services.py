"""A `ui` view or page state leaves each rule to the one service that owns it
(#44 retro). The #42, #43 and #44 reviews each found a rule copied into a
view or a page's state; #44's copies were the lane mask, the runs per lane
range and the scheduling method map. Two copies are mechanical enough to
lint in `ui/*/views.py` and `ui/*/state.py`:

- a bit shift: lane masks go through `racing.services.lanes.mask_of` and
  `lanes_of`;
- an ordering comparison (`<`, `<=`, `>`, `>=`) with an upper-case constant
  imported from a service module, such as `MAX_RUNS_PER_LANE`: the service
  checks its own limit, and the view catches its failure.

A copied lookup table such as the method map has no fixed shape; the review
still looks for it.
"""

import ast
from pathlib import Path

import pytest

from ui.tests.test_read_then_act import _service_names

SERVER = Path(__file__).resolve().parents[2]
FILES = sorted(SERVER.glob("ui/*/views.py")) + sorted(SERVER.glob("ui/*/state.py"))
ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def offenders(source: str, filename: str = "views.py") -> list[str]:
    tree = ast.parse(source)
    limits = {name for name in _service_names(tree) if name.isupper()}
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
    return found


@pytest.mark.parametrize("path", FILES, ids=[str(p.relative_to(SERVER)) for p in FILES])
def test_views_and_state_leave_rules_to_the_service(path):
    assert offenders(path.read_text(), str(path.relative_to(SERVER))) == []


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

LOCAL_PARSE_BOUND = """
MAX_LANES = 31

def scheduling_lanes(request):
    if count is None or not 0 <= count <= MAX_LANES:
        return error()
"""


@pytest.mark.parametrize(
    "source", [LANE_MASK, SERVICE_LIMIT], ids=["lane-mask", "service-limit"]
)
def test_a_copied_rule_is_flagged(source):
    assert len(offenders(source)) == 1


@pytest.mark.parametrize(
    "source",
    [SERVICE_CONSTANT_SHOWN, LOCAL_PARSE_BOUND],
    ids=["constant-shown", "local-bound"],
)
def test_a_view_that_shows_a_constant_passes(source):
    assert offenders(source) == []
