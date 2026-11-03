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

It also flags a row the view reads before a change and answers after it, in
a toast or an Undo link, when the service never got it (#47 retro): the
reply then names what the view read outside the lock. Answer from what the
service returns instead. Against #47's code before its review it flags
`assign`. `ANSWERED_READS` lists the reads left in on purpose.
"""

import ast
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2]
VIEWS = sorted(SERVER.glob("ui/*/views.py")) + sorted(
    SERVER.glob("legacy_api/handlers/*.py")
)
SERVICE_MODULES = ("services", "settings_form", "race_settings")
# Service modules that live outside a `services` package, as `package.module`.
SERVICE_PATHS = ("media.photos",)
# file:view:name -> why the view may answer with a row it read first.
ANSWERED_READS = {
    "ui/playlist/views.py:step:row": "the toast names the step's round, which"
    " no edit of the step changes",
}
# Helpers of `ui.responses` that read a stored row.
READ_HELPERS = ("event_racer",)


def _service_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            from_service = any(
                part in SERVICE_MODULES for part in node.module.split(".")
            )
            for alias in node.names:
                if (
                    from_service
                    or alias.name in SERVICE_MODULES
                    or f"{node.module}.{alias.name}" in SERVICE_PATHS
                ):
                    names.add(alias.asname or alias.name)
    return names


def _reads_stored_state(call: ast.Call, helpers: frozenset[str] = frozenset()) -> bool:
    func = ast.unparse(call.func)
    return (
        func == "build"
        or func.endswith("state.build")
        or ".objects." in func
        or func.startswith("get_object_or_404")
        or func in READ_HELPERS
        or func in helpers
    )


def _read_helpers(tree: ast.Module) -> frozenset[str]:
    """The module's own functions that read a stored row, such as a
    `_racer_or_none(request, posted)` that filters `Racer.objects`."""
    return frozenset(
        f.name
        for f in tree.body
        if isinstance(f, ast.FunctionDef)
        and f.name.startswith("_")
        and any(_reads_stored_state(c) for c in _calls(f))
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

    helpers = _read_helpers(tree)

    def reads(call: ast.Call) -> bool:
        return _reads_stored_state(call, helpers)

    found = []
    for view in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        service_calls = [c for c in _calls(view) if is_service(c)]
        service_lines = [c.lineno for c in service_calls]
        if not service_lines:
            continue
        tainted: set[str] = set()
        # Where each tainted name was first assigned.
        read_at: dict[str, int] = {}
        # The tainted names each one was computed from, itself included.
        derived: dict[str, set[str]] = {}
        statements = sorted(
            (n for n in ast.walk(view) if isinstance(n, ast.Assign | ast.AnnAssign)),
            key=lambda n: n.lineno,
        )
        for assign in statements:
            if assign.value is None:
                continue
            value = assign.value
            # A service's own result was read under its lock.
            if isinstance(value, ast.Call) and is_service(value):
                continue
            if any(reads(c) for c in _calls(value)) or (_names(value) & tainted):
                targets = (
                    assign.targets
                    if isinstance(assign, ast.Assign)
                    else [assign.target]
                )
                sources = set().union(
                    *(derived.get(n, {n}) for n in _names(value) & tainted)
                )
                for target in targets:
                    for name in _names(target):
                        tainted.add(name)
                        read_at.setdefault(name, assign.lineno)
                        derived.setdefault(name, {name} | sources)
        found += _read_into_reply(view, filename, read_at, derived, service_calls)
        # A branch that ends in a return, such as a GET that renders a
        # sheet, never reaches a write after it: only service calls later in
        # the same branch count.
        returning = [b for b in _blocks(view) if isinstance(b[-1], ast.Return)]
        for test in [n for n in ast.walk(view) if isinstance(n, ast.If)]:
            refuses = _names(test.test) & tainted or any(
                reads(c) for c in _calls(test.test)
            )
            later = {line for line in service_lines if line > test.lineno}
            for block in returning:
                inside = [n for s in block for n in ast.walk(s)]
                if test in inside:
                    later &= {c.lineno for c in inside if isinstance(c, ast.Call)}
            if not (refuses and later and _returns(test.body + test.orelse)):
                continue
            found.append(
                f"{filename}:{test.lineno} {view.name}: if {ast.unparse(test.test)}"
            )
    return found


def _read_into_reply(view, filename, read_at, derived, service_calls) -> list[str]:
    """A row read before the service call and used after it, in the toast or
    an Undo link, while the service never saw it: the reply then describes
    what the view read outside the lock, not what the service changed. #47's
    assign named a racer the view looked up itself in its toast. A row the
    view hands to the service, and a value computed from one (a round's
    name), is the service's to check, so it is not flagged; neither is a
    value used only before the call."""
    # A change is a service call in a `try` that catches its refusal; a
    # read such as `results.heat_times()` has none.
    tried = {
        id(c)
        for t in ast.walk(view)
        if isinstance(t, ast.Try)
        for s in t.body
        for c in _calls(s)
    }
    changes = [c for c in service_calls if id(c) in tried]
    if not changes:
        return []
    first = min(c.lineno for c in changes)
    last = max(c.end_lineno for c in changes)
    handed = set().union(*(_names(c) for c in changes))
    # A row whose value went to the service, as a racer whose stored photo
    # name the view passes, was handed too.
    handed |= set().union(*(derived[n] for n in handed if n in derived))
    # After a `try` whose `else` returns, only a refusal runs on.
    ends = [
        t.end_lineno
        for t in ast.walk(view)
        if isinstance(t, ast.Try)
        and t.orelse
        and isinstance(t.orelse[-1], ast.Return)
        and any(id(c) in tried for s in t.body for c in _calls(s))
    ]
    last_success = min(ends) if ends else float("inf")
    # A refusal's reply (an `except` body) re-renders what the view read,
    # and says nothing about a change.
    refusing = {
        id(n)
        for h in ast.walk(view)
        if isinstance(h, ast.ExceptHandler)
        for n in ast.walk(h)
    }
    used_after = {
        n.id
        for n in ast.walk(view)
        if isinstance(n, ast.Name)
        and isinstance(n.ctx, ast.Load)
        and last < n.lineno <= last_success
        and id(n) not in refusing
    }
    return [
        f"{filename}:{line} {view.name}: {name} is read before the service"
        " and answered after it"
        for name, line in sorted(read_at.items(), key=lambda item: item[1])
        if line < first
        and name in used_after
        and not derived[name] & handed
        and f"{filename}:{view.name}:{name}" not in ANSWERED_READS
    ]


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


READ_INTO_REPLY = """
from media import photos

def assign(request, repo):
    racer = _racer_or_none(request, data.get("racer", ""))
    try:
        photos.assign_photo(event, repository, data.get("racer", ""), name)
    except photos.Refused as failure:
        return refused(request, failure)
    return toasted(request, f"Assigned to {name_of(racer)}.")

def _racer_or_none(request, posted):
    return Racer.objects.filter(pk=posted).first()
"""

ANSWERED_FROM_SERVICE = """
from media import photos

def assign(request, repo):
    try:
        done = photos.assign_photo(event, repository, data.get("racer", ""), name)
    except photos.Refused as failure:
        return refused(request, failure)
    return toasted(request, f"Assigned to {name_of(done.racer)}.")
"""

HANDED_ROW = """
from racing.services import finals

def finalist(request, round_id, racer_id):
    racer = event_racer(request, racer_id)
    name = racer_name(racer.first_name, racer.last_name)
    try:
        finals.add_to_final(racer, round)
    except FinalsFailure as failure:
        return refused(request, failure)
    return updated(request, f"{name} added.")
"""


def test_a_row_read_before_a_change_and_answered_after_it_is_flagged():
    assert len(offenders(READ_INTO_REPLY)) == 1


@pytest.mark.parametrize(
    "source", [ANSWERED_FROM_SERVICE, HANDED_ROW], ids=["service-result", "handed"]
)
def test_a_reply_from_the_service_or_a_handed_row_passes(source):
    assert offenders(source) == []
