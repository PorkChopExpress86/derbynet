"""A browser test that runs at one size says so with
`@pytest.mark.sizes(...)`, never with a `pytest.skip` on `page.size` in its
body or a helper it calls (#366).

A skip in the body runs after the `page` fixture has opened a context and
loaded a page it never uses. #341's hand-offs failed on such a phone case of
`test_replay.py`, with `net::ERR_NETWORK_CHANGED` on a request the skipped
page sent at teardown. The browser conftest's `split_by_size` deselects the
other size at collection instead.

The check reads every module under `ui/tests`, so a helper such as
`desktop_only(page)` in a module a browser test imports is read too. A skip
(`pytest.skip(...)`, `raise pytest.skip.Exception(...)`, or a call of a
function whose body skips outright) counts when any condition it sits under
reads a size, even when that condition also reads another name or test
parameter (#458). The condition is an `if`, a `while`, an `x if c else y`,
a `match` subject or case guard, or the operands before it in an `and` or
`or`. A size read is `<x>.size`, `<x>.viewport_size`, `<x>.is_mobile`, the
`size` fixture, or a name the function binds from an expression that reads
a size (`s = page.size`, `phone = page.size == "phone"`).

What it reads past, left to review:
- a helper that takes the size as an argument and compares its own
  parameter (`only(page.size, "desktop")`);
- a skip under a further condition in a helper called under a size
  condition, as the helper may skip on something else;
- `pytest.skip` under another name (`from pytest import skip as s`), and a
  size read through `getattr` or JavaScript.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

SIZE_READS = {"size", "viewport_size", "is_mobile"}
SKIPS = {"pytest.skip", "skip", "pytest.skip.Exception", "skip.Exception"}


def modules(root: Path) -> dict[str, str]:
    """The source of each module under `root`, by its path there."""
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
        if "vendor" not in path.relative_to(root).parts
    }


def is_size_read(node: ast.AST, held: set[str]) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr in SIZE_READS
    return isinstance(node, ast.Name) and (node.id in SIZE_READS or node.id in held)


def reads_size(nodes: list[ast.AST], held: set[str]) -> bool:
    """True when any node in `nodes` reads a size."""
    return any(is_size_read(n, held) for node in nodes for n in ast.walk(node))


def held_names(function: ast.AST) -> set[str]:
    """Names `function` binds from an expression that reads a size, as
    `s = page.size`, read in the order they are bound."""
    held: set[str] = set()
    binds = sorted(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            and node.value is not None
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in binds:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if reads_size([node.value], held):
            held |= {t.id for t in targets if isinstance(t, ast.Name)}
    return held


def is_skip(node: ast.AST, calls: set[str]) -> bool:
    """A skip call, a `raise` of the skip exception class, or a call of a
    name in `calls`."""
    if isinstance(node, ast.Raise):
        return node.exc is not None and ast.unparse(node.exc) in SKIPS
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return ast.unparse(func) in SKIPS or name in calls


def skippers(trees: list[ast.Module]) -> set[str]:
    """Functions with a skip among their top-level statements, so a call of
    one is a skip."""

    def skips(statement):
        inner = (
            statement.value
            if isinstance(statement, ast.Expr)
            else getattr(statement, "exc", None)
        )
        return is_skip(statement, set()) or is_skip(inner, set())

    return {
        node.name
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(skips(statement) for statement in node.body)
    }


def size_skips(source: str, calls: set[str] = frozenset()) -> list[tuple[int, str]]:
    """`(line, function)` of each skip under a condition that reads a size;
    a call of a name in `calls` is a skip."""
    tree = ast.parse(source)
    found = []

    def visit(node, function, held, guarded):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function, held, guarded = node.name, held_names(node), False
        if guarded and is_skip(node, calls):
            found.append((node.lineno, function))
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            visit(node.test, function, held, guarded)
            inner = guarded or reads_size([node.test], held)
            branches = (
                [node.body, node.orelse]
                if isinstance(node, ast.IfExp)
                else node.body + node.orelse
            )
            for child in branches:
                visit(child, function, held, inner)
            return
        if isinstance(node, ast.Match):
            visit(node.subject, function, held, guarded)
            for case in node.cases:
                tests = [node.subject] + ([case.guard] if case.guard else [])
                inner = guarded or reads_size(tests, held)
                for child in case.body:
                    visit(child, function, held, inner)
            return
        if isinstance(node, ast.BoolOp):
            for i, value in enumerate(node.values):
                inner = guarded or reads_size(node.values[:i], held)
                visit(value, function, held, inner)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, function, held, guarded)

    visit(tree, "<module>", set(), False)
    return found


def tree_skips(root: Path) -> list[tuple[str, int, str]]:
    """`(path, line, function)` of each size skip in the modules under
    `root`."""
    sources = modules(root)
    calls = skippers([ast.parse(source) for source in sources.values()])
    return [
        (path, line, function)
        for path, source in sources.items()
        for line, function in size_skips(source, calls)
    ]


def test_a_skip_on_the_size_is_found():
    source = """
def desktop_only(page):
    if page.size != "desktop":
        pytest.skip("the replay station is a laptop")


def test_inline(gallery):
    if gallery.size == "phone":
        pytest.skip("a phone has no hover")


def test_the_fixture(size):
    if size == "phone":
        x = 1
    else:
        pytest.skip("the phone's")


def test_nested(page):
    if page.size == "phone":
        if page.url:
            pytest.skip("still the size")


def test_expression(page):
    pytest.skip("x") if page.size == "phone" else None


def test_bare_import(page):
    if page.size != "phone":
        skip("x")


def test_and_a_parameter(page, cancel):
    if cancel == "pointercancel" and page.size != "phone":
        pytest.skip("depends on cancel too")


def test_only_a_parameter(page, cancel):
    if cancel == "pointercancel":
        pytest.skip("not the size")


def test_another_condition(page):
    if page.url:
        pytest.skip("not the size")


def test_size_without_a_skip(page):
    if page.size == "phone":
        page.goto("/")


def test_a_skip_after_the_condition(page):
    if page.size == "phone":
        page.goto("/")
    pytest.skip("not under it")


def test_a_mark(page):
    pytest.mark.skip("not a call of skip")


def test_no_name_at_all(page):
    if True:
        pytest.skip("not the size")
"""
    assert size_skips(source) == [
        (4, "desktop_only"),
        (9, "test_inline"),
        (16, "test_the_fixture"),
        (22, "test_nested"),
        (26, "test_expression"),
        (31, "test_bare_import"),
        (36, "test_and_a_parameter"),
    ]


def test_each_way_of_writing_a_size_skip_is_found():
    source = """
DESKTOP = "desktop"
from ui.tests.browser.conftest import SIZES
from ui.tests.browser.conftest import SIZES as WIDTHS
from pytest import skip


def test_a_held_size(page):
    s = page.size
    if s == "phone":
        pytest.skip("x")


def test_a_held_comparison(page):
    phone = page.size == "phone"
    if phone:
        pytest.skip("x")


def test_a_walrus(page):
    if (s := page.size) == "phone":
        pytest.skip("x")


def test_a_module_constant(page):
    if page.size != DESKTOP:
        pytest.skip("x")


def test_an_imported_constant(page):
    if page.size != SIZES[1]:
        pytest.skip("x")


def test_a_match(page):
    match page.size:
        case "phone":
            pytest.skip("x")
        case _:
            page.goto("/")


def test_and(page):
    page.size == "phone" and pytest.skip("x")


def test_or(page):
    page.size == "desktop" or pytest.skip("x")


def test_the_exception(page):
    if page.size == "phone":
        raise pytest.skip.Exception("x")


def test_the_exception_class(page):
    if page.size == "phone":
        raise pytest.skip.Exception


def test_the_viewport(page):
    if page.viewport_size["width"] < 500:
        pytest.skip("x")


def test_is_mobile(page):
    if page.context.is_mobile:
        pytest.skip("x")


def test_a_skipping_helper(page):
    if page.size == "phone":
        not_on_a_phone()


def test_a_constant_imported_under_another_name(page):
    if page.size != WIDTHS[1]:
        pytest.skip("x")


def test_the_imported_exception(page):
    if page.size == "phone":
        raise skip.Exception("x")


def test_a_held_size_beside_an_annotation(page):
    note: str
    held: str = page.size
    s = held
    if s == "phone":
        pytest.skip("x")


def test_a_helper_through_a_module(page):
    if page.size == "phone":
        helpers.not_on_a_phone()


def test_a_match_under_a_size(page):
    if page.size == "phone":
        match page.url:
            case "/":
                pytest.skip("x")


def test_an_and_under_a_size(page):
    if page.size == "phone":
        page.url and pytest.skip("x")


def test_a_size_held_in_a_block(page):
    with page.expect_popup():
        s = page.size
    t = s
    if t == "phone":
        pytest.skip("x")
"""
    assert size_skips(source, {"not_on_a_phone"}) == [
        (11, "test_a_held_size"),
        (17, "test_a_held_comparison"),
        (22, "test_a_walrus"),
        (27, "test_a_module_constant"),
        (32, "test_an_imported_constant"),
        (38, "test_a_match"),
        (44, "test_and"),
        (48, "test_or"),
        (53, "test_the_exception"),
        (58, "test_the_exception_class"),
        (63, "test_the_viewport"),
        (68, "test_is_mobile"),
        (73, "test_a_skipping_helper"),
        (78, "test_a_constant_imported_under_another_name"),
        (83, "test_the_imported_exception"),
        (91, "test_a_held_size_beside_an_annotation"),
        (96, "test_a_helper_through_a_module"),
        (103, "test_a_match_under_a_size"),
        (108, "test_an_and_under_a_size"),
        (116, "test_a_size_held_in_a_block"),
    ]


def test_a_size_beside_another_name_is_found():
    source = """
from ui.tests.race import want


def test_a_lower_case_import(page):
    if page.size != want:
        pytest.skip("x")


def test_a_held_parameter(page, cancel):
    s = cancel
    if s == "x" and page.size == "phone":
        pytest.skip("x")


def test_a_held_size_with_a_parameter(page, cancel):
    held = cancel and page.size == "phone"
    if held:
        pytest.skip("x")


def test_a_match_with_a_guard(page, cancel):
    match page.size:
        case "phone" if cancel:
            pytest.skip("x")


def test_a_match_guard_with_a_size(page, cancel):
    match cancel:
        case "pointercancel" if page.size == "phone":
            pytest.skip("x")


def test_and_before_the_size(page, cancel):
    cancel and page.size == "phone" and pytest.skip("x")


def test_a_local_name(page):
    want = page.url
    if page.size != want:
        pytest.skip("x")


def test_a_viewport_beside_a_box(page, box):
    if page.viewport_size["width"] < box["x"]:
        pytest.skip("x")
"""
    assert size_skips(source, {"not_on_a_phone"}) == [
        (7, "test_a_lower_case_import"),
        (13, "test_a_held_parameter"),
        (19, "test_a_held_size_with_a_parameter"),
        (25, "test_a_match_with_a_guard"),
        (31, "test_a_match_guard_with_a_size"),
        (35, "test_and_before_the_size"),
        (41, "test_a_local_name"),
        (46, "test_a_viewport_beside_a_box"),
    ]


def test_skips_not_on_a_size_are_allowed():
    source = """
def test_a_parameter_only_skip(cancel):
    if cancel == "pointercancel":
        pytest.skip("x")


def test_a_held_parameter_only(cancel):
    s = cancel
    if s == "pointercancel":
        pytest.skip("x")


def test_a_helper_that_skips_on_something_else(page):
    if page.size == "phone":
        open_kiosk(page)


def test_a_raise_of_something_else(page):
    try:
        page.goto("/")
    except ValueError:
        if page.size == "phone":
            raise
        if page.size == "desktop":
            raise ValueError
        if page.size == "desktop":
            raise ValueError("x")


def test_a_match_without_size(cancel):
    match cancel:
        case "pointercancel":
            pytest.skip("x")


def test_and_without_size(page, cancel):
    cancel and page.url and pytest.skip("x")
"""
    assert size_skips(source, {"not_on_a_phone"}) == []


def test_a_function_that_skips_outright_is_a_skipper():
    tree = ast.parse(
        """
def not_on_a_phone():
    pytest.skip("x")


def raises():
    raise pytest.skip.Exception("x")


def raises_the_class():
    raise pytest.skip.Exception


def open_kiosk(page):
    if page.url:
        pytest.skip("not outright")
"""
    )
    assert skippers([tree]) == {"not_on_a_phone", "raises", "raises_the_class"}


def test_a_helper_beside_the_browser_tests_is_read(tmp_path):
    """A helper in a module a browser test imports, outside browser/, is
    read, and so is a call, under a size condition, of a helper in another
    module that skips outright. A vendored module is not read."""
    (tmp_path / "browser" / "vendor").mkdir(parents=True)
    (tmp_path / "size_helper.py").write_text(
        'def desktop_only(page):\n    if page.size != "desktop":\n'
        '        pytest.skip("x")\n\n\ndef never():\n    pytest.skip("x")\n'
    )
    (tmp_path / "browser" / "test_a.py").write_text(
        "from ui.tests.size_helper import desktop_only, never\n\n\n"
        'def test_a(page):\n    desktop_only(page)\n    if page.size == "phone":\n'
        "        never()\n"
    )
    (tmp_path / "browser" / "vendor" / "lib.py").write_text(
        'if page.size:\n    pytest.skip("x")\n'
    )
    assert tree_skips(tmp_path) == [
        ("browser/test_a.py", 7, "test_a"),
        ("size_helper.py", 3, "desktop_only"),
    ]


def test_no_browser_test_skips_on_the_size_in_its_body():
    skips = tree_skips(TESTS)
    assert skips == [], (
        "mark these @pytest.mark.sizes(..., reason=...), which deselects the "
        f"other size at collection (#366): {skips}"
    )
