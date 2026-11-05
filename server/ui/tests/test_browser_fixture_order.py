"""A browser test or fixture that changes `settings` takes it before the page
(#52 retro).

pytest tears fixtures down in the reverse of the order it set them up, and
sets them up in the order a function lists them. The component gallery took
`gallery(page, event, settings)`, so `settings` turned the test endpoints off
while the page was still open, and its 2 s poll got a 404 at teardown (seen
on Postgres in #52's CI). The harness drains the requests in flight when the
test returns, but a poll the page sends after that drain still meets the
restored settings. `settings` listed before every fixture that opens a page
is put back only once the page has closed.
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"


def functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            yield node


def is_fixture(node: ast.FunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if ast.unparse(target) in {"pytest.fixture", "fixture"}:
            return True
    return False


def params(node: ast.FunctionDef) -> list[str]:
    return [a.arg for a in node.args.args]


def page_fixtures(trees: list[ast.AST]) -> set[str]:
    """`page` and every fixture that takes it, directly or through another."""
    taking = {
        node.name: set(params(node))
        for tree in trees
        for node in functions(tree)
        if is_fixture(node)
    }
    found = {"page"}
    while grown := {name for name, ps in taking.items() if ps & found} - found:
        found |= grown
    return found


def offenders(sources: dict[str, str]) -> list[str]:
    trees = {name: ast.parse(text) for name, text in sources.items()}
    pages = page_fixtures(list(trees.values()))
    found = []
    for name, tree in trees.items():
        for node in functions(tree):
            names = params(node)
            if "settings" not in names:
                continue
            before = names[: names.index("settings")]
            if late := [p for p in before if p in pages]:
                found.append(f"{name}:{node.lineno} {node.name}: settings after {late}")
    return found


def browser_sources() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(BROWSER.glob("*.py"))}


def test_every_browser_function_takes_settings_before_the_page():
    assert offenders(browser_sources()) == []


def test_settings_after_a_page_fixture_is_found():
    conftest = browser_sources()["conftest.py"]
    late = (
        "import pytest\n"
        "@pytest.fixture\n"
        "def gallery(page, event, settings):\n"
        "    settings.ENABLE_TEST_ENDPOINTS = True\n"
        "def test_it(login, settings):\n"
        "    pass\n"
        "def test_fine(settings, gallery):\n"
        "    pass\n"
    )
    found = offenders({"conftest.py": conftest, "test_late.py": late})
    assert found == [
        "test_late.py:3 gallery: settings after ['page']",
        "test_late.py:5 test_it: settings after ['login']",
    ]
