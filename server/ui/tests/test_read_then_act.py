"""A `ui` view or a legacy handler leaves every rule about stored state to
the service it calls (#43 retro). The reviews of #42 and #43 found views that
read the database, refused on what they read, and then called a service that
trusted them: the read happens outside `lock_race_state`, so a change between
the read and the write slips past the rule (`plan/AGENT-BRIEF.md`).

The check is a lint over each `ui/*/views.py` and `legacy_api/handlers/*.py`.
It flags an `if` that tests something read from the database (`state.build`,
`build`, `.objects.`, `get_object_or_404`, or a name assigned from one) and
returns, ahead of a service call the branch can still reach. To clear it,
move the rule into the service, under the lock, and catch its failure in the
view. A check of the posted text alone (a blank field, a number that is not
one) reads nothing stored and is not flagged. Against the code before the
reviews' fixes it flags five of #43's six views and #42's `racer_new`.
"""

import ast
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2]
VIEWS = sorted(SERVER.glob("ui/*/views.py")) + sorted(
    SERVER.glob("legacy_api/handlers/*.py")
)
SERVICE_MODULES = ("services", "settings_form", "race_settings")


def _service_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            from_service = any(
                part in SERVICE_MODULES for part in node.module.split(".")
            )
            for alias in node.names:
                if from_service or alias.name in SERVICE_MODULES:
                    names.add(alias.asname or alias.name)
    return names


def _reads_stored_state(call: ast.Call) -> bool:
    func = ast.unparse(call.func)
    return (
        func == "build"
        or func.endswith("state.build")
        or ".objects." in func
        or func.startswith("get_object_or_404")
    )


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _returns(statements: list[ast.stmt]) -> bool:
    return any(isinstance(n, ast.Return) for s in statements for n in ast.walk(s))


def _blocks(node: ast.AST):
    """Each statement list in the function, with the statements in it."""
    for child in ast.walk(node):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(child, field, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                yield block


def offenders(source: str, filename: str = "views.py") -> list[str]:
    tree = ast.parse(source)
    services = _service_names(tree)

    def is_service(call: ast.Call) -> bool:
        root = call.func
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and root.id in services

    found = []
    for view in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        service_lines = [c.lineno for c in _calls(view) if is_service(c)]
        if not service_lines:
            continue
        tainted: set[str] = set()
        statements = sorted(
            (n for n in ast.walk(view) if isinstance(n, ast.Assign | ast.AnnAssign)),
            key=lambda n: n.lineno,
        )
        for assign in statements:
            if assign.value is None:
                continue
            value = assign.value
            if any(_reads_stored_state(c) for c in _calls(value)) or (
                _names(value) & tainted
            ):
                targets = (
                    assign.targets
                    if isinstance(assign, ast.Assign)
                    else [assign.target]
                )
                for target in targets:
                    tainted |= _names(target)
        # A branch that ends in a return, such as a GET that renders a
        # sheet, never reaches a write after it: only service calls later in
        # the same branch count.
        returning = [b for b in _blocks(view) if isinstance(b[-1], ast.Return)]
        for test in [n for n in ast.walk(view) if isinstance(n, ast.If)]:
            reads = _names(test.test) & tainted or any(
                _reads_stored_state(c) for c in _calls(test.test)
            )
            later = {line for line in service_lines if line > test.lineno}
            for block in returning:
                inside = [n for s in block for n in ast.walk(s)]
                if test in inside:
                    later &= {c.lineno for c in inside if isinstance(c, ast.Call)}
            if not (reads and later and _returns(test.body + test.orelse)):
                continue
            found.append(
                f"{filename}:{test.lineno} {view.name}: if {ast.unparse(test.test)}"
            )
    return found


@pytest.mark.parametrize("path", VIEWS, ids=[str(p.relative_to(SERVER)) for p in VIEWS])
def test_views_leave_stored_state_rules_to_the_service(path):
    assert offenders(path.read_text(), str(path.relative_to(SERVER))) == []


READ_THEN_ACT = """
from roster.models import Subgroup
from roster.services import groups

def order_subgroups(request):
    rows = {s.pk: s for s in Subgroup.objects.filter(pk__in=posted)}
    ordered = [rows[pk] for pk in posted if pk in rows]
    if not ordered or len({s.group_id for s in ordered}) != 1:
        return service_error(request, "That order names no subgroups of one group.")
    groups.order_subgroups(event, ordered)
"""

ELSE_BRANCH = """
from core import race_settings
from ui.groups import state

def calc_rule(request):
    tree = state.build(event)
    if value in dict(tree.calc_choices):
        pass
    else:
        return service_error(request, "Pick how to calculate the standings.")
    race_settings.write(event, "full-field-calc", value)
"""

POSTED_TEXT_ONLY = """
from racing.services import lanes

def lanes_close(request):
    chosen = [int(n) for n in request.POST.getlist("lane") if n.isdigit()]
    if not chosen:
        return service_error(request, "Choose the lane to close.")
    lanes.close_lanes(event, chosen, scoring)
"""

GET_SHEET_THEN_POST = """
from roster.services import groups
from ui.groups import state

def rule(request):
    if request.method == "GET":
        tree = state.build(event)
        if tree.rule == chosen:
            return render(request, "closed.html")
        return render(request, "sheet.html")
    groups.form_groups_by_rule(event, chosen)
"""

SERVICE_REFUSES = """
from roster.services import groups

def subgroup_move(request):
    try:
        groups.move_subgroup_to(event, subgroup_id, group_id)
    except GroupFailure as failure:
        return refused(request, failure)
"""


@pytest.mark.parametrize("source", [READ_THEN_ACT, ELSE_BRANCH], ids=["if", "else"])
def test_a_refusal_on_stored_state_before_a_service_is_flagged(source):
    assert len(offenders(source)) == 1


@pytest.mark.parametrize(
    "source",
    [POSTED_TEXT_ONLY, GET_SHEET_THEN_POST, SERVICE_REFUSES],
    ids=["posted-text", "get-branch", "service-refuses"],
)
def test_a_view_that_leaves_the_rule_to_the_service_passes(source):
    assert offenders(source) == []
