"""A browser test that runs at one size says so with
`@pytest.mark.sizes(...)`, never with a `pytest.skip` on `page.size` in its
body (#366).

A skip in the body runs after the `page` fixture has opened a context and
loaded a page it never uses. #341's two hand-offs each failed on such a
phone case of `test_replay.py`, with `net::ERR_NETWORK_CHANGED` on a request
the skipped page sent at teardown, and each rerun cost about 9 min. The
browser conftest's `split_by_size` deselects the other size at collection
instead.

A skip counts when a condition it sits under reads a size (`<x>.size`, or
the `size` fixture) and no other name, as in `desktop_only(page)`'s
`if page.size != "desktop"`. A condition that also reads another name, such
as a test's own parameter, depends on more than the size and is left alone.
What it reads past, left to review: a size held in a name first
(`s = page.size`), a size compared with a module constant (read as
depending on that constant), and a skip in a helper called under a size
condition rather than written under it.
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# (file name, `line: function`) of each skip left in place. test_kiosks.py
# keeps its `phone_only` while #369 rewrites that file; remove the entry
# when its skip moves onto the mark, and add none.
KNOWN = {("test_kiosks.py", "79: phone_only")}


def size_reads(node: ast.AST) -> list[ast.AST]:
    return [
        n
        for n in ast.walk(node)
        if (isinstance(n, ast.Attribute) and n.attr == "size")
        or (isinstance(n, ast.Name) and n.id == "size")
    ]


def on_size_alone(test: ast.AST) -> bool:
    """True when `test` reads a size and every name in it is inside one."""
    reads = size_reads(test)
    inside = {id(n) for read in reads for n in ast.walk(read)}
    names = [n for n in ast.walk(test) if isinstance(n, ast.Name)]
    return bool(reads) and all(id(n) in inside for n in names)


def is_skip(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and ast.unparse(node.func) in (
        "pytest.skip",
        "skip",
    )


def size_skips(source: str) -> list[str]:
    """`line: function` of each `pytest.skip` under a condition on the size
    alone."""
    found = []

    def visit(node, function, guarded):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function, guarded = node.name, False
        if is_skip(node) and guarded:
            found.append(f"{node.lineno}: {function}")
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            visit(node.test, function, guarded)
            inner = guarded or on_size_alone(node.test)
            branches = (
                [node.body, node.orelse]
                if isinstance(node, ast.IfExp)
                else node.body + node.orelse
            )
            for child in branches:
                visit(child, function, inner)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, function, guarded)

    visit(ast.parse(source), "<module>", False)
    return found


def test_a_skip_on_the_size_alone_is_found():
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
        "4: desktop_only",
        "9: test_inline",
        "16: test_the_fixture",
        "22: test_nested",
        "26: test_expression",
        "31: test_bare_import",
    ]


def test_no_browser_test_skips_on_the_size_in_its_body():
    found = {
        (path.name, skip)
        for path in sorted(BROWSER.glob("*.py"))
        for skip in size_skips(path.read_text(encoding="utf-8"))
    }
    assert found - KNOWN == set(), (
        "mark these @pytest.mark.sizes(..., reason=...), which deselects the "
        f"other size at collection (#366): {sorted(found - KNOWN)}"
    )
    assert KNOWN - found == set(), "no longer found; take it out of KNOWN"
