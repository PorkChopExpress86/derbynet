"""A browser test closes a second tab with `close_tab(page, tab)` (#53 retro).

`tab.close()` with a poll's request out leaves Playwright reporting no end
for it. The camera tab's POST failed PR #205's ui (postgres) job that way,
1 run in many, with "still in flight after 10 s". `close_tab()` stops the
tab's timers and waits for its requests first, and the harness names any
request a tab closed on. This finds the plain close before a run does.
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"


def tab_names(tree: ast.AST) -> set[str]:
    """Names bound to a page a test opened: `x = ....new_page()`, also inside
    a conditional, and `with ....expect_popup() as x` (its `x.value`)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "new_page"
            for call in ast.walk(node.value)
        ):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            expr = node.context_expr
            if isinstance(expr, ast.Call) and ast.unparse(expr.func).endswith(
                "expect_popup"
            ):
                names.add(f"{node.optional_vars.id}.value")
    return names


def plain_closes(source: str) -> list[str]:
    tree = ast.parse(source)
    names = tab_names(tree)
    return [
        f"{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and ast.unparse(node.func.value) in names
    ]


def test_a_plain_close_of_a_new_tab_is_found():
    source = """
tab = page if not tabs else page.context.new_page()
tab.close()
with page.expect_popup() as popup:
    pass
popup.value.close()
other = browser.new_context()
other.close()
"""
    assert plain_closes(source) == ["3: tab.close()", "6: popup.value.close()"]


def test_every_browser_test_closes_a_tab_with_close_tab():
    found = {
        path.name: closes
        for path in sorted(BROWSER.glob("test_*.py"))
        # test_harness.py closes a tab plainly to test what the harness does.
        if path.name != "test_harness.py"
        and (closes := plain_closes(path.read_text(encoding="utf-8")))
    }
    assert found == {}, f"close these with close_tab(page, tab): {found}"
